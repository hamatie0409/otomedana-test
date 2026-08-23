# -*- coding: utf-8 -*-
"""発売元がほんとうに発売元かを VNDB に問い合わせて選り分ける（標準ライブラリのみ）。

    python3 scripts/producers.py            # 問い合わせて分類するだけ
    python3 scripts/producers.py --apply    # 発売元でないものをDBから外す

なにが問題だったか
------------------
VNDB の publishers は「世界中のあらゆるリリースの発売元」を全部並べる。
そこには有志の翻訳グループや個人が混ざっていて、日本の乙女ゲームのサイトに
出す「発売元」としては誤りになる。掲載637件のうち106件が4社以上を持ち、
発売元ページ134件の中には次のようなものが混じっていた。

    Flamecho 67作品 / Otomevn 18 / snow rowan 11 / iqdhk8t0hxww 9
    Наша версия 7 / 夜鸮汉化组 3 / 推倒美男游戏汉化组 5 ...

名前で見分けようとすると必ず外す（`MIZUBLUE GAMES` は日本の同人サークルで正しい
発売元、`대원미디어` は韓国の正規販売元）。VNDB は制作者ごとに言語と種別
（co=会社 / in=個人 / ng=同人・有志）を持っているので、それを引いて判断する。

判定
----
**日本語以外の言語で、かつ会社でないもの**を外す。

    Flamecho        zh / in  → 外す
    Otomevn         vi / ng  → 外す
    MIZUBLUE GAMES  ja / ng  → 残す（日本の同人サークル）
    대원미디어          ko / co  → 残す（韓国の正規販売元）
    Aksys Games     en / co  → 残す（海外の正規販売元）

VNDB に見つからない名前は**残す**。分からないものを消すほうが害が大きい。
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "vndb_otome.db")
API = "https://api.vndb.org/kana/producer"
UA = ("otomedana-test/1.0 (publisher classification; "
      "https://github.com/hamatie0409/otomedana-test)")
PAUSE = 0.4

SCHEMA = """
CREATE TABLE IF NOT EXISTS producers (
    name TEXT PRIMARY KEY,   -- DBに入っている表記そのまま
    pid TEXT,
    lang TEXT,               -- ja / en / zh / ru ...
    type TEXT,               -- co=会社 / in=個人 / ng=同人・有志
    vndb_name TEXT,
    fetched_at TEXT
);
"""


def is_publisher(lang, typ):
    """発売元として出してよいか。分からないものは True（残す）"""
    if not lang or not typ:
        return True
    return lang == "ja" or typ == "co"


def ask(name):
    body = {"filters": ["search", "=", name],
            "fields": "id,name,lang,type", "results": 5}
    req = urllib.request.Request(
        API, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA})
    res = json.load(urllib.request.urlopen(req, timeout=30)).get("results", [])
    # 表記ゆれがあるので完全一致を優先し、無ければ先頭を使う
    for p in res:
        if p["name"] == name:
            return p
    return res[0] if res else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="キャッシュを無視して引き直す")
    a = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("DBがありません: %s" % DB)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)

    names = set()
    for (p,) in con.execute("""SELECT g.publishers FROM games g
            JOIN slugs s ON s.key=g.vid AND s.kind='game' AND s.is_page=1
            WHERE g.publishers IS NOT NULL AND g.publishers <> ''"""):
        names.update(x.strip() for x in p.split("/") if x.strip())

    known = {r["name"]: r for r in con.execute("SELECT * FROM producers")}
    todo = sorted(names if a.refresh else (names - set(known)))
    print("発売元の名前 %d件 / 未取得 %d件" % (len(names), len(todo)))

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    for i, n in enumerate(todo, 1):
        try:
            p = ask(n)
        except Exception as e:
            print("  [%d/%d] %s … 失敗 %s" % (i, len(todo), n[:30], e), file=sys.stderr)
            continue
        con.execute("INSERT OR REPLACE INTO producers VALUES (?,?,?,?,?,?)",
                    (n, p["id"] if p else None, p["lang"] if p else None,
                     p["type"] if p else None, p["name"] if p else None, stamp))
        if i % 25 == 0:
            con.commit()
            print("  [%d/%d]" % (i, len(todo)))
        time.sleep(PAUSE)
    con.commit()

    rows = {r["name"]: r for r in con.execute("SELECT * FROM producers")}
    drop = sorted(n for n in names
                  if n in rows and not is_publisher(rows[n]["lang"], rows[n]["type"]))
    unknown = sorted(n for n in names if n not in rows or not rows[n]["lang"])
    print()
    print("=== 分類 ===")
    print("  発売元として出す   %d件" % (len(names) - len(drop)))
    print("  外す（日本語以外・会社でない） %d件" % len(drop))
    print("  VNDBに見つからず（残す）    %d件" % len(unknown))
    print()
    for n in drop[:25]:
        r = rows[n]
        print("  外す  %-30s %s / %s" % (n[:30], r["lang"], r["type"]))
    if len(drop) > 25:
        print("  ほか %d件" % (len(drop) - 25))

    if not a.apply:
        print()
        print("  DBに反映するには --apply")
        return

    n_g = n_s = 0
    for r in con.execute("""SELECT g.vid, g.publishers FROM games g
            JOIN slugs s ON s.key=g.vid AND s.kind='game' AND s.is_page=1
            WHERE g.publishers IS NOT NULL AND g.publishers <> ''""").fetchall():
        keep = [x.strip() for x in r["publishers"].split("/")
                if x.strip() and x.strip() not in drop]
        new = " / ".join(keep)
        if new != r["publishers"]:
            con.execute("UPDATE games SET publishers=? WHERE vid=?", (new, r["vid"]))
            n_g += 1
    # 発売元ページも消す。実在しない発売元のページが立っていた
    for n in drop:
        n_s += con.execute("DELETE FROM slugs WHERE kind='publisher' AND label=?",
                           (n,)).rowcount
    con.commit()
    print()
    print("  作品の発売元を書き換え: %d件 / 発売元ページを削除: %d件" % (n_g, n_s))


if __name__ == "__main__":
    main()
