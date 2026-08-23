# -*- coding: utf-8 -*-
"""駿河屋の商品情報（JAN一致）で発売日を裏取りする（標準ライブラリのみ）。

    python3 scripts/verify_shops.py            # 内部で矛盾している作品を調べる
    python3 scripts/verify_shops.py --all      # 掲載作品ぜんぶ
    python3 scripts/verify_shops.py --queue    # 結果をファイルに書く

なぜ駿河屋か
------------
VNDB とも Wikipedia とも独立した小売の一次データで、**JANで引ける**。
タイトルで引くと、シリーズ物や合本、アニメ化作品で必ず取り違える。
実際 Wikipedia 照合ではクラスターエッジのゲーム(2006-09-14)にアニメの
放送開始日(2005-10-04)やWikipediaの誤記(2006-09-22)が当たっていたが、
JANで引けば一発で正しい日付が出た。

商品ページ(/product/detail/)は 403 で読めないが、検索結果ページには
「[発売日：2006/09/14]」の形で入っている。

何を調べるか
------------
既定では **作品の発売日に対応する版が1つも無いもの** を見る。
VNDB が発表時の予定日を VN 側に持ったまま、実際の発売が延びた場合にこうなる。
外部ソースが要らない内部矛盾なので、日本語Wikipediaの無い作品でも見つかる
（実測で40件中38件はWikipediaに記事が無かった）。
"""
import argparse
import html
import os
import re
import sqlite3
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "vndb_otome.db")
QUEUE = os.path.join(ROOT, "corrections", "_queue_shops.tsv")
KNOWN = os.path.join(ROOT, "corrections", "known_diffs.tsv")

SEARCH = "https://www.suruga-ya.jp/search?search_word=%s"
CACHE = """CREATE TABLE IF NOT EXISTS suruga_dates (
    jan TEXT PRIMARY KEY, released TEXT, fetched_at TEXT)"""
UA = "Mozilla/5.0 (compatible; otomedana-test/1.0; date verification)"
PAUSE = 1.6

# 「アブナイ★恋の捜査室 [通常版] PS2ソフト [発売日：2012/05/31]」の形
DATE = re.compile(r"発売日[：:]\s*(\d{4})[/年](\d{1,2})[/月](\d{1,2})")


def fetch_date(jan):
    """JANで検索して、結果に出る発売日を返す。無ければ None"""
    req = urllib.request.Request(SEARCH % jan, headers={"User-Agent": UA})
    raw = urllib.request.urlopen(req, timeout=30).read()
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            s = raw.decode(enc)
            break
        except UnicodeDecodeError:
            s = None
    if s is None:
        s = raw.decode("utf-8", "replace")
    t = html.unescape(re.sub("<[^>]+>", " ", s))
    m = DATE.search(t)
    return "%04d-%02d-%02d" % tuple(int(x) for x in m.groups()) if m else None


def load_known():
    known = set()
    if os.path.exists(KNOWN):
        for l in open(KNOWN, encoding="utf-8"):
            if l.strip() and not l.startswith("#") and not l.startswith("vid\t"):
                c = l.split("\t")
                if len(c) >= 2:
                    known.add((c[0].strip(), c[1].strip()))
    return known


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="掲載作品ぜんぶ調べる")
    ap.add_argument("--queue", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("DBがありません: %s" % DB)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(CACHE)
    cond = ("" if a.all else
            " HAVING g.released < MIN(e.released)")
    rows = con.execute("""
        SELECT g.vid, g.title, g.released, g.votecount, MIN(e.released) e0
        FROM games g JOIN slugs s ON s.key=g.vid AND s.kind='game' AND s.is_page=1
        JOIN editions e ON e.vid=g.vid AND e.released<>''
        WHERE g.released <> '' GROUP BY g.vid%s
        ORDER BY g.votecount DESC""" % cond).fetchall()
    known = load_known()
    rows = [r for r in rows if (r["vid"], "released") not in known]
    if a.limit:
        rows = rows[:a.limit]

    print("駿河屋で発売日を確かめます: %d件（判断済みは除く）" % len(rows))
    print()
    agree = diff = nohit = 0
    out = []
    for i, r in enumerate(rows, 1):
        # パッケージのある版だけ。ダウンロード版にJANは無い
        jans = [x[0] for x in con.execute(
            """SELECT gtin FROM editions WHERE vid=? AND gtin<>'' AND is_dl=0
               ORDER BY released""", (r["vid"],))]
        got = None
        for j in jans[:2]:
            c = con.execute("SELECT released FROM suruga_dates WHERE jan=?", (j,)).fetchone()
            if c:
                got = c["released"] or None
            else:
                try:
                    got = fetch_date(j)
                except Exception:
                    got = None
                con.execute("INSERT OR REPLACE INTO suruga_dates VALUES (?,?,?)",
                            (j, got or "", time.strftime("%Y-%m-%d %H:%M:%S")))
                con.commit()
                time.sleep(PAUSE)
            if got:
                break
        if not got:
            nohit += 1
            continue
        if got == r["released"]:
            agree += 1
            continue
        # 最初のJANが駿河屋に無いと、後年の再販に当たってしまう。
        # 駿河屋の日付が最古の版と一致するときだけ、その版の実際の発売日とみなす
        sure = (got == r["e0"])
        diff += 1
        out.append((r, got, sure))
        print("  %s %-28s %5d票  DB=%s / 駿河屋=%s（最古の版 %s）"
              % ("○" if sure else "?", r["title"][:28], r["votecount"] or 0,
                 r["released"], got, r["e0"]))

    print()
    print("=== 結果 ===")
    print("  DBと一致        %d件" % agree)
    print("  食い違い         %d件" % diff)
    print("  駿河屋に無い      %d件" % nohit)
    print()
    print("  ○ 駿河屋＝最古の版 … %d件  そのまま訂正に使える"
          % sum(1 for x in out if x[2]))
    print("  ? 別の版に当たった  … %d件  最初のJANが駿河屋に無く後年の再販を拾っている"
          % sum(1 for x in out if not x[2]))

    if a.queue and out:
        with open(QUEUE, "w", encoding="utf-8") as f:
            f.write("# 駿河屋（JAN一致）と食い違ったもの。\n")
            f.write("vid\ttitle\tdb_value\tsuruga_value\tconfidence\tsource_url\n")
            for r, got, sure in out:
                jan = con.execute(
                    """SELECT gtin FROM editions WHERE vid=? AND gtin<>'' AND is_dl=0
                       ORDER BY released LIMIT 1""", (r["vid"],)).fetchone()
                f.write("\t".join([r["vid"], r["title"], r["released"], got,
                                   "確度高" if sure else "別の版の可能性",
                                   SEARCH % (jan[0] if jan else "")]) + "\n")
        print("  → %s" % os.path.relpath(QUEUE, ROOT))


if __name__ == "__main__":
    main()
