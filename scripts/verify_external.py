# -*- coding: utf-8 -*-
"""DBの作品情報を外部と突き合わせて、食い違うものだけを出す（標準ライブラリのみ）。

    python3 scripts/verify_external.py              # 日本語Wikipediaと照合
    python3 scripts/verify_external.py --limit 50   # 件数を絞る（得票の多い順）
    python3 scripts/verify_external.py --queue      # 要確認リストをファイルに書き出す

なぜ機械照合を先にやるのか
--------------------------
掲載637件を全部Webで調べるのは現実的でない。内部整合（JANのチェックディジット、
版と作品の対応、slugの重複）は audit.py で全数調べて違反ゼロだった。
残る誤りは外部と照らさないと分からない種類なので、機械で取れる範囲を先に当てて
「外部と食い違う作品」だけを人の目に回す。

**ここでは自動訂正しない。** Wikipediaも間違えるし、発売日は「フリー版」「完全版」
「移植版」で複数あって、どれを作品の発売日とすべきかは人が決めることだから。
出た差分は corrections/_queue.tsv に落とし、人が出典を見て
corrections/works.csv に移す。
"""
import argparse
import datetime
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
QUEUE = os.path.join(ROOT, "corrections", "_queue.tsv")

API = "https://ja.wikipedia.org/w/api.php"
# Wikimedia は連絡先の分かる User-Agent を求めている
UA = ("otomedana-test/1.0 (data verification; "
      "https://github.com/hamatie0409/otomedana-test)")
BATCH = 50          # titles= にまとめて渡せる上限
PAUSE = 1.0         # 連続リクエストの間隔（秒）

JA_DATE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")


def wiki_title(url):
    """記事URLからAPI用のタイトルに戻す"""
    return urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[-1]).replace("_", " ")


def clean(s):
    """ウィキテキストの飾りを落として素のテキストにする"""
    s = re.sub(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", "", s, flags=re.S)
    s = re.sub(r"\{\{[^{}]*\}\}", " ", s)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]", r"\1", s)
    s = re.sub(r"</?[^>]+>", " ", s)
    s = re.sub(r"'{2,}", "", s)
    return re.sub(r"\s+", " ", s).strip()


# 日本語版の記事は、日本語の欄名（発売日・発売元）と英語の欄名（Date・Pub）が
# 混在している。{{Infobox Video game}} を英語版から持ってきた記事が多いため。
ALIAS = {
    "発売日":   ("発売日", "Date", "Released"),
    "発売元":   ("発売元", "Pub", "Publisher"),
    "開発元":   ("開発元", "Dev", "Developer"),
    "対応機種": ("対応機種", "Plat", "Platforms"),
}


def field(txt, name):
    """インフォボックスの1項目を取る。次の | か }} まで。欄名の別名も試す"""
    for key in ALIAS.get(name, (name,)):
        m = re.search(r"\|\s*%s\s*=\s*(.*?)(?=\n\s*\||\n\}\})" % re.escape(key),
                      txt, re.S)
        if m and clean(m.group(1)):
            return clean(m.group(1))
    return ""


def fetch(titles):
    """記事の本文をまとめて取る。{タイトル: 本文}"""
    out = {}
    for i in range(0, len(titles), BATCH):
        chunk = titles[i:i + BATCH]
        q = urllib.parse.urlencode({
            "action": "query", "prop": "revisions", "rvprop": "content",
            "rvslots": "main", "format": "json", "formatversion": "2",
            "redirects": "1", "titles": "|".join(chunk)})
        req = urllib.request.Request(API + "?" + q, headers={"User-Agent": UA})
        try:
            d = json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:                      # 通信は落ちうる。落ちても続ける
            print("  取得に失敗（%d件目〜）: %s" % (i + 1, e), file=sys.stderr)
            continue
        # リダイレクトで別名になることがあるので、元の名前に戻せるようにする
        back = {r["to"]: r["from"] for r in d.get("query", {}).get("redirects", [])}
        for p in d.get("query", {}).get("pages", []):
            if p.get("missing"):
                continue
            body = p["revisions"][0]["slots"]["main"]["content"]
            name = p.get("title")
            out[name] = body
            if name in back:
                out[back[name]] = body
        time.sleep(PAUSE)
    return out


def first_date(s):
    m = JA_DATE.search(s or "")
    return "%04d-%02d-%02d" % tuple(int(x) for x in m.groups()) if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--queue", action="store_true", help="要確認リストを書き出す")
    ap.add_argument("--publishers", action="store_true",
                    help="発売元も比べる（粒度が違うので差分だらけになる）")
    a = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("DBがありません: %s" % DB)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT g.vid, g.title, g.released, g.publishers, g.developers, g.votecount,
               g.jawiki_url
        FROM games g JOIN slugs s ON s.key=g.vid AND s.kind='game' AND s.is_page=1
        WHERE g.jawiki_url IS NOT NULL AND g.jawiki_url <> ''
        ORDER BY g.votecount DESC""").fetchall()
    if a.limit:
        rows = rows[:a.limit]

    print("日本語Wikipediaと照合します: %d件" % len(rows))
    pages = fetch([wiki_title(r["jawiki_url"]) for r in rows])
    print("  本文を取れた記事: %d件" % len(pages))
    print()

    found = []
    n_ok = n_nopage = n_nofield = 0
    for r in rows:
        txt = pages.get(wiki_title(r["jawiki_url"]))
        if not txt:
            n_nopage += 1
            continue
        w_date = first_date(field(txt, "発売日"))
        w_pub = field(txt, "発売元")
        if not w_date:
            n_nofield += 1
        diffs = []
        # 発売日。Wikipedia側は版ごとに複数あるので、いちばん古いものと比べる
        if w_date and r["released"] and w_date != r["released"]:
            diffs.append(("released", r["released"], w_date))
        # 発売元は既定では比べない。Wikipedia はブランド名（オトメイト）を書き、
        # VNDB は会社名（アイディアファクトリー株式会社）を持つ。さらに VNDB には
        # 海外の正規販売元も入る。比べているものが違うので差分がほぼ全件出てしまい、
        # 見るべきものが埋もれる。発売元の掃除は producers.py（VNDBの制作者情報）で
        # 別にやってある。
        if a.publishers and w_pub and r["publishers"]:
            a_, b_ = r["publishers"], w_pub
            if a_ not in b_ and b_ not in a_ and a_.split("/")[0].strip() not in b_:
                diffs.append(("publishers", a_, b_[:60]))
        if diffs:
            found.append((r, diffs))
        else:
            n_ok += 1

    print("=== 結果 ===")
    print("  一致            %d件" % n_ok)
    print("  食い違いあり      %d件  ← 人が見る" % len(found))
    print("  記事が取れなかった %d件" % n_nopage)
    print("  発売日欄が無い    %d件" % n_nofield)
    print()
    for r, diffs in found[:30]:
        print("  %-32s %5d票  %s" % (r["title"][:32], r["votecount"] or 0, r["jawiki_url"]))
        for f, mine, theirs in diffs:
            print("      %-11s DB=%s / Wikipedia=%s" % (f, mine, theirs))
    if len(found) > 30:
        print("  ほか %d件" % (len(found) - 30))

    if a.queue:
        os.makedirs(os.path.dirname(QUEUE), exist_ok=True)
        with open(QUEUE, "w", encoding="utf-8") as f:
            f.write("# 外部照合で食い違ったもの。人が出典を見て判断し、\n")
            f.write("# 正しいほうを corrections/works.csv に移すこと。\n")
            f.write("# ここは機械が上書きするので、直接書き込んでも消える。\n")
            f.write("vid\ttitle\tfield\tdb_value\twikipedia_value\tsource_url\n")
            for r, diffs in found:
                for fl, mine, theirs in diffs:
                    f.write("\t".join([r["vid"], r["title"], fl, mine or "",
                                       theirs or "", r["jawiki_url"]]) + "\n")
        print()
        print("  要確認リスト: %s（%d行）"
              % (os.path.relpath(QUEUE, ROOT), sum(len(d) for _, d in found)))


if __name__ == "__main__":
    main()
