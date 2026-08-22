# -*- coding: utf-8 -*-
"""【価格取得】楽天市場商品検索APIで、JANごとの実売価格と商品直リンクを取る。

  export RAKUTEN_APPLICATION_ID=... RAKUTEN_ACCESS_KEY=... RAKUTEN_AFFILIATE_ID=...
  python3 scripts/rakuten_prices.py --dry-run       # 何件叩くかだけ見る
  python3 scripts/rakuten_prices.py --limit 20      # 先に20件で試す
  python3 scripts/rakuten_prices.py --only-scope    # 掲載対象だけ（1601件・約30分）
  python3 scripts/rakuten_prices.py --by-title      # JANの無い版をタイトルで引く
  python3 scripts/rakuten_prices.py --apply         # APIを叩かず offers に入れ直すだけ

`offers` が「検索ページへのリンク」なのに対し、こちらは
**その商品ページへの直リンクと価格**を持つ。作るテーブルは2つ。

  rakuten_items      … JANごとにヒットした楽天市場の商品
  rakuten_fetch_log  … いつどのJANを引いたか（叩き直しを避けるため）

取り込んだあと apply_to_offers() が offers の該当行を UPDATE する。
埋まるのは2チャネルだけ。

  楽天（新品）        … JAN検索でいちばん安い購入可能な新品
  駿河屋（中古）      … 同じ結果に駿河屋楽天市場店が居ればその価格。
                        駿河屋本体には公式APIが無く、規約上安全に
                        中古価格を取れる経路がここしかない

Amazon・アニメイト・メルカリは価格を取る手段が無いので検索リンクのまま。

楽天ウェブサービスの規約で、価格・在庫の保存は取得から24時間まで、
表示するなら最低でも週1回は更新することになっている。
だから「取り込んだら終わり」ではなく、定期的に回し直す前提のスクリプト。
"""
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import affiliate_config as AF
import rakuten_api
from common import DATA
from editions import PLATFORM_KW

DB = os.path.join(DATA, "vndb_otome.db")

MISS_TTL = 7 * 24 * 3600     # 0件だったJANは1週間置く（明日いきなり現れはしない）
HIT_TTL = 24 * 3600          # 価格を持っているものは24時間で取り直す（規約上の上限）

# 家庭用機を先に処理する。中断しても価値の高いところから埋まるように。
PLATFORM_RANK = {
    "swi": 0, "ps5": 1, "ps4": 2, "psv": 3, "n3d": 4, "ps3": 5,
    "nds": 6, "psp": 7, "wii": 8, "ps2": 9, "ps1": 10,
    "gba": 11, "gbc": 12, "sfc": 13, "sat": 14, "drc": 15,
}
PC_RANK = 90                 # win / mac は最後（新品流通がほぼない）

SCHEMA = """
CREATE TABLE IF NOT EXISTS rakuten_items (
    jan           TEXT,
    item_code     TEXT,
    item_name     TEXT,
    price         INTEGER,
    shop_name     TEXT,
    shop_code     TEXT,
    availability  INTEGER,   -- 1=購入可能
    condition     TEXT,      -- 新品 / 中古
    item_url      TEXT,
    affiliate_url TEXT,
    image_url     TEXT,
    point_rate    INTEGER,
    review_count  INTEGER,
    review_average REAL,
    fetched_at    TEXT,
    PRIMARY KEY (jan, item_code)
);
CREATE INDEX IF NOT EXISTS idx_ri_jan  ON rakuten_items(jan);
CREATE INDEX IF NOT EXISTS idx_ri_cond ON rakuten_items(condition);

CREATE TABLE IF NOT EXISTS rakuten_fetch_log (
    jan        TEXT PRIMARY KEY,
    fetched_at TEXT,
    hits       INTEGER
);

-- JANが無い版はタイトルで引く。VNDBは新作・近作にJANがまだ入っていないことが多く、
-- 2026年発売の作品は82%がJAN未登録だった（＝画像も価格も出せなかった）
CREATE TABLE IF NOT EXISTS rakuten_title_items (
    eid           TEXT,
    item_code     TEXT,
    item_name     TEXT,
    price         INTEGER,
    shop_name     TEXT,
    shop_code     TEXT,
    availability  INTEGER,
    condition     TEXT,
    item_url      TEXT,
    affiliate_url TEXT,
    image_url     TEXT,
    point_rate    INTEGER,
    review_count  INTEGER,
    review_average REAL,
    fetched_at    TEXT,
    PRIMARY KEY (eid, item_code)
);
CREATE INDEX IF NOT EXISTS idx_rti_eid ON rakuten_title_items(eid);

CREATE TABLE IF NOT EXISTS rakuten_title_log (
    eid TEXT PRIMARY KEY, fetched_at TEXT, hits INTEGER
);
"""

# タイトル検索はグッズを大量に拾う。ゲーム本体だけ残す。
# （「マツリカの炯」で引くと缶バッジ・ブロマイドが最安で並ぶ）
GOODS_WORDS = ("バッジ", "ブロマイド", "キャラカード", "アクリル", "アクスタ", "ラバスト",
               "クリアファイル", "ポスター", "ステッカー", "タペストリー", "マグカップ",
               "ぬいぐるみ", "キーホルダー", "コースター", "缶バ", "色紙", "同人",
               "攻略本", "設定資料", "画集", "サウンドトラック", "サントラ", "ドラマCD",
               "抱き枕", "Tシャツ", "トレーディング", "パスケース", "スタンド")
SOFT_WORDS = ("Switch", "スイッチ", "ソフト", "PS4", "PS5", "PSVita", "Vita",
              "PlayStation", "プレイステーション", "HAC-P", "3DS", "PSP")


# 「その商品自体がゲームではない」と読める書き方。
#
# GOODS_WORDS をそのまま JAN 検索に使ってはいけない。ゲーム本体の商品名にも
# 「限定版:ドラマCD/特製手ぬぐい同梱」のように特典の説明で同じ語が出るので、
# 正しいリンクを大量に落とす。弾いてよいのは商品そのものがグッズや書籍のとき。
#
#   駿河屋は 【中古】<カテゴリ> で始まる。ソフト以外のカテゴリなら本体ではない
#   楽天ブックスのCDは ＜通常盤＞（CD＋DVD−ROM）／（ドラマCD） の形
#   特典CDだけの出品は「※CDのみ」と書かれる
NOT_THE_GAME = re.compile(
    r"※?CDのみ"
    r"|【中古】(?:ノート・メモ帳|アニメムック|ライトノベル|販促品|画集|設定資料集)"
    r"|／（ドラマCD）")


def not_the_game(name):
    return bool(NOT_THE_GAME.search(name or ""))


def looks_like_game(name):
    """商品名がゲーム本体か。グッズ・書籍・CDを落とす"""
    n = name or ""
    if any(w in n for w in GOODS_WORDS):
        return False
    return any(w in n for w in SOFT_WORDS)

USED_WORDS = ("中古", "USED", "used", "ユーズド")


def platform_rank(code):
    """家庭用機ほど小さい数を返す。中断しても価値の高いところから埋まるように"""
    if code in PLATFORM_RANK:
        return PLATFORM_RANK[code]
    return PC_RANK if code in ("win", "mac", "lin") else 99


def classify(item):
    """新品か中古か。店舗コードと商品名から判定する。"""
    if (item.get("shopCode") or "") in AF.RAKUTEN_USED_SHOPCODES:
        return "中古"
    name = item.get("itemName") or ""
    return "中古" if any(w in name for w in USED_WORDS) else "新品"


def image_of(item):
    """mediumImageUrls は formatVersion によって文字列配列 / dict配列 で返る"""
    for key in ("mediumImageUrls", "smallImageUrls"):
        urls = item.get(key) or []
        for u in urls:
            if isinstance(u, dict):
                u = u.get("imageUrl")
            if u:
                return u.split("?")[0]      # ?_ex=128x128 のリサイズ指定を落とす
    return None


def targets(con, only_home=False, only_scope=False):
    """引くべきJANを、価値の高い順に並べて返す。

    版（editions）を単位に見る。同じJANが複数の版に付くことがあるので
    JANでまとめ、いちばん優先度の高い機種の順位を代表にする。
    """
    where = "WHERE gtin <> ''"
    if only_scope:
        # 掲載対象（家庭用機で出ている作品）に絞る。1601件で約30分
        cond = " OR ".join("platforms LIKE '%%%s%%'" % k
                           for k in ("Switch", "PS", "ニンテンドー", "Xbox"))
        where += " AND vid IN (SELECT vid FROM games WHERE %s)" % cond
    rows = con.execute("""
        SELECT gtin, platform, MAX(released), COUNT(DISTINCT vid)
        FROM editions %s GROUP BY gtin, platform
    """ % where).fetchall()

    best = {}
    for gtin, platform, released, n_vid in rows:
        rank = platform_rank(platform)
        if only_home and rank >= PC_RANK:
            continue
        cur = best.get(gtin)
        if cur is None or rank < cur[0]:
            best[gtin] = (rank, released or "", gtin, platform, n_vid)
    out = list(best.values())
    # 機種の優先度 → 新しい順
    out.sort(key=lambda r: (r[0], -int((r[1][:4] or "0"))))
    return out


def title_targets(con, only_scope=True):
    """タイトルで引く価値のある版を返す。

    家庭用機のパッケージ版だけ。DL版とPC・スマホは店で買えないか、
    タイトル検索の当たりが悪すぎる。

    対象は2種類。
      ・JANが無い版（VNDBは新作にJANが入っていないことが多い）
      ・JANはあるが、そのJANでは使える商品画像が1枚も返らなかった版
        （例: 花笑む彼と & bloom は通常版のJANが0件、特装版もブックオフの
          「画像準備中」だけだった。タイトルで引くと写真つきの出品が見つかる）
    """
    no_image = """NOT EXISTS (SELECT 1 FROM rakuten_items r WHERE r.jan = e.gtin
                    AND r.image_url IS NOT NULL
                    AND r.image_url NOT LIKE '%noimg%'
                    AND r.image_url NOT LIKE '%noimage%'
                    AND r.image_url NOT LIKE '%no_image%')"""
    where = ["e.is_dl = 0", "e.plat_group = 0",
             "(e.gtin = '' OR %s)" % no_image]
    if only_scope:
        cond = " OR ".join("platforms LIKE '%%%s%%'" % k
                           for k in ("Switch", "PS", "ニンテンドー", "Xbox"))
        where.append("e.vid IN (SELECT vid FROM games WHERE %s)" % cond)
    rows = con.execute("""
        SELECT e.eid, e.search_kw, e.platform, e.edition, e.edition_kind, e.released
        FROM editions e WHERE %s ORDER BY e.released DESC""" % " AND ".join(where)).fetchall()
    return rows


# 楽天の keyword は記号や長すぎる文字列を弾く（HTTP 400 wrong_parameter）。
# 「~」「♪」「☆」「＆」などを含む作品名がそのままだと通らない
BAD_KW_CHARS = re.compile(r"[~〜～♪☆★＆&＋+|｜/／\\<>＜＞\"'`^*＊%％#＃@＠!！?？:：;；,，.。、]")


def title_keyword(row):
    parts = [row[1], PLATFORM_KW.get(row[2], "")]
    if row[4] in ("限定", "セット"):
        parts.append(row[3])
    kw = " ".join(p for p in parts if p)
    kw = BAD_KW_CHARS.sub(" ", kw)
    # 楽天の keyword は「1文字の語」と「6語以上」を弾く（実地確認済み）。
    #   「剣が君 for S Switch」 … 単独の "S" で 400
    #   「ワンド オブ フォーチュン 2 時空に沈む黙示録 PSP」 … 6語で 400
    plat = PLATFORM_KW.get(row[2], "")
    words = [w for w in re.split(r"[\s　]+", kw) if len(w) >= 2]
    if plat and plat in words:
        words = [w for w in words if w != plat]
        words = words[:4] + [plat]
    else:
        words = words[:5]
    return " ".join(words)


def fetch_by_title(con, todo, refresh=False):
    """JANの無い版をタイトルで引く。グッズを落としてから保存する"""
    n_item = n_hit = n_drop = 0
    for i, row in enumerate(todo, 1):
        eid = row[0]
        kw = title_keyword(row)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            data = rakuten_api.ichiba_search(keyword=kw, hits=30, use_cache=not refresh)
        except rakuten_api.RakutenError as e:
            print("  [%d/%d] %s 失敗: %s" % (i, len(todo), kw, e))
            continue
        rows = []
        for it in rakuten_api.items_of(data):
            code = it.get("itemCode")
            if not code or not it.get("itemPrice"):
                continue
            if not_the_game(it.get("itemName")) or not looks_like_game(it.get("itemName")):
                n_drop += 1
                continue
            rows.append((
                eid, code, it.get("itemName"), int(it["itemPrice"]),
                it.get("shopName"), it.get("shopCode"),
                int(it.get("availability") or 0), classify(it),
                it.get("itemUrl"), it.get("affiliateUrl") or it.get("itemUrl"),
                image_of(it), it.get("pointRate"),
                it.get("reviewCount"), it.get("reviewAverage"), stamp))
        con.execute("DELETE FROM rakuten_title_items WHERE eid = ?", (eid,))
        con.executemany(
            "INSERT OR REPLACE INTO rakuten_title_items VALUES (%s)" % ",".join("?" * 15), rows)
        con.execute("INSERT OR REPLACE INTO rakuten_title_log VALUES (?,?,?)",
                    (eid, stamp, len(rows)))
        con.commit()
        n_item += len(rows)
        if rows:
            n_hit += 1
        if i % 25 == 0 or i == len(todo):
            print("  [%d/%d] %s → %d件（累計 商品%d / ヒット%d / グッズ除外%d）"
                  % (i, len(todo), kw[:26], len(rows), n_item, n_hit, n_drop))
    return n_hit


def apply_to_offers(con):
    """取り込んだ楽天の商品を offers に流し込む。

    価格・在庫は楽天ウェブサービスの規約で24時間しか保持できないので、
    site_build.py 側でも fetched_at を見て古いものは表示しない。
    """
    n_new = n_used = n_used_rk = 0
    items = {}
    for r in con.execute("SELECT * FROM rakuten_items"):
        items.setdefault(r[0], []).append(r)
    # JANの無い版はタイトル検索の結果を使う。列の並びは同じにしてある
    for r in con.execute("SELECT * FROM rakuten_title_items"):
        items.setdefault(r[0], []).append(r)
    cols = [d[1] for d in con.execute("PRAGMA table_info(rakuten_items)")]
    F = {n: i for i, n in enumerate(cols)}

    # JANで引いた商品とタイトルで引いた商品の両方を見る。
    # JANがあっても出品が無い・写真が無いことがあり、その穴をタイトル検索が埋める
    eds = con.execute("SELECT eid, gtin FROM editions").fetchall()
    for eid, jan in eds:
        got = list(items.get(jan) or []) + list(items.get(eid) or [])
        buyable = [i for i in got if i[F["availability"]] and i[F["price"]]]

        # 楽天（新品）… いちばん安い購入可能な新品
        new = sorted([i for i in buyable if i[F["condition"]] == "新品"],
                     key=lambda i: i[F["price"]])
        if new:
            i = new[0]
            con.execute("""UPDATE offers SET link_type='item', item_code=?, item_name=?,
                           image_url=?, price=?, availability='在庫あり', fetched_at=?,
                           url=? WHERE eid=? AND channel='楽天' AND condition='新品'""",
                        (i[F["item_code"]], i[F["item_name"]], i[F["image_url"]],
                         i[F["price"]], i[F["fetched_at"]],
                         i[F["affiliate_url"]] or i[F["item_url"]], eid))
            n_new += con.total_changes and 1 or 0

        # 楽天（中古）… 駿河屋楽天市場店を除いた最安の中古。
        # 駿河屋は専用の行で出すので、同じ商品が2行に並ばないよう外す
        used = sorted([i for i in buyable
                       if i[F["condition"]] == "中古"
                       and i[F["shop_code"]] != AF.SURUGAYA_RAKUTEN_SHOPCODE],
                      key=lambda i: i[F["price"]])
        if used:
            i = used[0]
            con.execute("""UPDATE offers SET link_type='item', item_code=?, item_name=?,
                           image_url=?, price=?, availability='在庫あり', fetched_at=?,
                           url=? WHERE eid=? AND channel='楽天' AND via='rakuten_used'""",
                        (i[F["item_code"]], i[F["item_name"]], i[F["image_url"]],
                         i[F["price"]], i[F["fetched_at"]],
                         i[F["affiliate_url"]] or i[F["item_url"]], eid))
            n_used_rk += 1

        # 駿河屋（中古）… 同じ結果に駿河屋楽天市場店が居ればその価格を使う
        sg = sorted([i for i in buyable
                     if i[F["shop_code"]] == AF.SURUGAYA_RAKUTEN_SHOPCODE],
                    key=lambda i: i[F["price"]])
        if sg:
            i = sg[0]
            con.execute("""UPDATE offers SET link_type='item', item_code=?, item_name=?,
                           image_url=?, price=?, availability='在庫あり', fetched_at=?,
                           url=? WHERE eid=? AND channel='駿河屋' AND via='rakuten_shop'""",
                        (i[F["item_code"]], i[F["item_name"]], i[F["image_url"]],
                         i[F["price"]], i[F["fetched_at"]],
                         i[F["affiliate_url"]] or i[F["item_url"]], eid))
            n_used += 1
    con.commit()
    print("offers を更新: 楽天（新品）%d行 / 楽天（中古）%d行 / 駿河屋楽天市場店（中古）%d行"
          % (n_new, n_used_rk, n_used))


def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    refresh = "--refresh" in argv
    only_home = "--only-home" in argv
    only_scope = "--only-scope" in argv       # 掲載対象（家庭用機の作品）だけ
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None

    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)

    if "--by-title" in argv:
        try:
            rakuten_api.credentials()
        except rakuten_api.MissingCredentials as e:
            print(e); con.close(); return 1
        todo = title_targets(con, only_scope=True)
        done = {r[0] for r in con.execute("SELECT eid FROM rakuten_title_log")} \
            if not refresh else set()
        todo = [t for t in todo if t[0] not in done]
        if limit:
            todo = todo[:limit]
        print("JANの無い版 %d件をタイトルで引きます（約%d分）"
              % (len(todo), max(1, round(len(todo) * rakuten_api.DELAY / 60))))
        if dry:
            for t in todo[:20]:
                print("   %-46s %s" % (title_keyword(t)[:46], t[5] or "-"))
            con.close(); return 0
        fetch_by_title(con, todo, refresh=refresh)
        print()
        apply_to_offers(con)
        con.close()
        return 0

    if "--apply" in argv:
        # 取得済みのデータを offers に入れ直すだけ（APIは叩かない）
        apply_to_offers(con)
        report(con)
        con.close()
        return 0

    log = {j: (t, h) for j, t, h in
           con.execute("SELECT jan, fetched_at, hits FROM rakuten_fetch_log")}
    now = time.time()

    def is_fresh(jan):
        if refresh or jan not in log:
            return False
        fetched_at, hits = log[jan]
        try:
            age = now - time.mktime(time.strptime(fetched_at, "%Y-%m-%d %H:%M:%S"))
        except (ValueError, TypeError):
            return False
        return age < (HIT_TTL if hits else MISS_TTL)

    allj = targets(con, only_home=only_home, only_scope=only_scope)
    todo = [t for t in allj if not is_fresh(t[2])]
    if limit:
        todo = todo[:limit]

    print("対象JAN %d件（全 %d件 / うち期限内で省略 %d件）"
          % (len(todo), len(allj), len(allj) - len([t for t in allj if not is_fresh(t[2])])))
    print("所要見込み: 約 %d分（1秒1リクエスト）" % max(1, round(len(todo) * rakuten_api.DELAY / 60)))

    if dry:
        print()
        print("--dry-run のため問い合わせません。先頭20件:")
        for rank, rel, jan, plat, n in todo[:20]:
            print("  %-14s %-10s %s (%d作品)" % (jan, plat, rel or "-", n))
        con.close()
        return 0

    try:
        rakuten_api.credentials()
    except rakuten_api.MissingCredentials as e:
        print()
        print(e)
        con.close()
        return 1

    n_item = n_hit = n_miss = n_err = 0
    for i, (rank, rel, jan, plat, n_vid) in enumerate(todo, 1):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            data = rakuten_api.ichiba_search(keyword=jan, hits=30, use_cache=not refresh)
        except rakuten_api.RakutenError as e:
            print("  [%d/%d] JAN %s 失敗: %s" % (i, len(todo), jan, e))
            n_err += 1
            continue

        items = rakuten_api.items_of(data)
        # JAN検索なので基本は同一商品だが、まれに関係ない商品が混じる。
        # 価格が取れて購入可能なものだけ残す。
        rows = []
        for it in items:
            code = it.get("itemCode")
            if not code or not it.get("itemPrice"):
                continue
            # JANがグッズや特典CDのものを指していることがある
            # （金色のコルダ3 → 同梱特典ドラマCD ※CDのみ）
            if not_the_game(it.get("itemName")):
                continue
            rows.append((
                jan, code, it.get("itemName"), int(it["itemPrice"]),
                it.get("shopName"), it.get("shopCode"),
                int(it.get("availability") or 0), classify(it),
                it.get("itemUrl"), it.get("affiliateUrl") or it.get("itemUrl"),
                image_of(it), it.get("pointRate"),
                it.get("reviewCount"), it.get("reviewAverage"), stamp,
            ))

        con.execute("DELETE FROM rakuten_items WHERE jan = ?", (jan,))
        con.executemany(
            "INSERT OR REPLACE INTO rakuten_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.execute("INSERT OR REPLACE INTO rakuten_fetch_log VALUES (?,?,?)",
                    (jan, stamp, len(rows)))
        con.commit()

        n_item += len(rows)
        if rows:
            n_hit += 1
        else:
            n_miss += 1
        if i % 25 == 0 or i == len(todo):
            print("  [%d/%d] JAN %s → %d件（累計 商品%d / ヒット%d / 0件%d / 失敗%d）"
                  % (i, len(todo), jan, len(rows), n_item, n_hit, n_miss, n_err))

    print()
    apply_to_offers(con)
    print()
    report(con)
    con.close()
    return 0


def report(con):
    q = lambda s: con.execute(s).fetchone()[0]
    print("=== rakuten_items ===")
    print("  商品 %d件 / JAN %d件" % (q("SELECT COUNT(*) FROM rakuten_items"),
                                      q("SELECT COUNT(DISTINCT jan) FROM rakuten_items")))
    for cond, c, j in con.execute(
            "SELECT condition, COUNT(*), COUNT(DISTINCT jan) FROM rakuten_items "
            "GROUP BY condition ORDER BY 2 DESC"):
        print("  %-4s %6d件 / %4dJAN" % (cond, c, j))
    n_vid = q("""SELECT COUNT(DISTINCT e.vid) FROM editions e
                 JOIN rakuten_items r ON r.jan = e.gtin""")
    print()
    print("  価格を取れた作品: %d件" % n_vid)
    if not AF.RAKUTEN_AFFILIATE_ID:
        print()
        print("※ RAKUTEN_AFFILIATE_ID が未設定です。affiliate_url には素のURLが入っています。")
        print("  IDを設定して --refresh で取り直せば、アフィリエイトURLに置き換わります。")


if __name__ == "__main__":
    sys.exit(main())
