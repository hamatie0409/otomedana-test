# -*- coding: utf-8 -*-
"""【フェーズ1】VNDBのリリースを「版（エディション）」の一覧に組み直す。

  python3 scripts/buy_links.py    # 先に gtins / vndb_links を作っておく
  python3 scripts/editions.py

1作品＝1商品ではないので、購入導線はリリース単位で持つ。
掲載対象637作品のうち469作品（74%）が版を2つ以上持っているため、
「機種 × 版」で出し分けられる形が既定でないと足りない。

VNDBのリリースには機種・発売日・JANが揃っているので、
版名（通常版／限定版／DL版…）だけリリース名から取り出せば表が組める。
作品名とリリース名は一致しないことがある点に注意。
  作品名 「薄桜鬼 新選組奇譚」 / Switch版のリリース名 「薄桜鬼 真改 風華伝」
作品名で検索リンクを作ると当たらないので、rel_title を必ず持たせる。
"""
import os, re, sqlite3
from collections import defaultdict
from common import DATA, ROOT
from overrides import FORCE_VNDB_IMAGE, MANUAL_GTIN
from vndb_build import PLATFORM_JA, vndb_date

DB_DUMP = os.path.join(ROOT, "vndb", "db")

# 機種の大分類。表示はこの順に並べる（家庭用機 → PC → スマホ → その他）
HOME_PLAT = {"swi", "sw2", "ps1", "ps2", "ps3", "ps4", "ps5", "psp", "psv",
             "nds", "n3d", "gba", "gbc", "nes", "sfc", "sat", "drc", "pce",
             "xbo", "xxs", "xb3", "bdp", "dvd"}
PC_PLAT = {"win", "mac", "lin", "dos", "p88", "p98", "x68", "fmt", "fm7", "msx", "x1s"}
MOBILE_PLAT = {"and", "ios", "mob", "web"}

# 検索窓に打つときの機種名。正式名称だと店の商品名と一致しない
# （「Nintendo Switch」より「Switch」のほうが引ける）
PLATFORM_KW = {
    "swi": "Switch", "sw2": "Switch2", "psv": "Vita", "psp": "PSP",
    "ps1": "PS", "ps2": "PS2", "ps3": "PS3", "ps4": "PS4", "ps5": "PS5",
    "nds": "DS", "n3d": "3DS", "gba": "GBA", "gbc": "GBC",
    "xbo": "XboxOne", "xxs": "XboxSeries", "xb3": "Xbox360",
    "win": "PC", "mac": "Mac", "sat": "セガサターン", "drc": "ドリームキャスト",
    "pce": "PCエンジン", "sfc": "スーパーファミコン",
}

# 版の判定。DL版は他の版と同時に現れる（「all in one ダウンロード版」など）ので、
# 先に切り離してから通常/限定/セット/廉価を見る。
# edition には実際に使われている語を残す（「初回限定版」と「豪華版」は別物なので丸めない）。
DL_PATTERN = r"(?:フリー)?(?:ダウンロード版|ダウンロード|DL版)"
EDITION_PATTERNS = [
    ("限定", 2, r"(?:初回限定生産版|初回生産限定版|初回限定版|初回限定|限定版|豪華版|特装版"
               r"|コレクターズ(?:エディション|パッケージ|版|BOX)?|プレミアム(?:BOX|エディション|版)"
               r"|デラックス版|[^\s　]*BOX)"),
    ("セット", 3, r"(?:ツインパック|ダブルパック|トリプルパック|[^\s　]*スペシャルパック"
                r"|[^\s　]*セット|コンプリート[^\s　]*|オールインワン|all in one)"),
    ("廉価", 4, r"(?:[^\s　]*ザ・ベスト|ベスト版|Best版|the Best|廉価版|お買い得版"
               r"|[^\s　]*ゲームベスト)"),
    ("通常", 1, r"(?:通常版|通定版|パッケージ版)"),
]

# VNDBに実在する表記ゆれ・誤記を表示用に直す（DBには元の語も残す）
EDITION_FIX = {"通定版": "通常版"}

# DL版のときに案内する配信ストア。機種の分類ごとに優先順を変える
STORE_PREF = {
    "home": ["nintendo_jp"],
    "pc": ["steam", "dlsite", "digiket", "booth", "melonjp", "toranoana", "getchu"],
    "mobile": ["appstore", "googplay"],
    "other": ["steam", "dlsite"],
}
STORE_JA = {"nintendo_jp": "ニンテンドーストア", "steam": "Steam", "dlsite": "DLsite",
            "digiket": "デジケット", "booth": "BOOTH", "melonjp": "メロンブックス",
            "toranoana": "とらのあな", "getchu": "Getchu", "appstore": "App Store",
            "googplay": "Google Play", "playasia": "Play-Asia"}

# 全角の英数字を半角に寄せる。「ＤＬ版」のような表記が実在し、
# そのままだと版の判定に当たらない
ZEN2HAN = {ord(c): ord(c) - 0xFEE0 for c in
           "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
           "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９"}

SCHEMA = """
DROP TABLE IF EXISTS editions;
CREATE TABLE editions (
    eid TEXT PRIMARY KEY,   -- v1715-r56825-swi
    vid TEXT REFERENCES games(vid),
    rid TEXT,
    platform TEXT,          -- swi
    platform_ja TEXT,       -- Nintendo Switch
    plat_group INTEGER,     -- 0=家庭用機 1=PC 2=スマホ 3=その他
    rel_title TEXT,         -- 版名を除いたリリース名
    search_kw TEXT,         -- 店の検索窓に打つ語。原則 rel_title、当たらない場合だけ作品名
    edition TEXT,           -- 実際に使われている語（初回限定版 など）。無ければ ''
    edition_label TEXT,     -- 表示用に整えた版名（誤記を直し、DL版を併記）
    edition_kind TEXT,      -- 通常/限定/セット/廉価。DLは is_dl で別に持つ
    edition_rank INTEGER,   -- 版の表示順
    released TEXT,          -- ISO。年だけ・年月だけもある
    gtin TEXT,              -- JAN。無ければ ''
    asin TEXT,              -- amazon_asin.py が埋める。PA-APIが通るまでは空
    is_dl INTEGER,          -- ダウンロード版。版名かメディア（ネット配信のみ）で判定
    store_site TEXT,        -- DL版の配信ストア（nintendo_jp など）
    store_url TEXT,
    n_vn INTEGER,           -- そのリリースが収録する作品数。2以上なら合本
    plat_sort TEXT          -- 機種グループの並べ替えキー（新しい機種が上）
);
CREATE INDEX idx_ed_vid ON editions(vid);
CREATE INDEX idx_ed_gtin ON editions(gtin);
CREATE INDEX idx_ed_rid ON editions(rid);

-- 表紙を VNDB の画像で出してよい作品（site_build.py / site_data.py が見る）。
--   dl     … パッケージ版が1つも無い。箱が存在せず楽天から画像を取りようがない
--   manual … overrides.py で人が指定したもの
-- 定義を1か所に置くために、ビューではなく実体のテーブルとして持つ
DROP TABLE IF EXISTS vndb_image_ok;
CREATE TABLE vndb_image_ok (vid TEXT PRIMARY KEY, reason TEXT);
"""


def unesc(v):
    if v == "\\N":
        return None
    if "\\" in v:
        v = (v.replace("\\t", "\t").replace("\\n", "\n")
              .replace("\\r", "\r").replace("\\\\", "\\"))
    return v


def read(table):
    path = os.path.join(DB_DUMP, table)
    cols = open(path + ".header", encoding="utf-8").read().rstrip("\n").split("\t")
    with open(path, encoding="utf-8") as f:
        for line in f:
            yield dict(zip(cols, (unesc(v) for v in line.rstrip("\n").split("\t"))))


def tidy(s):
    """版名を抜いた跡を整える。空になった括弧と余った区切りを落とす"""
    s = re.sub(r"[（(【「\[]\s*[）)】」\]]", "", s)
    s = re.sub(r"[\s　]+", " ", s)
    return s.strip(" 　-－―‐~〜/・,、")


def parse_edition(title):
    """リリース名 -> (版名を除いた名前, 版名, 表示用の版名, 種別, 並び順, DL版か)

    「大正×対称アリス ダウンロード版 all in one」のように版名は重なるので、
    DL版だけ先に切り離してから残りを判定する。
    """
    t = title
    dl = bool(re.search(DL_PATTERN, t))
    if dl:
        t = tidy(re.sub(DL_PATTERN, "", t, count=1)) or title

    for kind, rank, pat in EDITION_PATTERNS:
        m = re.search(pat, t)
        if not m:
            continue
        word = m.group(0)
        base = tidy(t[:m.start()] + t[m.end():]) or tidy(t)
        label = EDITION_FIX.get(word, word)
        if dl:
            label = "%s（ダウンロード版）" % label
        return (base, word, label, kind, rank, dl)

    return (tidy(t), "", "ダウンロード版" if dl else "", "", 0, dl)


def bigrams(s):
    s = re.sub(r"[\s　]", "", s or "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def search_keyword(rel_title, vn_title):
    """店の検索窓に打つ語。

    原則はリリース名（作品名とは違うことがある。
    作品名「薄桜鬼 新選組奇譚」/ Switch版「薄桜鬼 真改 風華伝」）。
    ただし本体同梱パックのようにリリース名が作品名と無関係なことがあるので
    （「PlayStation®Vita オトメイトスペシャルパック」）、
    2文字でも重ならなければ作品名に切り替える。
    """
    if rel_title and vn_title and not (bigrams(rel_title) & bigrams(vn_title)):
        return vn_title
    return rel_title or vn_title


def plat_group(code):
    if code in HOME_PLAT:
        return 0
    if code in PC_PLAT:
        return 1
    if code in MOBILE_PLAT:
        return 2
    return 3


GROUP_KEY = {0: "home", 1: "pc", 2: "mobile", 3: "other"}


def main():
    db = os.path.join(DATA, "vndb_otome.db")
    con = sqlite3.connect(db)
    vn_title = dict(con.execute("SELECT vid, title FROM games"))
    vids = set(vn_title)
    print("対象作品: %d件" % len(vids))

    # リリース → 作品（体験版は買うものではないので落とす）
    rel2vn = defaultdict(list)
    for r in read("releases_vn"):
        if r["vid"] in vids and r["rtype"] != "trial":
            rel2vn[r["id"]].append(r["vid"])

    plats = defaultdict(list)
    for r in read("releases_platforms"):
        if r["id"] in rel2vn:
            plats[r["id"]].append(r["platform"])

    # メディア。'in'（ネット配信）だけならダウンロード専売と判断できる
    media = defaultdict(set)
    for r in read("releases_media"):
        if r["id"] in rel2vn:
            media[r["id"]].add(r["medium"])

    titles = {}
    for r in read("releases_titles"):
        if r["id"] in rel2vn and r["lang"] == "ja" and r["title"]:
            # 「ＤＬ版」のような全角表記が実在するので、判定前に半角へ寄せる
            titles[r["id"]] = r["title"].translate(ZEN2HAN)

    # 配信ストアのリンクは buy_links.py が作った vndb_links から引く
    links = defaultdict(dict)
    for rid, site, url in con.execute(
            "SELECT rid, site, url FROM vndb_links WHERE url IS NOT NULL"):
        links[rid].setdefault(site, url)

    con.executescript(SCHEMA)

    rows = []
    n_rel = n_skip_lang = n_skip_unofficial = 0
    for r in read("releases"):
        rid = r["id"]
        if rid not in rel2vn:
            continue
        if r["official"] != "t" or r["patch"] == "t":
            n_skip_unofficial += 1
            continue
        title = titles.get(rid)
        if not title:
            n_skip_lang += 1      # 日本語名が無いリリース＝海外版。国内の購入導線には出さない
            continue
        n_rel += 1

        base, edition, label, kind, rank, dl = parse_edition(title)
        released = vndb_date(r["released"]) or ""
        gtin = r["gtin"] if r["gtin"] and r["gtin"] != "0" else ""
        n_vn = len(rel2vn[rid])
        # 版名に書かれていなくても、メディアがネット配信だけならDL版
        if not dl and media.get(rid) == {"in"}:
            dl = True
            label = ("%s（ダウンロード版）" % label) if label else "ダウンロード版"

        for code in plats.get(rid, []) or ["oth"]:
            g = plat_group(code)
            store_site = store_url = ""
            for site in STORE_PREF[GROUP_KEY[g]]:
                if site in links.get(rid, {}):
                    store_site, store_url = site, links[rid][site]
                    break
            for vid in rel2vn[rid]:
                rows.append((
                    "%s-%s-%s" % (vid, rid, code), vid, rid, code,
                    PLATFORM_JA.get(code, code), g,
                    base, search_keyword(base, vn_title[vid]),
                    edition, label, kind, rank,
                    released, gtin, "", 1 if dl else 0,
                    store_site, store_url, n_vn, ""))

    print("リリース %d件を採用（海外版のみ %d件・非公式/パッチ %d件を除外）"
          % (n_rel, n_skip_lang, n_skip_unofficial))

    # --- 重複の整理 -------------------------------------------------
    # 同じ機種の同じ版が何度も登録されていることがある（Steam版の再登録など）。
    # JANを持つもの、次に新しいものを残す。
    F = {n: i for i, n in enumerate(
        "eid vid rid platform platform_ja plat_group rel_title search_kw edition "
        "edition_label edition_kind edition_rank released gtin asin is_dl "
        "store_site store_url n_vn plat_sort".split())}
    best = {}
    for row in rows:
        key = (row[F["vid"]], row[F["platform"]], row[F["edition_kind"]],
               row[F["is_dl"]], row[F["rel_title"]])
        cur = best.get(key)
        if cur is None or ((bool(row[F["gtin"]]), row[F["released"]])
                           > (bool(cur[F["gtin"]]), cur[F["released"]])):
            best[key] = row
    kept = list(best.values())
    print("重複を整理: %d行 -> %d行" % (len(rows), len(kept)))

    # --- 機種グループの並べ替えキー ---------------------------------
    # 家庭用機を先に、その中では新しく出た機種を上に置く（今買えるものが上に来る）
    newest = defaultdict(str)
    for row in kept:
        k = (row[F["vid"]], row[F["platform"]])
        newest[k] = max(newest[k], row[F["released"]])
    kept = [row[:-1] + ("%d|%s" % (row[F["plat_group"]],
                                   newest[(row[F["vid"]], row[F["platform"]])].ljust(10, "0")),)
            for row in kept]

    con.executemany("INSERT INTO editions VALUES (%s)" % ",".join("?" * len(F)), kept)

    # 手で足したJAN（VNDBに入っていないもの）
    n_manual = 0
    for vid, per_plat in MANUAL_GTIN.items():
        for code, gtin in per_plat.items():
            cur = con.execute("""UPDATE editions SET gtin=? WHERE vid=? AND platform=?
                                 AND is_dl=0 AND gtin=''""", (gtin, vid, code))
            n_manual += cur.rowcount
    if n_manual:
        print("overrides.py のJANを %d行に反映" % n_manual)

    # 表紙をVNDBの画像で出してよい作品
    con.execute("""INSERT INTO vndb_image_ok
                   SELECT vid, 'dl' FROM editions GROUP BY vid HAVING MIN(is_dl)=1""")
    for vid in FORCE_VNDB_IMAGE:
        con.execute("INSERT OR REPLACE INTO vndb_image_ok VALUES (?, 'manual')", (vid,))
    con.commit()

    # --- 結果 -------------------------------------------------------
    q = lambda s: con.execute(s).fetchone()[0]
    HOME = ("Switch", "PS", "ニンテンドー", "Xbox")
    cond = " OR ".join("platforms LIKE '%%%s%%'" % k for k in HOME)
    sub = "SELECT vid FROM games WHERE %s" % cond
    print()
    print("editions %d行 / %d作品" % (len(kept), q("SELECT COUNT(DISTINCT vid) FROM editions")))
    print()
    print("=== 版の種別 ===")
    for k, n in con.execute("""SELECT CASE edition_kind WHEN '' THEN '(版名なし)' ELSE edition_kind END,
                               COUNT(*) FROM editions GROUP BY 1 ORDER BY 2 DESC"""):
        print("  %-10s %5d行" % (k, n))
    print("  うちDL版   %5d行" % q("SELECT COUNT(*) FROM editions WHERE is_dl=1"))
    print()
    print("=== 掲載対象（家庭用機の作品）===")
    print("  作品          %4d件" % q("SELECT COUNT(*) FROM games WHERE %s" % cond))
    print("  版            %4d行" % q("SELECT COUNT(*) FROM editions WHERE vid IN (%s)" % sub))
    print("  JANあり       %4d行" % q("SELECT COUNT(*) FROM editions WHERE gtin<>'' AND vid IN (%s)" % sub))
    print("  JANの実数     %4d件（楽天APIを叩く回数）"
          % q("SELECT COUNT(DISTINCT gtin) FROM editions WHERE gtin<>'' AND vid IN (%s)" % sub))
    print("  配信ストア    %4d行" % q("SELECT COUNT(*) FROM editions WHERE store_url<>'' AND vid IN (%s)" % sub))
    print("  検索語を作品名に切替 %d行"
          % q("SELECT COUNT(*) FROM editions WHERE search_kw<>rel_title AND vid IN (%s)" % sub))
    multi = q("""SELECT COUNT(*) FROM (SELECT vid FROM editions WHERE vid IN (%s)
                 GROUP BY vid HAVING COUNT(*)>1)""" % sub)
    print("  版が2つ以上   %4d作品" % multi)

    # 同じJANが別のリリースに付いているものはVNDB側の誤りの可能性が高いので出しておく
    n_dup = q("""SELECT COUNT(*) FROM (SELECT gtin FROM editions WHERE gtin<>''
                 GROUP BY gtin HAVING COUNT(DISTINCT rid)>1)""")
    if n_dup:
        print()
        print("※ 同じJANが複数リリースに付いている（VNDB側の要確認）: %d件" % n_dup)
        for g, c in con.execute("""SELECT gtin, COUNT(DISTINCT rid) c FROM editions
                                   WHERE gtin<>'' GROUP BY gtin HAVING c>1
                                   ORDER BY c DESC LIMIT 5"""):
            names = [x[0] for x in con.execute(
                "SELECT DISTINCT rel_title||' '||edition FROM editions WHERE gtin=? LIMIT 3", (g,))]
            print("   %s -> %s" % (g, " / ".join(n.strip() for n in names)))
    con.close()
    print()
    print("-> %s" % db)


if __name__ == "__main__":
    main()
