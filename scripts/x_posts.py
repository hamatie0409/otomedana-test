# -*- coding: utf-8 -*-
"""キャラクター紹介のXポストを集めて検証する（標準ライブラリのみ）。

    python3 scripts/x_posts.py verify      # 候補を oEmbed で検証して確定ファイルへ
    python3 scripts/x_posts.py report      # 収集状況を出す
    python3 scripts/x_posts.py queue       # まだ埋まっていない作品を優先順に出す

なぜ「埋め込みコード」ではなく URL だけを集めるのか
--------------------------------------------------
X の埋め込みHTMLは publish.x.com/oembed から認証なしで取れる。
人間（や検索）が集めるのは投稿URL 1個でよく、HTMLの整形・多言語・dnt指定は
ビルド時にこちらで決められる。あとから表示方法を変えたくなっても再収集がいらない。

oEmbed が検証にそのまま使える理由
--------------------------------
  * 削除・非公開の投稿は 404 を返す → 死活監視ができる
  * URL のアカウント名が違っていても、実際の投稿者を author_url で返してくる
    → 「公式アカウントの投稿か」を機械で照合できる。ショップやファンの投稿を
      間違って拾っても、ここで落ちる

ファイルの役割
--------------
  corrections/x_accounts.tsv  作品 → 公式アカウント。これが照合の基準になる
  corrections/_queue_x.tsv    検索で拾った候補。ここは間違いが混ざっていてよい
  corrections/x_posts.tsv     検証を通った確定分。サイトが読むのはこれだけ

`data/` は git 管理外で作り直されるので、収集結果は corrections/ に置く。
corrections/works.csv と同じで、値・出典・確認日が揃わないものは通さない。
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    """data/ を探す。worktree には data/ が無いので本体の作業ツリーも見る。"""
    here = os.path.join(ROOT, "data")
    if os.path.isdir(here):
        return here
    # .git がファイル（worktree）なら、そこに書かれた本体を辿る
    dotgit = os.path.join(ROOT, ".git")
    if os.path.isfile(dotgit):
        with open(dotgit, encoding="utf-8") as f:
            gitdir = os.path.abspath(f.read().split(":", 1)[1].strip())
        # gitdir は <本体>/.git/worktrees/<名前>。".git" まで遡って親を取る
        while gitdir != os.path.dirname(gitdir):
            if os.path.basename(gitdir) == ".git":
                cand = os.path.join(os.path.dirname(gitdir), "data")
                return cand if os.path.isdir(cand) else None
            gitdir = os.path.dirname(gitdir)
    sys.exit("data/ が見つかりません")


DATA = data_dir()
DB = os.path.join(DATA, "site2.db")
CACHE = os.path.join(DATA, "cache", "x_oembed")
CORR = os.path.join(ROOT, "corrections")
ACCOUNTS = os.path.join(CORR, "x_accounts.tsv")
QUEUE = os.path.join(CORR, "_queue_x.tsv")
POSTS = os.path.join(CORR, "x_posts.tsv")

OEMBED = "https://publish.x.com/oembed"
STATUS = re.compile(r"^https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d+)")
TAG = re.compile(r"<[^>]+>")
DELAY = 1.2


def read_tsv(path):
    """# で始まる行と空行を飛ばして dict のリストで返す"""
    if not os.path.exists(path):
        return []
    rows, head = [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cells = line.split("\t")
            if head is None:
                head = cells
                continue
            rows.append(dict(zip(head, cells + [""] * (len(head) - len(cells)))))
    return rows


def write_tsv(path, head, rows, preamble=""):
    with open(path, "w", encoding="utf-8") as f:
        if preamble:
            f.write(preamble)
        f.write("\t".join(head) + "\n")
        for r in rows:
            f.write("\t".join((r.get(k) or "").replace("\t", " ") for k in head) + "\n")


def parse_status(url):
    """投稿URLを (アカウント, ID) に割る。表記ゆれ・クエリは落とす。"""
    m = STATUS.match((url or "").strip())
    return (m.group(1), m.group(2)) if m else (None, None)


def oembed(status_id, account="i"):
    """oEmbed を引く。結果はIDごとにキャッシュする（同じ投稿を二度叩かない）。

    戻り値: dict（本文つき）／ None（404＝削除済み・非公開）
    """
    os.makedirs(CACHE, exist_ok=True)
    cache = os.path.join(CACHE, "%s.json" % status_id)
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            d = json.load(f)
        return None if d.get("_dead") else d

    url = "%s?%s" % (OEMBED, urllib.parse.urlencode({
        "url": "https://x.com/%s/status/%s" % (account, status_id),
        "omit_script": 1, "hide_thread": 1, "lang": "ja", "dnt": "true",
    }))
    time.sleep(DELAY)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            with open(cache, "w", encoding="utf-8") as f:
                json.dump({"_dead": True, "_code": e.code}, f)
            return None
        raise
    d["_text"] = TAG.sub("", d.get("html", ""))
    d["_handle"] = (d.get("author_url", "").rstrip("/").rsplit("/", 1)[-1])
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    return d


def name_hit(text, name):
    """本文にキャラ名が出るか。姓名の区切りの空白や中黒は無視して見る。"""
    flat = re.sub(r"[\s・･]", "", text)
    n = re.sub(r"[\s・･]", "", name or "")
    if n and n in flat:
        return "full"
    # 「ダンテ・ファルツォーネ」を「ダンテ」だけで呼ぶ投稿は多い
    parts = [p for p in re.split(r"[\s・･]", name or "") if len(p) >= 2]
    if parts and any(p in flat for p in parts):
        return "part"
    return ""


def cmd_peek(args):
    """URLを渡すと投稿者と本文を出す。

    検索は本文なしの結果をよく返すので、拾うかどうかをここで決める。
    候補をキューに入れる前の一次選別に使う。
    """
    for url in args.urls:
        acct, sid = parse_status(url)
        if not sid:
            print("×  URLとして読めない: %s" % url)
            continue
        d = oembed(sid, acct or "i")
        if d is None:
            print("×  削除済み・非公開: %s" % url)
            continue
        print("○  @%s  %s" % (d["_handle"], d.get("author_name", "")))
        print("   %s" % d["_text"].strip().replace("\n", " ")[:200])
        print("   https://x.com/%s/status/%s" % (d["_handle"], sid))


# 紹介ポストらしさの手がかり。あればスコアを上げる
INTRO_HINT = re.compile(r"キャラクター紹介|攻略キャラ|キャラ紹介|CHARACTER|"
                        r"CV[\.．:：]|V\.A\.|年齢[：:]|誕生日[：:]|身長[：:]")
# 紹介ではないと分かる語。あれば落とす（物販・イベント・抽選の告知が大半）
NOISE = re.compile(r"入荷|通販|買取|予約受付|発売日：|価格：|抽選|フェア|"
                   r"キャンペーン|チケット|イベント出展|ラジオ|第\d+回")


def cmd_harvest(args):
    """作品IDと検索で拾ったURL群を渡すと、本文を取ってキャラに自動で割り当てる。

    作品単位で検索したほうが検索回数がずっと少なくて済む。公式アカウントは
    キャラ紹介を連投するので、1回の検索で複数キャラ分のURLが返ってくる。
    本文にどのキャラ名が出るかはこちらで判定できるので、人が振り分けなくてよい。

    紹介ポストではない告知（物販・ラジオ・イベント）は NOISE で落とす。
    ここで拾いすぎると、キャラページにアクリルスタンドの発売告知が並ぶことになる。
    """
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    chars = [dict(r) for r in con.execute(
        "select cid, name from character where main_vid = ?", (args.vid,))]
    if not chars:
        sys.exit("この作品のキャラが見つかりません: %s" % args.vid)
    official = {r["vid"]: r["account"].lstrip("@") for r in read_tsv(ACCOUNTS) if r.get("account")}
    want = official.get(args.vid)

    rows, skipped = [], []
    for url in args.urls:
        acct, sid = parse_status(url)
        if not sid:
            skipped.append((url, "URLとして読めない"))
            continue
        d = oembed(sid, acct or "i")
        if d is None:
            skipped.append((url, "削除済み・非公開"))
            continue
        handle, text = d["_handle"], d["_text"]
        if want and handle.lower() != want.lower():
            skipped.append((url, "@%s は公式(@%s)ではない" % (handle, want)))
            continue
        if NOISE.search(text) and not INTRO_HINT.search(text):
            skipped.append((url, "紹介ではない告知"))
            continue
        hits = [c for c in chars if name_hit(text, c["name"]) == "full"]
        if not hits:
            hits = [c for c in chars if name_hit(text, c["name"]) == "part"]
        if not hits:
            skipped.append((url, "本文にこの作品のキャラ名なし"))
            continue
        if len(hits) > args.max_chars:
            # 全キャラを羅列しただけの告知はキャラ紹介ではない
            skipped.append((url, "%d人が並ぶ一覧的な投稿" % len(hits)))
            continue
        for c in hits:
            rows.append({"cid": c["cid"], "vid": args.vid, "character": c["name"],
                         "status_url": "https://x.com/%s/status/%s" % (handle, sid),
                         "note": ("紹介らしい" if INTRO_HINT.search(text) else "要確認")
                                 + " ｜ " + text.strip().replace("\n", " ")[:60]})

    head = ["cid", "vid", "character", "status_url", "note"]
    keep = {(r["cid"], r["status_url"]): r for r in read_tsv(QUEUE)}
    for r in rows:
        keep.setdefault((r["cid"], r["status_url"]), r)
    write_tsv(QUEUE, head, sorted(keep.values(), key=lambda r: (r["vid"], r["cid"])),
              preamble="# 検索で拾った候補。ここは間違いが混ざっていてよい。\n"
                       "# x_posts.py verify が oEmbed で実在・投稿者・本文を確かめ、\n"
                       "# 通ったものだけ x_posts.tsv に、怪しいものは _review_x.tsv に振り分ける。\n")
    got = sorted({r["character"] for r in rows})
    print("採用 %d件 → %d人: %s" % (len(rows), len(got), " / ".join(got)))
    for url, why in skipped:
        print("  除外 %s  (%s)" % (url.rsplit("/", 1)[-1], why))
    left = [c["name"] for c in chars if c["cid"] not in {r["cid"] for r in keep.values()}]
    if left:
        print("未収集 %d人: %s" % (len(left), " / ".join(left)))


def cmd_verify(args):
    chars = {}
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    for r in con.execute("select cid, name, main_vid, main_title from character"):
        chars[r["cid"]] = dict(r)
    official = {r["vid"]: r["account"].lstrip("@") for r in read_tsv(ACCOUNTS) if r.get("account")}

    today = datetime.date.today().isoformat()
    ok, review, dead, bad = [], [], [], []
    seen = set()
    for row in read_tsv(QUEUE):
        cid = row.get("cid", "").strip()
        url = row.get("status_url", "").strip()
        acct, sid = parse_status(url)
        ch = chars.get(cid)
        if not ch or not sid:
            bad.append((cid, url, "cid か URL が不正"))
            continue
        if (cid, sid) in seen:
            continue
        seen.add((cid, sid))

        d = oembed(sid, acct or "i")
        if d is None:
            dead.append((cid, ch["name"], url))
            continue

        handle = d["_handle"]
        text = d["_text"]
        want = official.get(row.get("vid") or ch["main_vid"] or "")
        hit = name_hit(text, ch["name"])
        reasons = []
        if want and handle.lower() != want.lower():
            reasons.append("@%s は公式(@%s)ではない" % (handle, want))
        if not want:
            reasons.append("公式アカウント未登録")
        if not hit:
            reasons.append("本文にキャラ名なし")
        elif hit == "part":
            reasons.append("名前が部分一致")

        rec = {"cid": cid, "vid": row.get("vid") or ch["main_vid"], "character": ch["name"],
               "account": "@" + handle, "status_url": "https://x.com/%s/status/%s" % (handle, sid),
               "checked_at": today,
               "note": (row.get("note") or "").strip() or text.strip().replace("\n", " ")[:80]}
        (ok if not reasons else review).append(
            rec if not reasons else dict(rec, note="要確認: " + " / ".join(reasons) + " ｜ " + rec["note"]))

    keep = {(r["cid"], r["status_url"]): r for r in read_tsv(POSTS)}
    for r in ok:
        keep[(r["cid"], r["status_url"])] = r
    head = ["cid", "vid", "character", "account", "status_url", "checked_at", "note"]
    write_tsv(POSTS, head, sorted(keep.values(), key=lambda r: (r["vid"], r["cid"])),
              preamble="# 検証を通ったキャラクター紹介ポスト。x_posts.py verify が書く。\n"
                       "# 公式アカウントの投稿で、本文にキャラ名が出るものだけが入る。\n")
    if review:
        write_tsv(os.path.join(CORR, "_review_x.tsv"), head, review,
                  preamble="# 自動では確定できなかった候補。人が見て x_posts.tsv へ移すか捨てる。\n")

    print("確定 %d / 要確認 %d / 削除済み %d / 不正 %d" % (len(ok), len(review), len(dead), len(bad)))
    for cid, name, url in dead[:10]:
        print("  削除済み: %s %s %s" % (cid, name, url))
    for cid, url, why in bad[:10]:
        print("  不正: %s %s %s" % (cid, url, why))


def cmd_report(args):
    con = sqlite3.connect(DB)
    got = {r["cid"] for r in read_tsv(POSTS)}
    tot = con.execute("select count(*) from character").fetchone()[0]
    rows = con.execute("""
        select case when w.year is null then 'unknown'
                    when w.year < 2012 then '~2011' else cast(w.year as text) end g,
               c.cid
        from character c left join work w on w.vid = c.main_vid""").fetchall()
    per = {}
    for g, cid in rows:
        n, k = per.get(g, (0, 0))
        per[g] = (n + 1, k + (1 if cid in got else 0))
    print("全キャラ %d / 収集済み %d (%.1f%%)" % (tot, len(got), 100.0 * len(got) / tot))
    print("%-8s %6s %6s" % ("発売年", "キャラ", "収集"))
    for g in sorted(per):
        n, k = per[g]
        print("%-8s %6d %6d" % (g, n, k))


def cmd_queue(args):
    """まだ埋まっていない作品を優先順に出す。次に何を検索すればいいかの一覧。"""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    got = {r["cid"] for r in read_tsv(POSTS)}
    accounts = {r["vid"]: r.get("account", "") for r in read_tsv(ACCOUNTS)}
    rows = con.execute("""
        select w.vid, w.title, w.year, w.brand, w.votecount,
               group_concat(c.cid) cids
        from work w join character c on c.main_vid = w.vid
        where w.year >= ?
        group by w.vid
        order by w.votecount desc, w.year desc""", (args.since,)).fetchall()
    print("%-7s %-4s %-5s %-5s %s" % ("vid", "年", "残", "全", "作品 / 公式アカウント"))
    n = 0
    for r in rows:
        cids = r["cids"].split(",")
        left = [c for c in cids if c not in got]
        if not left:
            continue
        n += 1
        if n > args.limit:
            break
        print("%-7s %-4s %-5d %-5d %s  %s" % (r["vid"], r["year"], len(left), len(cids),
                                              r["title"], accounts.get(r["vid"], "(未特定)")))
    print("\n未完了の作品: %d件" % sum(1 for r in rows if any(c not in got for c in r["cids"].split(","))))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    pk = sub.add_parser("peek")
    pk.add_argument("urls", nargs="+")
    pk.set_defaults(fn=cmd_peek)
    hv = sub.add_parser("harvest")
    hv.add_argument("vid")
    hv.add_argument("urls", nargs="+")
    hv.add_argument("--max-chars", type=int, default=3,
                    dest="max_chars", help="1投稿に何人まで載っていたら紹介とみなすか")
    hv.set_defaults(fn=cmd_harvest)
    q = sub.add_parser("queue")
    q.add_argument("--since", type=int, default=2012)
    q.add_argument("--limit", type=int, default=40)
    q.set_defaults(fn=cmd_queue)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
