# -*- coding: utf-8 -*-
"""【画像】作品のパッケージ画像を、楽天が返す商品画像から選ぶ。

  python3 scripts/rakuten_prices.py --only-scope   # 先に商品を取っておく
  python3 scripts/shop_images.py

なぜ必要か
----------
VNDBの画像は「個々の画像はフェアユース」という立場であって第三者への
利用許諾ではなく、日本にフェアユース規定は無い。VNDBの帯域も使うので
公開には向かない。楽天の商品画像は「返ってきたURLのまま表示する」形なら
規約上使えるので、公開時（IMAGE_MODE="affiliate"）はこちらに差し替える。

どれを選ぶか
------------
店によって元画像の大きさがまるで違う（実測）。

    楽天ブックス          741 x 1200
    アニメイト系・家電量販   300〜600 幅
    駿河屋楽天市場店       118 x 192
    ブックオフ            150 x 246 ／ 画像なしの代替gifが返ることもある

作品ページのパッケージ画像は 220px 幅で出すので、小さい元画像を引き伸ばすと
粗くなる。楽天ブックス → そのほかの新品 → 中古 の順に選び、
中古専門店の小さい画像は最後の手段にする。
"""
import concurrent.futures
import os
import re
import sqlite3
import struct
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA

DB = os.path.join(DATA, "vndb_otome.db")

# 220px幅で出すので、2倍の余裕を見て500pxの箱に収める。
# 元画像が小さいときは拡大されない（楽天の画像サーバは縮小のみ）
EX = "?_ex=500x500"

# 実際に配信される高さがこれ未満の店は「小さい」とみなす。
# 220px幅で出すので、縦500pxあれば2倍の余裕がある
GOOD_HEIGHT = 400
POOR_HEIGHT = 300

RAKUTEN_BOOKS = "book"      # 楽天ブックス。元画像が大きく、帯や中古シールも無い

# 版の代表性。作品の顔として出すなら通常版がいい
EDITION_RANK = {"": 0, "通常": 0, "限定": 1, "セット": 2, "廉価": 3}

# 機種の優先順。Switch版が出ている作品はSwitchのパッケージを顔にする
# （いま買える版なので、探している人が店頭で見るものと一致する）
PLATFORM_RANK = {"swi": 0, "sw2": 0}
OTHER_HOME_RANK = 1     # そのほかの家庭用機
PC_RANK = 2
ETC_RANK = 3


def platform_rank(code, plat_group):
    if code in PLATFORM_RANK:
        return PLATFORM_RANK[code]
    return {0: OTHER_HOME_RANK, 1: PC_RANK}.get(plat_group, ETC_RANK)

MEASURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS shop_image_size (
    shop_code TEXT PRIMARY KEY,
    width INTEGER, height INTEGER, n INTEGER, checked_at TEXT
);
CREATE TABLE IF NOT EXISTS image_probe (
    url TEXT PRIMARY KEY, width INTEGER, height INTEGER, checked_at TEXT
);
"""

SCHEMA = """
DROP TABLE IF EXISTS shop_images;
CREATE TABLE shop_images (
    vid TEXT PRIMARY KEY REFERENCES games(vid),
    url TEXT,
    source TEXT,     -- 選んだ店のコード
    height INTEGER,  -- 配信される画像の高さ（実測。分かっていれば）
    eid TEXT         -- どの版の画像か
);
"""


# 同じ画像が何件のJANで使い回されていたら「商品の写真ではない」とみなすか。
# 実測すると 1JAN=7644枚 に対して 3JAN以上は13枚しかなく、その全部が
# no_img.gif / noimage.jpg / shippingfree.gif のような店の共通画像だった。
# 通常版と限定版で同じ写真を使う店はあるので、2件までは許す。
SHARED_MAX = 3


def usable(url):
    """商品の写真として使えるURLか。ファイル名で分かるものはここで落とす"""
    if not url:
        return False
    return not re.search(r"no[_-]?img|no[_-]?image|no[_-]?photo|nowprinting|now_printing"
                         r"|shippingfree|caution|comingsoon|coming_soon|dummy|sample",
                         url, re.I)


def jpeg_size(b):
    """JPEGの先頭だけから縦横を読む。全部落とさずに済む"""
    i = 2
    while i < len(b) - 9:
        if b[i] != 0xFF:
            i += 1
            continue
        m = b[i + 1]
        if m in (0xC0, 0xC1, 0xC2, 0xC3):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return w, h
        if m in (0xD8, 0xD9):
            i += 2
            continue
        i += 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
    return None


def probe(url):
    """配信される画像の大きさを測る。先頭16KBだけ取る"""
    req = urllib.request.Request(url + EX, headers={"Range": "bytes=0-16383",
                                                    "User-Agent": "otomedana/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return jpeg_size(r.read())
    except (urllib.error.URLError, OSError, struct.error):
        return None


TRY_N = 6          # 1作品あたり実測する候補の上限


def probe_best(con, cands):
    """候補を上から実測して、十分な高さの最初の1枚を選ぶ。
    どれも届かなければ、測れた中でいちばん大きいものを使う"""
    cache = {r[0]: (r[1], r[2]) for r in con.execute("SELECT url,width,height FROM image_probe")}
    todo = []
    for v, lst in cands.items():
        for _, it, _e in lst[:TRY_N]:
            if it["image_url"] not in cache:
                todo.append(it["image_url"])
    todo = list(dict.fromkeys(todo))
    if todo:
        print("出す画像を1枚ずつ実測中… %d枚" % len(todo))
        got = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for u, sz in zip(todo, ex.map(probe, todo)):
                got.append((u, sz[0] if sz else None, sz[1] if sz else None))
                if sz:
                    cache[u] = sz
        con.executemany(
            "INSERT OR REPLACE INTO image_probe VALUES (?,?,?,datetime('now'))", got)
        con.commit()

    best = {}
    for v, lst in cands.items():
        picked = None
        fallback = None
        for sc, it, e in lst[:TRY_N]:
            wh = cache.get(it["image_url"])
            if not wh or not wh[1]:
                continue
            if fallback is None or wh[1] > fallback[0][1]:
                fallback = (wh, it, e)
            if wh[1] >= GOOD_HEIGHT:
                picked = (wh, it, e)
                break
        chosen = picked or fallback
        if chosen:
            wh, it, e = chosen
            best[v] = (wh[1], it, e)
    return best


def measure_shops(con, items):
    """店ごとに画像の大きさを実測してキャッシュする。

    店によって元画像が 118x192 から 741x1200 まで開きがある。
    店ごとにほぼ一定なので、1店につき2枚だけ測れば順位付けには足りる。
    """
    con.executescript(MEASURE_SCHEMA)
    known = {r[0] for r in con.execute("SELECT shop_code FROM shop_image_size")}
    samples = {}
    for rows in items.values():
        for r in rows:
            sc = r["shop_code"]
            if sc and sc not in known and len(samples.setdefault(sc, [])) < 2:
                samples[sc].append(r["image_url"])
    if not samples:
        return
    print("画像の大きさを実測中… %d店 / %d枚" % (len(samples), sum(len(v) for v in samples.values())))

    def one(sc):
        sizes = [s for s in (probe(u) for u in samples[sc]) if s]
        if not sizes:
            return None
        w = max(s[0] for s in sizes)
        h = max(s[1] for s in sizes)
        return (sc, w, h, len(sizes))

    out = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(one, list(samples)):
            if res:
                out.append(res + (None,))
    con.executemany("INSERT OR REPLACE INTO shop_image_size VALUES (?,?,?,?,datetime('now'))",
                    [(a, b, c, d) for a, b, c, d, _ in out])
    con.commit()
    print("  測れた店: %d / %d" % (len(out), len(samples)))


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    # 何件のJANで使い回されている画像かを先に数える。
    # 店の共通画像（送料無料バナー・画像準備中）はファイル名で分からないものもあり、
    # 「複数のJANで同じ写真」が いちばん確かな手がかりになる
    shared = {}
    for u, n in con.execute(
            """SELECT image_url, COUNT(DISTINCT jan) FROM rakuten_items
               WHERE image_url IS NOT NULL GROUP BY image_url
               UNION ALL
               SELECT image_url, COUNT(DISTINCT eid) FROM rakuten_title_items
               WHERE image_url IS NOT NULL GROUP BY image_url"""):
        shared[u] = shared.get(u, 0) + n
    n_shared = sum(1 for u, n in shared.items() if n >= SHARED_MAX)

    items = {}
    for sql in ("""SELECT jan AS k, shop_code, condition, image_url, review_count
                   FROM rakuten_items WHERE image_url IS NOT NULL""",
                # JANの無い版はタイトル検索の結果を eid で引く
                """SELECT eid AS k, shop_code, condition, image_url, review_count
                   FROM rakuten_title_items WHERE image_url IS NOT NULL"""):
        for r in con.execute(sql):
            u = r["image_url"]
            if usable(u) and shared.get(u, 0) < SHARED_MAX:
                items.setdefault(r["k"], []).append(r)
    print("店の共通画像として除外: %d枚（%d JANで使い回されていたもの）"
          % (n_shared, SHARED_MAX))

    # JANがあればJANで、無ければ eid で候補を引く
    eds = con.execute("""SELECT eid, vid,
                                CASE WHEN gtin <> '' THEN gtin ELSE eid END AS key,
                                gtin, edition_kind, platform, plat_group, released, is_dl
                         FROM editions""").fetchall()

    measure_shops(con, items)
    heights = {r[0]: r[1] for r in con.execute("SELECT shop_code, height FROM shop_image_size")}

    def score(e, it):
        h = heights.get(it["shop_code"], 0)
        # 実測した高さで3段階に分ける。同じ段の中では新品・楽天ブックスを優先
        band = 0 if h >= GOOD_HEIGHT else (1 if h >= POOR_HEIGHT else 2)
        if it["shop_code"] == RAKUTEN_BOOKS:
            shop = 0
        elif it["condition"] == "新品":
            shop = 1
        else:
            shop = 2
        return (band,
                platform_rank(e["platform"], e["plat_group"]),
                shop,
                EDITION_RANK.get(e["edition_kind"], 5),
                # 今買える版を優先するので新しい順
                "" if not e["released"] else "".join(chr(255 - ord(c)) for c in e["released"]),
                -(it["review_count"] or 0))

    # 作品ごとに候補を良さそうな順に並べる
    cands = {}
    for e in eds:
        if e["is_dl"]:
            continue
        for it in items.get(e["key"], ()):
            cands.setdefault(e["vid"], []).append((score(e, it), it, e))
    for v in cands:
        cands[v].sort(key=lambda t: t[0])

    # 店ごとの実測はあくまで目安で、同じ店でも商品によって大きさが違う。
    # 実際に出す1枚は個別に測って確かめる（上位 TRY_N 件まで）
    best = probe_best(con, cands)

    con.executescript(SCHEMA)
    rows = [(vid, it["image_url"] + EX, it["shop_code"], h, e["eid"])
            for vid, (h, it, e) in best.items()]
    con.executemany("INSERT INTO shop_images VALUES (?,?,?,?,?)", rows)
    con.commit()

    q = lambda s: con.execute(s).fetchone()[0]
    HOME = ("Switch", "PS", "ニンテンドー", "Xbox")
    cond = " OR ".join("platforms LIKE '%%%s%%'" % k for k in HOME)
    sub = "SELECT vid FROM games WHERE %s" % cond
    tot = q("SELECT COUNT(*) FROM games WHERE %s" % cond)
    got = q("SELECT COUNT(*) FROM shop_images WHERE vid IN (%s)" % sub)
    print("shop_images %d行" % len(rows))
    print("掲載対象 %d件 のうち画像あり %d件 (%.0f%%)" % (tot, got, 100.0 * got / tot))
    print()
    print("=== 配信される画像の高さ ===")
    for label, lo, hi in (("400px以上（十分）", GOOD_HEIGHT, 99999),
                          ("300〜399px（やや小さい）", POOR_HEIGHT, GOOD_HEIGHT),
                          ("300px未満（粗い）", 0, POOR_HEIGHT)):
        n = q("""SELECT COUNT(*) FROM shop_images WHERE vid IN (%s)
                 AND height >= %d AND height < %d""" % (sub, lo, hi))
        print("  %-24s %4d件" % (label, n))
    unk = q("SELECT COUNT(*) FROM shop_images WHERE vid IN (%s) AND height IS NULL" % sub)
    if unk:
        print("  %-24s %4d件" % ("測れなかった", unk))
    print()
    print("=== 選ばれた店（上位）===")
    for sc, n, h in con.execute("""SELECT source, COUNT(*), MAX(height) FROM shop_images
                                   WHERE vid IN (%s) GROUP BY source
                                   ORDER BY 2 DESC LIMIT 8""" % sub):
        print("  %-24s %4d件  高さ %s" % (sc, n, h or "?"))
    con.close()


if __name__ == "__main__":
    main()
