# -*- coding: utf-8 -*-
"""【商品ID取得】版ごとの Amazon の ASIN を取り込む。

  python3 scripts/amazon_asin.py --export   # 未取得の版をCSVに書き出す
  python3 scripts/amazon_asin.py --import data/asin.csv
  python3 scripts/amazon_asin.py --report

なぜ別のスクリプトなのか
------------------------
Amazon の価格を表示するには PA-API を通す必要があり（アソシエイトの規約上、
PA-API 以外で取った価格は載せられない）、その PA-API はアソシエイト審査に
通って売上が立つまで使えない。開設直後のサイトでは原理的に価格が出せない。

一方で **ASIN さえ分かれば商品直リンクは作れる**（PA-API は不要）。
検索リンクより確実で、通常版と限定版を取り違えない。
そこでこのスクリプトは「ASINの器」だけを先に用意しておく。

  ・いまは CSV で受け取る（手で埋める / 他の手段で集めたものを流し込む）
  ・PA-API が通ったら、GetItems を JAN(EAN) で引く処理をここに足せばいい。
    書き込み先（editions.asin）と、その後の流れ（offers.py 再実行）は同じ

CSVの形式（1行目は見出し。eid か gtin のどちらかがあればいい）:

  eid,gtin,asin
  v1715-r56825-swi,4995857095643,B07CQKZ8NN
"""
import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA

DB = os.path.join(DATA, "vndb_otome.db")
HOME = ("Switch", "PS", "ニンテンドー", "Xbox")
SCOPE = " OR ".join("platforms LIKE '%%%s%%'" % k for k in HOME)


def export(con, path):
    """ASINがまだ無い版を、価値の高い順にCSVへ書き出す"""
    rows = con.execute("""
        SELECT e.eid, e.gtin, g.title, e.platform_ja, e.edition_label, e.released
        FROM editions e JOIN games g ON g.vid = e.vid
        WHERE e.asin = '' AND e.gtin <> '' AND e.vid IN (SELECT vid FROM games WHERE %s)
        ORDER BY e.released DESC
    """ % SCOPE).fetchall()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["eid", "gtin", "asin", "作品名", "機種", "版", "発売日"])
        for eid, gtin, title, plat, ed, rel in rows:
            w.writerow([eid, gtin, "", title, plat, ed or "通常版", rel])
    print("%d行を書き出した -> %s" % (len(rows), path))
    print("asin の列を埋めて --import で読み込む")


def load(con, path):
    n = skip = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            asin = (row.get("asin") or "").strip().upper()
            if not asin:
                continue
            if len(asin) != 10 or not asin.isalnum():
                print("  ASINの形式が違うので飛ばした: %r" % asin)
                skip += 1
                continue
            eid, gtin = (row.get("eid") or "").strip(), (row.get("gtin") or "").strip()
            if eid:
                cur = con.execute("UPDATE editions SET asin=? WHERE eid=?", (asin, eid))
            elif gtin:
                cur = con.execute("UPDATE editions SET asin=? WHERE gtin=?", (asin, gtin))
            else:
                continue
            n += cur.rowcount
    con.commit()
    print("editions.asin を %d行 更新（形式不正で飛ばした %d件）" % (n, skip))
    print("反映するには: python3 scripts/offers.py && python3 scripts/site_build.py")


def report(con):
    q = lambda s: con.execute(s).fetchone()[0]
    tot = q("""SELECT COUNT(*) FROM editions WHERE gtin<>''
               AND vid IN (SELECT vid FROM games WHERE %s)""" % SCOPE)
    got = q("""SELECT COUNT(*) FROM editions WHERE gtin<>'' AND asin<>''
               AND vid IN (SELECT vid FROM games WHERE %s)""" % SCOPE)
    print("掲載対象でJANのある版 %d件 / ASINあり %d件 (%.0f%%)"
          % (tot, got, 100.0 * got / tot if tot else 0))
    n_item = q("SELECT COUNT(*) FROM offers WHERE channel='Amazon' AND link_type='item'")
    print("Amazonが商品直リンクになっている行: %d" % n_item)
    if not got:
        print()
        print("※ ASINはまだ1件も入っていません。Amazonは当面JAN検索のリンクです。")
        print("  PA-APIはアソシエイト審査（売上実績）を通るまで使えないため、")
        print("  価格の表示も当面できません。")


def main():
    argv = sys.argv[1:]
    con = sqlite3.connect(DB)
    if "--export" in argv:
        i = argv.index("--export")
        path = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("-") \
            else os.path.join(DATA, "asin.csv")
        export(con, path)
    elif "--import" in argv:
        load(con, argv[argv.index("--import") + 1])
    else:
        report(con)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
