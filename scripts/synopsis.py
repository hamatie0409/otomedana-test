# -*- coding: utf-8 -*-
"""日本語のあらすじを用意するための道具（標準ライブラリのみ）。

    python3 scripts/synopsis.py --gather --limit 5   # 材料を集める
    python3 scripts/synopsis.py --check              # 書いたものが元文と重なっていないか

なぜ必要か
----------
掲載637件のうち、説明文に日本語が入っているのは4件だけ。残り633件は英語のまま。
日本語の乙女ゲームのサイトとしては大きな欠落なので埋めたい。

書き写してはいけない
--------------------
公式サイトのあらすじには著作権がある。あらすじ欄を丸ごと引用で埋めると
主従関係が逆転し、著作権法32条の引用の要件を満たさない。日本語Wikipediaは
CC BY-SA なので転載できるが、継承義務が発生してサイト全体のライセンス表示が
面倒になる（こちらは ODbL のデータを扱っている）。

**事実や筋書きそのものに著作権は及ばない。** 読んで理解した内容を自分の表現で
書けば問題ない。ただし「要約して」と指示すると元の言い回しを縮めた文が出て、
翻案とみなされうる。そこで、書いたものが材料の文とどれだけ重なっているかを
機械で測り、長い一致が残っていれば弾く。

--check がやること
------------------
corrections/works.csv の description_ja を、集めた材料と突き合わせる。
  - いちばん長い共通部分文字列が LCS_MAX 文字以上 → 落とす
  - 文字8-gramの重なりが GRAM_MAX 以上      → 落とす
固有名詞や作品名は当然一致するので、短い一致は通す。
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "vndb_otome.db")
OUT = os.path.join(ROOT, "data", "synopsis")     # data/ は git 管理外。材料の置き場
CSV_PATH = os.path.join(ROOT, "corrections", "works.csv")

LCS_MAX = 25      # これ以上続けて一致したら書き写しとみなす
GRAM_MAX = 0.12   # 文字8-gramの重なりの上限
N = 8

API = "https://ja.wikipedia.org/w/api.php"
UA = ("otomedana-test/1.0 (synopsis sourcing; "
      "https://github.com/hamatie0409/otomedana-test)")


def wiki_extract(title):
    """記事の導入とあらすじ節を平文で取る"""
    q = urllib.parse.urlencode({
        "action": "query", "prop": "extracts", "explaintext": "1",
        "format": "json", "formatversion": "2", "redirects": "1", "titles": title})
    req = urllib.request.Request(API + "?" + q, headers={"User-Agent": UA})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    pages = d.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return ""
    return pages[0].get("extract", "")


def grams(s, n=N):
    s = re.sub(r"\s+", "", s or "")
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}


def lcs_len(a, b):
    """いちばん長い共通部分文字列の長さ。短い文どうしなので素朴な動的計画法でよい"""
    a = re.sub(r"\s+", "", a or "")
    b = re.sub(r"\s+", "", b or "")
    if not a or not b:
        return 0, ""
    prev = [0] * (len(b) + 1)
    best, end = 0, 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best, end = cur[j], i
        prev = cur
    return best, a[end - best:end]


def gather(con, limit):
    os.makedirs(OUT, exist_ok=True)
    rows = con.execute("""
        SELECT g.vid, g.title, g.description, g.jawiki_url, g.votecount
        FROM games g JOIN slugs s ON s.key=g.vid AND s.kind='game' AND s.is_page=1
        WHERE (g.description_ja IS NULL OR g.description_ja='')
        ORDER BY g.votecount DESC""").fetchall()
    if limit:
        rows = rows[:limit]
    print("材料を集めます: %d件" % len(rows))
    for r in rows:
        wiki = ""
        if r["jawiki_url"]:
            title = urllib.parse.unquote(
                r["jawiki_url"].rstrip("/").rsplit("/", 1)[-1]).replace("_", " ")
            try:
                wiki = wiki_extract(title)
            except Exception as e:
                print("  %s の記事が取れず: %s" % (r["title"][:24], e), file=sys.stderr)
            time.sleep(1.0)
        links = [x[0] for x in con.execute(
            "SELECT DISTINCT url FROM vndb_links WHERE vid=? AND site='website'",
            (r["vid"],))]
        path = os.path.join(OUT, "%s.json" % r["vid"])
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"vid": r["vid"], "title": r["title"],
                       "votecount": r["votecount"],
                       "vndb_description": r["description"] or "",
                       "jawiki_url": r["jawiki_url"] or "",
                       "jawiki_text": wiki, "websites": links},
                      f, ensure_ascii=False, indent=1)
        print("  %-34s wiki %5d字 / 公式候補 %d件"
              % (r["title"][:34], len(wiki), len(links)))
    print()
    print("  置き場: %s" % os.path.relpath(OUT, ROOT))


def sources_of(vid):
    path = os.path.join(OUT, "%s.json" % vid)
    if not os.path.exists(path):
        return None
    d = json.load(open(path, encoding="utf-8"))
    return d, [("VNDBの説明文", d.get("vndb_description", "")),
               ("Wikipedia", d.get("jawiki_text", ""))]


def check():
    import csv as csvmod
    if not os.path.exists(CSV_PATH):
        sys.exit("訂正ファイルがありません")
    with open(CSV_PATH, encoding="utf-8") as f:
        lines = [l for l in f if l.strip() and not l.lstrip().startswith("#")]
    rows = [r for r in csvmod.DictReader(lines)
            if (r.get("field") or "").strip() == "description_ja"]
    print("あらすじ %d件を検査します" % len(rows))
    print("  基準: 連続一致 %d文字未満 / 8-gramの重なり %.0f%%未満"
          % (LCS_MAX, GRAM_MAX * 100))
    print()
    ng = 0
    for r in rows:
        vid = r["vid"].strip()
        text = r["value"].strip()
        got = sources_of(vid)
        if not got:
            print("  ? %s … 材料が無い（--gather を先に）" % vid)
            continue
        d, srcs = got
        worst_n, worst_s, worst_who, worst_frag = 0, 0.0, "", ""
        tg = grams(text)
        for who, src in srcs:
            if not src:
                continue
            n, frag = lcs_len(text, src)
            sg = grams(src)
            share = len(tg & sg) / float(len(tg)) if tg else 0.0
            if n > worst_n:
                worst_n, worst_who, worst_frag = n, who, frag
            worst_s = max(worst_s, share)
        bad = worst_n >= LCS_MAX or worst_s > GRAM_MAX
        ng += bad
        print("  %s %-28s 連続一致 %2d文字 / 重なり %4.1f%%  %s"
              % ("×" if bad else "○", d["title"][:28], worst_n, worst_s * 100,
                 ("← %s と「%s」" % (worst_who, worst_frag[:24])) if bad else ""))
    print()
    print("  落ちたもの %d件" % ng)
    return 1 if ng else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gather", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    if not os.path.exists(DB):
        sys.exit("DBがありません: %s" % DB)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    have = {d[1] for d in con.execute("PRAGMA table_info(games)")}
    if "description_ja" not in have:
        con.execute("ALTER TABLE games ADD COLUMN description_ja TEXT")
    if a.gather:
        gather(con, a.limit)
    if a.check:
        sys.exit(check())
    if not (a.gather or a.check):
        ap.print_help()


if __name__ == "__main__":
    main()
