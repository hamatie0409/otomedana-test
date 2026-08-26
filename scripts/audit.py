# -*- coding: utf-8 -*-
"""データの検収。作品ページに出る情報が信用できるかを全数で調べる。

    python3 scripts/audit.py                 # 人が読むレポート
    python3 scripts/audit.py --json          # 機械可読（CI用）
    python3 scripts/audit.py --limit 40      # 要確認リストの表示件数
    python3 scripts/audit.py --strict        # 要確認が残っていても落とす

検査は性質の違う2種類に分けてある。混ぜると使えなくなる。

**構造の検査** … 満たされていなければバグ。違反が1件でもあれば異常終了する。
**結合の検査** … 白黒つかない。risk順に並べて人に見せ、判断は audit_ok.py に残す。

なぜ自動で弾かないか
--------------------
「作品名が商品名に含まれない」を不合格にすると、正しいものが大量に落ちる。
実測すると item リンク2,961行のうち584行が不一致だが、その中身は

  合本        華ヤカ哉 キネマモザイク → 幻燈ノスタルジィ（収録作品。買う手段はこれだけ）
  英題と邦題   Real Rode → リアルロデ / ビタミンX → VitaminX
  表記ゆれ     Collar x Malice → Collar×Malice
  廉価版      召しませ浪漫茶房 → SIMPLE2000シリーズVol.98 THE浪漫茶房
  移植版      薄桜鬼 新選組奇譚 → 薄桜鬼 ポータブル

と、ほとんどが正しい結び付きだった。弱い手がかりを足し合わせて順位を付け、
人が上から見るのが結局いちばん速い。
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from audit_ok import ACK, COMPILATION_OK, CHAR_ACK
except ImportError:
    ACK, COMPILATION_OK, CHAR_ACK = set(), {}, set()
try:
    from rakuten_prices import not_the_game
except ImportError:
    def not_the_game(_name):
        return False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "vndb_otome.db")

PRICE_RATIO = 3.0      # 機種×状態の中央値の何倍から外れ値とみなすか
RISK_SHOW = 4          # これ以上を要確認として出す


def norm(s):
    """比較用に潰す。全半角・大文字小文字・記号・空白の違いを無視する"""
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[^0-9a-z぀-ヿ一-鿿]", "", s)


# ---------------------------------------------------------------- 構造の検査

def structure(con):
    """満たされていなければバグ。1件でもあれば異常終了する"""
    out = []

    def check(name, sql, hint):
        rows = con.execute(sql).fetchall()
        if rows:
            out.append({"name": name, "n": len(rows), "hint": hint,
                        "sample": [list(r) for r in rows[:5]]})

    check("同じ版・同じ販路・同じ状態に商品リンクが2つ以上",
          """SELECT eid, channel, condition, COUNT(*) FROM offers
             WHERE link_type='item' GROUP BY eid, channel, condition HAVING COUNT(*) > 1""",
          "apply_to_offers の UPDATE が複数行に当たっている")

    check("offers と editions で作品IDが食い違う",
          """SELECT o.eid, o.vid, e.vid FROM offers o JOIN editions e ON e.eid=o.eid
             WHERE o.vid <> e.vid""",
          "版の組み直しか offers の生成で作品の対応が壊れている")

    check("表紙が、その作品の版でない版から来ている",
          """SELECT s.vid, s.eid FROM shop_images s
             WHERE s.eid IS NOT NULL AND s.eid NOT IN
                   (SELECT eid FROM editions WHERE vid = s.vid)""",
          "shop_images.eid が別作品の版を指している")

    check("URLスラッグが重複",
          """SELECT kind, slug, COUNT(*) FROM slugs WHERE is_page=1
             GROUP BY kind, slug HAVING COUNT(*) > 1""",
          "別のページが同じURLを取り合う")

    check("価格があるのに在庫表示が無い",
          """SELECT eid, channel, price FROM offers
             WHERE link_type='item' AND price IS NOT NULL
               AND (availability IS NULL OR availability='')""",
          "site_build 側で在庫を見て出し分けているので表示が崩れる")

    check("商品リンクなのに URL が無い",
          """SELECT eid, channel FROM offers
             WHERE link_type='item' AND (url IS NULL OR url='')""",
          "リンク先が空のボタンが出る")

    return out


# ---------------------------------------------------------------- 結合の検査

def joins(con):
    """白黒つかないものを risk 順に並べる"""
    rows = con.execute("""
        SELECT o.eid, o.vid, o.channel, o.condition, o.price, o.item_code, o.item_name,
               g.title, e.rel_title, e.edition_label, e.platform_ja, e.plat_group, e.n_vn, e.rid
        FROM offers o
        JOIN editions e ON e.eid = o.eid
        JOIN games g    ON g.vid = o.vid
        WHERE o.link_type='item'
    """).fetchall()

    # 同じ商品が何作品に付いているか
    per_item = {}
    for r in rows:
        per_item.setdefault(r["item_code"], set()).add(r["vid"])

    # 機種×状態ごとの値ごろ感。中央値から大きく離れたものを疑う
    buckets = {}
    for r in rows:
        if r["price"]:
            buckets.setdefault((r["plat_group"], r["condition"]), []).append(r["price"])
    median = {k: statistics.median(v) for k, v in buckets.items() if len(v) >= 5}

    found = []
    for r in rows:
        why, risk = [], 0

        # 合本そのものは誤りではない。「ツインパック」のように版の名前で
        # そうと分かるなら、通常版と並べて出しても誤解は生まれない。
        # 問題は版の名前が「通常版」「限定版」や空で、中身が別タイトルのとき。
        # 実物のページで、同じ作品に「通常版 ¥30,472」（実体は合本）と
        # 「通常版 ¥380」（実体はその作品）が並んでいた。
        n_vn = r["n_vn"] or 1
        if n_vn >= 2 and str(r["rid"]) not in COMPILATION_OK:
            vague = (r["edition_label"] or "").strip() in ("", "通常版", "限定版")
            if vague and norm(r["title"]) != norm(r["rel_title"]):
                why.append("合本なのに版の名前で見分けられない（%d作品入り・実体は「%s」）"
                           % (n_vn, r["rel_title"]))
                risk += 3
            else:
                why.append("合本（%d作品入り・版の名前で判別できる）" % n_vn)
                risk += 1

        shared = per_item.get(r["item_code"]) or set()
        if len(shared) >= 2:
            why.append("同じ商品が複数の作品に付いている（%d作品）" % len(shared))
            risk += 2

        med = median.get((r["plat_group"], r["condition"]))
        if med and r["price"] and r["price"] > med * PRICE_RATIO:
            why.append("価格が%s中央値から外れる（%.1f倍・¥%s / 中央値¥%d）"
                       % (r["condition"], r["price"] / med, r["price"], med))
            risk += 2

        # 商品そのものがゲームでない（特典小冊子・付箋・特典CD）。
        # ¥3,540 の特典小冊子がゲームとして並んでいたことがある
        if not_the_game(r["item_name"]):
            why.append("商品がゲーム本体ではない")
            risk += 4

        t, rel, item = norm(r["title"]), norm(r["rel_title"]), norm(r["item_name"])
        if t and t not in rel:
            why.append("作品名がリリース名に無い")
            risk += 1
        if rel and rel[:14] not in item:
            why.append("リリース名が商品名に無い")
            risk += 1

        if not why:
            continue
        if "%s:%s" % (r["vid"], r["item_code"]) in ACK:
            continue
        found.append({"vid": r["vid"], "eid": r["eid"], "rid": r["rid"],
                      "title": r["title"], "rel_title": r["rel_title"],
                      "item_name": r["item_name"], "item_code": r["item_code"],
                      "channel": r["channel"], "condition": r["condition"],
                      "price": r["price"], "platform": r["platform_ja"],
                      "risk": risk, "why": why})

    found.sort(key=lambda x: (-x["risk"], -(x["price"] or 0)))
    return found, len(rows)


# ------------------------------------------------------- キャラクターの検査

PUB_GAMES = "SELECT key FROM slugs WHERE kind='game' AND is_page=1"

# 想定される値。ここに無い値が入っていたら取り込みが壊れている
SEX_OK = ("m", "f", "b", "")
ROLE_OK = ("攻略対象", "サブキャラ", "主人公", "登場のみ")

# 人の身体としてありえない範囲。VNDBは非人間キャラも扱うので、
# 「ありえない」ではなく「転記ミスを疑う」ための緩い枠にしてある
# VNDBが「名前が無い」ことを示すために置く英語の符丁。日本語サイトにそのまま
# 出ると読めないので vndb_build.py で「主人公」に寄せている。ここは再発の見張り。
#   「???」「？？？」は符丁ではなく、作中で名前が伏せられているキャラの表記
#   （幻奏喫茶アンシャンテ・ピオフィオーレなど10人）。正しいので対象にしない。
PLACEHOLDER = ("Protagonist", "protagonist", "MC")

# 作中で名前が伏せられているキャラの表記。日本語が1文字も無いが誤りではない
MASKED = ("???", "？？？", "?????", "―", "－")

# 日本語が1文字も無い名前。VNDBに日本語表記が無いだけで、人物としては
# 日本人という例が多い（Arisugawa Yuki など）。誤りではないが読みにくい
JA_CHARS = re.compile(r"[぀-ヿ一-鿿]")

H_MIN, H_MAX = 100, 250      # cm
W_MIN, W_MAX = 20, 200       # kg
AGE_MAX = 120                # 歳

# 未発売かどうかの境目。発売前の作品はキャストが未発表なのがふつうなので、
# 「声優が空」の検査はここより後の発売日を持つ作品には当てない
TODAY = datetime.date.today().isoformat()


def chars_structure(con):
    """キャラクター側の構造の検査。満たされていなければバグ"""
    out = []

    def check(name, sql, hint):
        rows = con.execute(sql).fetchall()
        if rows:
            out.append({"name": name, "n": len(rows), "hint": hint,
                        "sample": [list(r) for r in rows[:5]]})

    check("キャラページのcidがcharactersに無い",
          """SELECT key FROM slugs WHERE kind='character' AND is_page=1
             AND key NOT IN (SELECT cid FROM characters)""",
          "中身の無いページが出る")

    check("traitsのcidがcharactersに無い",
          """SELECT DISTINCT t.cid FROM traits t WHERE t.vid IN (%s)
             AND t.cid NOT IN (SELECT cid FROM characters)""" % PUB_GAMES,
          "属性が誰のものか辿れない")

    check("同じ作品の中でcidが重複",
          """SELECT vid, cid, COUNT(*) FROM characters WHERE vid IN (%s)
             GROUP BY vid, cid HAVING COUNT(*) > 1""" % PUB_GAMES,
          "同じキャラが作品ページに二重に出る")

    check("同じcidなのに作品ごとに名前が違う",
          """SELECT cid, COUNT(DISTINCT name) FROM characters WHERE vid IN (%s)
             GROUP BY cid HAVING COUNT(DISTINCT name) > 1""" % PUB_GAMES,
          "1つのキャラページに複数の名前が候補として立つ")

    check("性別が想定外の値",
          "SELECT DISTINCT sex FROM characters WHERE sex IS NOT NULL AND sex NOT IN %s"
          % (SEX_OK,),
          "攻略対象の出し分けが性別を見ているので表示が崩れる")

    check("区分が想定外の値",
          "SELECT DISTINCT role FROM characters WHERE role NOT IN %s" % (ROLE_OK,),
          "role_label が対応していない値はそのまま画面に出る")

    check("誕生日が「n月n日」の形でない",
          """SELECT cid, birthday FROM characters WHERE vid IN (%s)
             AND birthday <> '' AND birthday NOT GLOB '*月*日'""" % PUB_GAMES,
          "取り込みで月日の組み立てが崩れている")

    check("身長・体重・年齢が0以下",
          """SELECT cid, height, weight, age FROM characters WHERE vid IN (%s)
             AND (height <= 0 OR weight <= 0 OR age < 0)""" % PUB_GAMES,
          "数値カラムに欠測値の記号が入っている")

    check("立ち絵がVNDB以外のホストから来ている",
          """SELECT DISTINCT SUBSTR(image_url, 1, 40) FROM characters
             WHERE vid IN (%s) AND image_url <> ''
             AND image_url NOT LIKE 'https://t.vndb.org/%%'""" % PUB_GAMES,
          "画像の出どころが変わっている（利用条件が別になる）")

    check("キャラ名が空、または前後に空白",
          """SELECT cid, name FROM characters WHERE vid IN (%s)
             AND (name IS NULL OR name = '' OR name <> TRIM(name))""" % PUB_GAMES,
          "slug の生成と見出しが壊れる")

    # 声優名の表記。DBの慣習は「姓 名」で、間は半角スペース1つ。
    # 全角スペースそのものは誤りではない（VNDB側が「篁　莎耶」で登録している
    # 例がある）。困るのは、同じ人が空白の使い方だけ違う2通りで入って
    # 別人に分裂するとき。声優ページが2つに割れて担当作品が半分ずつになる
    check("空白の使い方だけが違う声優名が両方ある",
          """SELECT a.cv, b.cv FROM (SELECT DISTINCT cv FROM characters
                                     WHERE vid IN (%s) AND cv <> '') a
             JOIN (SELECT DISTINCT cv FROM characters
                   WHERE vid IN (%s) AND cv <> '') b
               ON a.cv < b.cv
              AND REPLACE(REPLACE(a.cv, '　', ''), ' ', '')
                = REPLACE(REPLACE(b.cv, '　', ''), ' ', '')""" % (PUB_GAMES, PUB_GAMES),
          "同一人物が2ページに割れ、担当作品が分散する")

    check("声優名の前後に空白",
          "SELECT DISTINCT cv FROM characters WHERE cv <> '' AND cv <> TRIM(cv)",
          "同上")

    check("声優欄に注記が混じる",
          """SELECT DISTINCT cv FROM characters WHERE vid IN (%s)
             AND (cv LIKE '%%(%%' OR cv LIKE '%%（%%' OR cv LIKE '%%/%%'
                  OR cv LIKE '%%、%%')""" % PUB_GAMES,
          "1つの欄に複数人が入っている可能性がある")

    check("ボイスなしの作品なのに声優が入っている",
          """SELECT c.vid, c.name, c.cv FROM characters c JOIN games g ON g.vid = c.vid
             WHERE c.vid IN (%s) AND g.voiced = 'ボイスなし' AND c.cv <> ''""" % PUB_GAMES,
          "作品のボイス情報かキャストのどちらかが誤り")

    # 声優名から声優ページへ辿れること。別名義は slug_aliases で本名義に寄せる。
    # 担当1作品の人にページが無いのは正しい（MIN_WORKS["cv"]=2）。site_build は
    # ページのある行き先だけをリンクにするので、その人の名前は素の文字列で出る。
    # 異常なのは、2作品以上を担当しているのにページが無いとき＝ slugs が古い。
    # corrections.py は毎日走るが slugs.py はDBを作り直すときしか走らないので、
    # 訂正で声優を足すとこの差が出る
    check("担当2作品以上なのに声優ページが無い",
          """SELECT cv, COUNT(DISTINCT vid) FROM characters
             WHERE vid IN (%s) AND cv <> ''
             AND cv NOT IN (SELECT key FROM slugs WHERE kind='cv')
             AND cv NOT IN (SELECT alias FROM slug_aliases)
             GROUP BY cv HAVING COUNT(DISTINCT vid) >= 2""" % PUB_GAMES,
          "slugs が訂正に追いついていない。DBを作り直すと解消する")

    return out


def dead_links(_con):
    """生成済みの docs/ から、行き先の無い内部リンクを探す。

    slugs の url 列は is_page に関わらず埋まっている。site_build が
    そのまま href にすると、ページを作らない行き先（声優624人・属性など）への
    リンクになる。実測で 789本が 404 だった。DBだけを見ても分からないので、
    ここだけ生成物を読む。docs/ が無ければ何も言わずに飛ばす。
    """
    docs = os.path.join(ROOT, "docs")
    if not os.path.isdir(docs):
        return []
    pages, files_ = {"/"}, set()
    for dirpath, _dirs, files in os.walk(docs):
        rel = os.path.relpath(dirpath, docs).replace(os.sep, "/")
        base = "/" if rel == "." else "/%s/" % rel
        if "index.html" in files:
            pages.add(base)
        for fn in files:
            files_.add(base + fn)

    # root相対のリンクだけを見る。//example.com や http(s):// は外部
    href = re.compile(r'href="(/(?!/)[^"#?]*)"')
    prefix = "/" + os.path.basename(ROOT)     # BASE_URL の接頭辞
    dead = {}
    for dirpath, _dirs, files in os.walk(docs):
        if "index.html" not in files:
            continue
        f = os.path.join(dirpath, "index.html")
        with open(f, encoding="utf-8") as fh:
            html = fh.read()
        for m in set(href.findall(html)):
            t = m[len(prefix):] or "/" if m.startswith(prefix + "/") or m == prefix else m
            if t in pages or t in files_:
                continue
            dead.setdefault(t, os.path.relpath(f, ROOT))
    if not dead:
        return []
    return [{"name": "行き先の無い内部リンク", "n": len(dead),
             "hint": "ページを作らない slug（is_page=0）へリンクしている。"
                     "site_build の page_url() を通していない箇所がある",
             "sample": [[k, v] for k, v in list(dead.items())[:5]]}]


def chars_review(con):
    """キャラクター側の、白黒つかないものを risk 順に並べる

    作品側の「結合の検査」に当たる。違いは、疑うのが商品との結び付きではなく
    **VNDBに入っている値そのもの**だという点。外部と照らさないと決着しないので、
    ここでは順位を付けるところまでしかやらない。
    """
    rows = con.execute("""
        SELECT c.vid, c.cid, c.name, c.name_latin, c.role, c.sex, c.cv,
               c.birthday, c.height, c.weight, c.age, c.image_url,
               g.title, g.votecount, g.voiced, g.released, g.jawiki_url
        FROM characters c JOIN games g ON g.vid = c.vid
        WHERE c.vid IN (%s)
    """ % PUB_GAMES).fetchall()

    paged = {r[0] for r in con.execute(
        "SELECT key FROM slugs WHERE kind='character' AND is_page=1")}

    # 作品ごとの人数構成。主人公の有無や同名の重なりは作品単位でしか見えない
    by_vid = defaultdict(list)
    for r in rows:
        by_vid[r["vid"]].append(r)
    name_dup = set()      # (vid, name) が2人以上いる
    for vid, rs in by_vid.items():
        seen = Counter(x["name"] for x in rs)
        for nm, n in seen.items():
            if n > 1:
                name_dup.add((vid, nm))

    found = []
    for r in rows:
        why, risk = [], 0
        is_page = r["cid"] in paged

        nm = (r["name"] or "").strip()
        if nm in PLACEHOLDER:
            why.append("名前がVNDBの符丁のまま（%s）" % nm)
            risk += 3
        elif nm and nm not in MASKED and not JA_CHARS.search(nm):
            why.append("名前に日本語表記が無い（%s）" % nm)
            risk += 1 + (1 if is_page else 0)

        if (r["vid"], r["name"]) in name_dup:
            why.append("同じ作品に同じ名前のキャラが複数いる")
            risk += 2

        if not (r["sex"] or "").strip():
            why.append("性別が空（攻略対象の出し分けに使う）")
            risk += 2

        # 「女性なのに攻略対象」は検査しない。VNDBの primary は「主要キャラクター」
        # の意味で、女性が入っているのは誤りではない（掲載作品で90人）。表示側は
        # すでに男性だけを攻略対象と出す形に直してあるので、拾っても全件が空振りになる。

        # 未発売の作品はキャストがまだ発表されていないほうがふつうで、声優が空でも
        # 誤りではない。発売日が未定（空）か先の日付なら、この検査からは外す
        rel = (r["released"] or "").strip()
        unreleased = not rel or rel > TODAY
        if r["voiced"] == "フルボイス" and not (r["cv"] or "").strip() \
                and r["role"] == "攻略対象" and r["sex"] == "m" and not unreleased:
            why.append("フルボイス作品の攻略対象なのに声優が空")
            risk += 3

        h, w, ag = r["height"], r["weight"], r["age"]
        if h and not (H_MIN <= h <= H_MAX):
            why.append("身長が%dcm" % h)
            risk += 2
        if w and not (W_MIN <= w <= W_MAX):
            why.append("体重が%dkg" % w)
            risk += 2
        if ag and ag > AGE_MAX:
            why.append("年齢が%d歳" % ag)
            risk += 1

        if is_page and not (r["image_url"] or "").strip():
            why.append("ページがあるのに立ち絵が無い")
            risk += 1
        if is_page and not (r["name_latin"] or "").strip():
            why.append("ページがあるのにローマ字が無い（slugが名前から作れない）")
            risk += 1

        if not why:
            continue
        if "%s:%s" % (r["vid"], r["cid"]) in CHAR_ACK:
            continue
        found.append({"vid": r["vid"], "cid": r["cid"], "name": r["name"],
                      "title": r["title"], "votecount": r["votecount"] or 0,
                      "role": r["role"], "sex": r["sex"], "cv": r["cv"],
                      "is_page": is_page, "jawiki": r["jawiki_url"] or "",
                      "risk": risk, "why": why})

    # 作品単位の疑い。人単位の行に混ぜると同じ作品で何十行も出るので分けて持つ
    per_work = []
    for vid, rs in by_vid.items():
        g = rs[0]
        why, risk = [], 0
        n_hero = sum(1 for x in rs if x["role"] == "主人公")
        if n_hero == 0:
            why.append("主人公が1人もいない")
            risk += 2
        elif n_hero >= 2:
            why.append("主人公が%d人いる" % n_hero)
            risk += 1
        if why and vid not in CHAR_ACK:
            per_work.append({"vid": vid, "title": g["title"],
                             "votecount": g["votecount"] or 0,
                             "n": len(rs), "risk": risk, "why": why,
                             "jawiki": g["jawiki_url"] or ""})

    # キャラが1人も居ない作品は上のループに現れないので別に拾う
    for vid, title, vc, jw in con.execute("""
            SELECT vid, title, votecount, jawiki_url FROM games WHERE vid IN (%s)
            AND vid NOT IN (SELECT vid FROM characters)""" % PUB_GAMES):
        if vid in CHAR_ACK:
            continue
        per_work.append({"vid": vid, "title": title, "votecount": vc or 0,
                         "n": 0, "risk": 4 if (vc or 0) > 0 else 1,
                         "why": ["キャラクターが1人も登録されていない"],
                         "jawiki": jw or ""})

    # 得票の多い作品ほど人目に触れる。同じ risk なら票の多い順に見るのが得
    found.sort(key=lambda x: (-x["risk"], -x["votecount"]))
    per_work.sort(key=lambda x: (-x["risk"], -x["votecount"]))
    return found, per_work, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--strict", action="store_true",
                    help="要確認が残っていても異常終了する（CIで回帰を止める用）")
    ap.add_argument("--only", choices=("works", "chars"),
                    help="作品側／キャラクター側のどちらかだけを見る")
    ap.add_argument("--queue", action="store_true",
                    help="キャラクターの要確認を corrections/_queue_chars.tsv に書き出す")
    a = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("DBがありません: %s\n"
                 "  gh release download db-latest --pattern vndb_otome.db --dir data" % DB)

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    do_w = a.only in (None, "works")
    do_c = a.only in (None, "chars")

    bugs = ((structure(con) + dead_links(con)) if do_w else []) \
        + (chars_structure(con) if do_c else [])
    todo, total = joins(con) if do_w else ([], 0)
    high = [t for t in todo if t["risk"] >= RISK_SHOW]
    c_todo, c_work, c_total = chars_review(con) if do_c else ([], [], 0)
    c_high = [t for t in c_todo if t["risk"] >= RISK_SHOW]

    if a.queue:
        # verify_external.py と同じ流儀。ここでは直さず、人が出典を見て
        # corrections/characters.csv に移すための材料だけを置く
        path = os.path.join(ROOT, "corrections", "_queue_chars.tsv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# audit.py --queue が書いた要確認リスト。直す気になった行を\n"
                     "# corrections/characters.csv に移すこと（値・出典URL・確認日の3点が要る）。\n"
                     "# 正しいと確認できた行は audit_ok.py の CHAR_ACK に理由つきで入れる。\n")
            fh.write("risk\t得票\t作品\tvid\tcid\tキャラ\t区分\tページ\t理由\t照合先\n")
            for t in c_todo:
                fh.write("%d\t%d\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n"
                         % (t["risk"], t["votecount"], t["title"], t["vid"], t["cid"],
                            t["name"], t["role"], "有" if t["is_page"] else "無",
                            " / ".join(t["why"]), t["jawiki"]))
            for t in c_work:
                fh.write("%d\t%d\t%s\t%s\t\t（作品全体）\t\t\t%s\t%s\n"
                         % (t["risk"], t["votecount"], t["title"], t["vid"],
                            " / ".join(t["why"]), t["jawiki"]))
        print("書き出した: %s（%d人 + %d作品）"
              % (os.path.relpath(path, ROOT), len(c_todo), len(c_work)))
        return

    if a.json:
        print(json.dumps({"structure": bugs, "join_total": total,
                          "join_todo": len(todo), "join_high": len(high),
                          "items": todo,
                          "char_total": c_total, "char_todo": len(c_todo),
                          "char_high": len(c_high), "chars": c_todo,
                          "char_works": c_work},
                         ensure_ascii=False, indent=2))
    else:
        print("=== 構造の検査 ===")
        if not bugs:
            print("  違反なし")
        for b in bugs:
            print("  ✗ %s … %d件" % (b["name"], b["n"]))
            print("      %s" % b["hint"])
            for s in b["sample"]:
                print("      %s" % (s,))
        print()
    if not a.json and do_w:
        print("=== 作品：結合の検査 ===")
        print("  商品リンク総数   %d行" % total)
        print("  要確認          %d行 / %d作品（うち risk>=%d が %d行）"
              % (len(todo), len({t['vid'] for t in todo}), RISK_SHOW, len(high)))
        print("  承認済み        %d件" % len(ACK))
        print()
        tally = {}
        for t in todo:
            for w in t["why"]:
                tally[w.split("（")[0]] = tally.get(w.split("（")[0], 0) + 1
        print("  理由別の内訳（1行が複数該当する）:")
        for k, v in sorted(tally.items(), key=lambda x: -x[1]):
            print("    %-28s %5d行" % (k, v))
        print()
        for t in todo[:a.limit]:
            print("  [risk %d] %s" % (t["risk"], t["title"]))
            print("      %s / %s / ¥%s" % (t["platform"], t["condition"], t["price"]))
            print("      商品: %s" % t["item_name"][:70])
            print("      → %s" % " / ".join(t["why"]))
            print("      承認するなら audit_ok.py の ACK に \"%s:%s\"" % (t["vid"], t["item_code"]))
            print()
        if len(todo) > a.limit:
            print("  ほか %d行（--limit で増やす / --json で全件）" % (len(todo) - a.limit))

    if not a.json and do_c:
        print("=== キャラクター：内容の検査 ===")
        print("  掲載作品のキャラ   %d行" % c_total)
        print("  要確認            %d人 / %d作品（うち risk>=%d が %d人）"
              % (len(c_todo), len({t["vid"] for t in c_todo}), RISK_SHOW, len(c_high)))
        print("  作品単位の疑い     %d作品" % len(c_work))
        print("  承認済み          %d件" % len(CHAR_ACK))
        print()
        tally = {}
        for t in c_todo:
            for w in t["why"]:
                k = w.split("（")[0].split("が%d" % 0)[0]
                k = re.sub(r"\d+", "n", k)
                tally[k] = tally.get(k, 0) + 1
        print("  理由別の内訳（1人が複数該当する）:")
        for k, v in sorted(tally.items(), key=lambda x: -x[1]):
            print("    %-40s %5d人" % (k, v))
        print()
        if c_work:
            print("  作品単位:")
            for t in c_work[:a.limit]:
                print("    [risk %d] %s（%d票・キャラ%d人）"
                      % (t["risk"], t["title"], t["votecount"], t["n"]))
                print("        → %s" % " / ".join(t["why"]))
                if t["jawiki"]:
                    print("        照合先: %s" % t["jawiki"])
            if len(c_work) > a.limit:
                print("    ほか %d作品" % (len(c_work) - a.limit))
            print()
        for t in c_todo[:a.limit]:
            print("  [risk %d] %s ｜ %s（%d票）"
                  % (t["risk"], t["name"], t["title"], t["votecount"]))
            print("      区分 %s / 性別 %s / 声優 %s%s"
                  % (t["role"], t["sex"] or "—", t["cv"] or "—",
                     "" if t["is_page"] else " ／ページ無し"))
            print("      → %s" % " / ".join(t["why"]))
            if t["jawiki"]:
                print("      照合先: %s" % t["jawiki"])
            print("      承認するなら audit_ok.py の CHAR_ACK に \"%s:%s\"" % (t["vid"], t["cid"]))
            print()
        if len(c_todo) > a.limit:
            print("  ほか %d人（--limit で増やす / --json で全件）" % (len(c_todo) - a.limit))

    if bugs:
        sys.exit(1)
    if a.strict and (high or c_high):
        sys.exit(1)


if __name__ == "__main__":
    main()
