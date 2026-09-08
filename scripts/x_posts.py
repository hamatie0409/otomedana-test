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
# 文中から拾う用。intake は書式を問わないテキストを読むので行頭に限定できない
SCAN = re.compile(r"https?://(?:www\.)?(?:x|twitter|mobile\.x)\.com/"
                  r"([A-Za-z0-9_]{1,15})/status/(\d+)")
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


def load_accounts():
    """作品 → 公式アカウント（小文字）の集合。

    1作品に複数あることがある。続編と共用、アニメ版の公式、キャラが喋る
    サブアカウントなど。account 列は空白かカンマ区切りで複数書ける。
    """
    out = {}
    for r in read_tsv(ACCOUNTS):
        handles = {h.lstrip("@").lower()
                   for h in re.split(r"[,\s]+", r.get("account", "")) if h.strip()}
        if handles:
            out.setdefault(r["vid"], set()).update(handles)
    return out


# アニメ版・舞台版の公式アカウント。公式ではあるが、このサイトが扱うのは
# ゲームなので、同じキャラの紹介ならゲーム公式のほうを採りたい。
# NORN9 は13人全員がアニメ公式 @norn9_anime からの取得になっていた。
DERIVED_ACCOUNT = re.compile(r"anime|_tv$|^tv_|tvanime|butai|stage", re.I)


def primary_accounts():
    """作品 → ゲーム公式アカウント（account 列の先頭。小文字）。

    account 列は空白区切りで複数書ける。**先頭をゲーム公式とする**
    のが約束で、アニメ版などはその後ろに並べる。load_accounts が
    集合を返して順序を捨てるので、順序が要るときはこちらを使う。
    """
    out = {}
    for r in read_tsv(ACCOUNTS):
        handles = [h.lstrip("@").lower()
                   for h in re.split(r"[,\s]+", r.get("account", "")) if h.strip()]
        if handles:
            out.setdefault(r["vid"], handles[0])
    return out


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


def name_variants(name):
    """DBのキャラ名から、投稿本文で使われそうな表記を作る。

    DBの名前は「天草 四郎 時貞 (アマクサ シロウ トキサダ)」のように
    読み仮名が括弧で付いていることがある。そのままでは本文と一致しないので、
    括弧の中と外を別々の表記として扱う。
    """
    name = (name or "").strip()
    kana = re.findall(r"[（(]([^）)]+)[）)]", name)
    base = re.sub(r"[（(][^）)]*[）)]", "", name).strip()
    out = []
    for v in [base] + kana:
        v = re.sub(r"[\s・･]", "", v)
        if len(v) >= 1:
            out.append(v)
    return out


# 1文字の名前（「楊」「袁」など）は、前後が漢字でないときだけ名前とみなす。
# そうしないと「楊枝」のような別の語に当たる
KANJI = r"\u4e00-\u9fff\u3005"


def occurs(flat, v):
    """本文に出てくるか。

    1文字の名前は漢字のときだけ認める。「ティレル・I・リスター」を分解した
    「I」のような1文字のラテン字は、短縮URL（t.co/ITE6…）にも当たってしまう。
    """
    if len(v) >= 2:
        return v in flat
    if not re.match(r"^[%s]$" % KANJI, v):
        return False
    return re.search(r"(?<![%s])%s(?![%s])" % (KANJI, re.escape(v), KANJI), flat) is not None


# 「名前（CV.○○」「名前 年齢：」のように、紹介文で名前の直後に来る目印
NEAR_INTRO = r"(?:[\s　]*[（(【\[]?\s*(?:CV|ＣＶ|V\.A\.|Cast|年齢|声))"


def latin_hit(text, latin, title=""):
    """ラテン表記での一致。誕生日ポストは「Joyeux anniversaire - Mathis -」のように
    日本語名を一切出さないことがある。DBの name_latin はほぼ全キャラに入っている
    ので、これを second chance として使う。

    姓だけ・名だけの表記も拾うが、3文字未満の語は一般語と衝突するので使わない。
    """
    latin = (latin or "").strip()
    if not latin:
        return ""
    title_l = (title or "").lower()
    for v in [latin] + latin.split():
        if len(v) < 3 or v.lower() in title_l:
            continue
        if re.search(r"(?<![A-Za-z])" + re.escape(v) + r"(?![A-Za-z])", text, re.I):
            return "full" if v == latin else "part"
    return ""


def name_hit(text, name, title="", latin=""):
    """本文にキャラ名が出るか。full / part / 空 を返す。

    作品名にキャラ名が含まれる場合（『オランピアソワレ』の「オランピア」など）は
    どの投稿にも名前が出てしまう。この場合だけは、名前の直後に CV や年齢といった
    紹介文の目印があるときしか採らない。そうしないと全投稿が主人公の紹介になる。
    """
    flat = re.sub(r"[\s・･]", "", text)
    flat_title = re.sub(r"[\s・･]", "", title or "")
    best = ""
    for v in name_variants(name):
        if not occurs(flat, v):
            continue
        if v in flat_title:
            # 作品名の一部。紹介の目印が続くときだけ認める
            if re.search(re.escape(v) + NEAR_INTRO, flat, re.I):
                return "full"
            continue
        return "full" if v == name_variants(name)[0] else "part"
    # 「ダンテ・ファルツォーネ」を「ダンテ」とだけ呼ぶ投稿は多い
    for v in name_variants(name):
        for part in re.split(r"[\s・･]", re.sub(r"[（(][^）)]*[）)]", "", name or "")):
            part = part.strip()
            if part and part not in flat_title and occurs(flat, part):
                best = "part"
    return best or latin_hit(text, latin, title)


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
                        r"CV[\.．:：]|V\.A\.|年齢[：:]|誕生日[：:]|身長[：:]", re.I)
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
    chars = [dict(r) for r in con.execute("""
        select cid, name, name_latin from character where main_vid = ?
        union
        select c.cid, c.name, c.name_latin from character c
          join character_work cw on cw.cid = c.cid where cw.vid = ?""",
        (args.vid, args.vid))]
    if not chars:
        sys.exit("この作品のキャラが見つかりません: %s" % args.vid)
    wtitle = (con.execute("select title from work where vid=?",
                                  (args.vid,)).fetchone() or [""])[0]
    want = load_accounts().get(args.vid)

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
        if want and handle.lower() not in want:
            skipped.append((url, "@%s は公式(%s)ではない"
                            % (handle, "/".join("@" + w for w in sorted(want)))))
            continue
        if NOISE.search(text) and not INTRO_HINT.search(text):
            skipped.append((url, "紹介ではない告知"))
            continue
        hits = [c for c in chars if name_hit(text, c["name"], wtitle, c["name_latin"]) == "full"]
        match = "full"
        if not hits:
            hits = [c for c in chars if name_hit(text, c["name"], wtitle, c["name_latin"]) == "part"]
            # 部分一致でも、その作品でただ1人に決まるなら曖昧さはない。
            # 誕生日ポストは「Joyeux anniversaire - Mathis -」のように
            # 姓を省くことが多く、これを落とすと取りこぼしが大きい
            match = "unique" if len(hits) == 1 else "part"
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
                         "match": match,
                         "note": ("紹介らしい" if INTRO_HINT.search(text) else "要確認")
                                 + " ｜ " + text.strip().replace("\n", " ")[:60]})

    head = ["cid", "vid", "character", "status_url", "match", "note"]
    keep = {(r["cid"], r["status_url"]): r for r in read_tsv(QUEUE)}
    for r in rows:
        # 取り直したときは新しい判定で上書きする。キューは作業用の置き場で、
        # 確定分は x_posts.tsv 側にある
        keep[(r["cid"], r["status_url"])] = r
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


def e(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cmd_plan(args):
    """作品IDを渡すと、未収集キャラぶんのX検索URLを出す。

    Googleは X の投稿を部分的にしか索引していない。X自身の検索なら
    `from:アカウント名 キャラ名` でほぼ確実に出るので、そちらを叩く。
    ブラウザでログインしている必要がある（自分のアカウントで見るだけ。
    大量に速く回すと制限がかかるので、作品単位で区切って進めること）。

    出てきたURLを harvest に渡せば、割り当てと検証はそのまま流れる。
    """
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    got = {r["cid"] for r in read_tsv(POSTS)}
    handles = sorted(load_accounts().get(args.vid, []))
    if not handles:
        sys.exit("公式アカウントが未登録です。先に corrections/x_accounts.tsv に足してください: %s"
                 % args.vid)
    rows = con.execute("select cid, name, name_latin from character where main_vid = ?",
                       (args.vid,)).fetchall()
    for r in rows:
        if r["cid"] in got:
            continue
        # 姓名のうち、投稿で呼ばれやすい方を1語だけ使う。フルネームだと
        # 表記ゆれで外れることが多い
        base = re.sub(r"[（(][^）)]*[）)]", "", r["name"]).strip()
        key = max(re.split(r"[\s・･]", base) or [base], key=len)
        q = ("from:%s %s %s" % (handles[0], key, args.word)).strip()
        print("%s\t%s\thttps://x.com/search?q=%s&f=live"
              % (r["cid"], r["name"], urllib.parse.quote(q)))


SITE = os.path.join(ROOT, "docs", "v2")

# ポストの種類。プロフィール型（年齢・CV・人物の説明が入った紹介）が
# データベースサイトには一番合う。誕生日ポストは描き下ろしイラストが付く
# 代わりに、その日の挨拶だけで人物の説明が無いことが多い。
# プロフィール型の目印。見出しラベルが無くても、年齢や身長のような
# 「人物のデータ」が入っていれば紹介ポストとみなす。
# 年齢は「年齢：18歳」と「年齢25歳／178cm」の両方の書き方があり、
# コロンを必須にしていたせいでスチームプリズンの本編紹介5件を
# 取りこぼしていた（誕生日ポストのほうが選ばれていた）。
# 「年齢???」のように伏せる作品もあるので数字以外も受ける。
KIND_PROFILE = re.compile(
    r"【\s*(?:Character|キャラクター紹介|攻略キャラクター紹介|キャラ紹介|登場人物情報|"
    r"新キャラクター紹介\S*|攻略キャラ\S*)\s*】"
    r"|Character\s*Profile"
    r"|年齢\s*[：:]?\s*[\d０-９?？]"
    r"|身長\s*[：:]?\s*[\d０-９]"
    r"|登場人物情報")
KIND_BIRTHDAY = re.compile(
    r"HAPPY\s*BIRTHDAY|Joyeux\s+anniversaire|Buon\s+compleanno|誕生祭|誕生日", re.I)
# 載せたい順。誕生日は最後にする。描き下ろしイラストが付くので見栄えはよいが、
# 「本日は◯◯の誕生日です」だけで人物の説明が無いことが多く、
# データベースサイトとしては情報量が最も少ない。
KIND_ORDER = ["プロフィール", "フルネーム", "その他", "誕生日"]


def has_full_name(text, name):
    """本文にキャラのフルネームが出ているか。

    姓だけ・愛称だけの言及（「本日はシンの日」）と、人物を紹介する体裁で
    フルネームを書いている投稿を分ける。後者は紹介ポストであることが多い。
    区切り（空白・中黒）は表記ゆれがあるので詰めて比べる。
    """
    if not name:
        return False
    flat = re.sub(r"[\s・･]", "", text)
    base = re.sub(r"[（(][^）)]*[）)]", "", name).strip()
    parts = [p for p in re.split(r"[\s・･]", base) if p]
    if len(parts) < 2:
        return False
    return re.sub(r"[\s・･]", "", base) in flat


def post_kind(text, name=""):
    """ポストの種類を決める。name を渡すとより正確になる。

    「【緋影】「僕以外はあり得ないだろう」（CV：石川界人）」のように、
    見出しラベルではなくキャラ名そのものを【】でくくる紹介形式がある。
    定型の語（キャラクター紹介など）が無いので、名前を知らないと判定できない。
    誕生日ポストは名前を単独で【】に入れないので、これで取り違えない。

    誕生日の判定を「フルネーム」より先に置く。誕生日ポストもフルネームを
    書くことが多いので、後に置くと誕生日が上位に紛れ込む。
    """
    if KIND_PROFILE.search(text):
        return "プロフィール"
    for v in name_variants(name):
        if ("【%s】" % v) in text and re.search(r"CV|ＣＶ|声優|V\.A\.", text, re.I):
            return "プロフィール"
    if KIND_BIRTHDAY.search(text):
        return "誕生日"
    if has_full_name(text, name):
        return "フルネーム"
    return "その他"


def best_posts():
    """キャラごとに1件だけ選ぶ。KIND_ORDER の順（誕生日は最後）。

    同じ種類なら本文が長いほうを採る。「本日は◯◯の誕生日です」だけの投稿より、
    人物の説明が入っているほうがページに載せる価値がある。
    """
    by_cid = {}
    for r in read_tsv(POSTS):
        by_cid.setdefault(r["cid"], []).append(r)
    primary = primary_accounts()

    def rank(r):
        # ゲーム公式を最優先にする。アニメ版の公式も「公式」ではあるが、
        # このサイトが載せているのはゲームなので、同じキャラの紹介が
        # 両方にあるならゲーム公式のほうを採る。
        acct = (r.get("account") or "").lstrip("@").lower()
        derived = 0 if acct == primary.get(r["vid"], acct) else 1
        return (derived, KIND_ORDER.index(r.get("kind") or "その他"),
                -len(r.get("note") or ""))

    return {cid: sorted(rows, key=rank)[0] for cid, rows in by_cid.items()}


EMBED_CSS = """
.xsec{margin-top:6px}
.xsec .sec-head{margin-top:0}
.x-embeds{display:flex;flex-wrap:wrap;gap:16px;margin-top:14px;align-items:flex-start}
.x-embeds .twitter-tweet{margin:0!important;flex:0 1 460px}
.x-note{font-size:12px;margin-top:12px}
/* 押すまで読み込まない版。先に場所を取っておかないと本文が飛ぶ */
.x-slot{flex:0 1 460px;min-height:300px;border:1px dashed #d8d2ca;border-radius:12px;
        display:flex;align-items:center;justify-content:center}
.x-slot button{font:inherit;padding:10px 18px;border-radius:999px;cursor:pointer;
        border:1px solid #d8d2ca;background:transparent}
/* 個別ページ：パッケージ写真の代わりに埋め込みを置く。
   既定の300px（内側244px）だとXの最小幅220pxすれすれで窮屈なので広げる */
.hero-x{grid-template-columns:380px minmax(0,1fr)}
.hero-x .hero-img{padding:22px}
.hero-x .hero-img .twitter-tweet{margin:0!important;width:100%!important}
.hero-x .hero-img .x-slot{min-height:260px;flex:1 1 auto}
.hero-cap{font-size:12px;margin-top:10px}
@media (max-width:900px){.hero-x{grid-template-columns:1fr}}
/* 作品ページ：全キャラを1投稿ずつ並べる */
.xgrid{display:grid;gap:22px;margin-top:16px;
       grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.xcard{min-width:0}
.xcard-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;
            padding-bottom:8px;margin-bottom:10px;border-bottom:1px solid #eae5df}
.xcard-head .nm{font-weight:700;font-size:15px}
.xcard-head .nm a{text-decoration:none}
.xcard-head .nm a:hover{text-decoration:underline}
.xcard-head .meta{font-size:12px;color:#7a736b}
.xcard-head .kind{font-size:11px;border:1px solid #e0dad2;border-radius:999px;
                  padding:1px 8px;color:#7a736b;margin-left:auto}
/* Xの埋め込みは読み込み時のコンテナ幅を見るので、カード幅に合わせる */
.xcard .twitter-tweet{margin:0!important;width:100%!important}
.xcard .x-slot{min-height:200px}
"""


def _cards(rows, lazy):
    """埋め込みHTMLの配列を返す。lazy なら押すまで読み込まない箱にする。"""
    out = []
    for r in rows:
        _, sid = parse_status(r["status_url"])
        d = oembed(sid, r["account"].lstrip("@"))
        if d:
            out.append(d["html"])
    if lazy:
        out = ['<div class="x-slot" data-html="%s"><button type="button">公式ポストを表示'
               '</button></div>' % e(h).replace('"', "&quot;") for h in out]
    return out


def _css():
    path = os.path.join(SITE, "assets", "style.css")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _wrap(html, css, lazy):
    """1枚で開ける形にする。CSSを埋め込み、widgets.js を足す。"""
    html = html.replace("</head>", "<style>%s\n%s</style></head>" % (css, EMBED_CSS), 1)
    if lazy:
        tail = ("<script>document.querySelectorAll('.x-slot button')"
                ".forEach(function(b){b.addEventListener('click',function(){"
                "var s=b.parentNode;s.outerHTML=s.dataset.html;"
                "if(window.twttr){twttr.widgets.load();return;}"
                "var j=document.createElement('script');"
                "j.src='https://platform.twitter.com/widgets.js';j.charset='utf-8';"
                "document.body.appendChild(j);});});</script>")
    else:
        tail = ('<script async src="https://platform.twitter.com/widgets.js" '
                'charset="utf-8"></script>')
    return html.replace("</body>", tail + "</body>", 1)


def cmd_preview(args):
    """確定したポストを埋め込んだ見本を作る。サイト本体はまだ触らない。

      preview c94428 c39730     キャラの個別ページ（1人1投稿）
      preview --work v29661     作品ページに全キャラを1投稿ずつ並べる
    """
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    best = best_posts()
    css = _css()
    os.makedirs(args.out, exist_ok=True)
    if args.work:
        return _preview_work(con, best, css, args)

    for cid in args.cids:
        ch = con.execute("""select cid, name, slug, main_title, main_url
                            from character where cid=?""", (cid,)).fetchone()
        if not ch:
            print("×  そんなキャラはいない: %s" % cid)
            continue
        src = os.path.join(SITE, "character", ch["slug"], "index.html")
        if not os.path.exists(src):
            print("×  ページが未生成: %s" % src)
            continue
        r = best.get(cid)
        if not r:
            print("・ ポストなし: %s" % ch["name"])
            continue
        cards = _cards([r], args.lazy)
        with open(src, encoding="utf-8") as f:
            html = f.read()

        if args.place == "hero":
            html = _put_in_hero(html, ch, r, cards)
        else:
            block = ('<section class="pad sec xsec">\n'
                     '<div class="sec-head"><h2>公式アカウントの紹介</h2>'
                     '<span class="text-muted" style="font-size:12px">%s の投稿（%s）'
                     '</span></div>\n<div class="x-embeds">%s</div>\n'
                     '<p class="text-muted x-note">X の公式埋め込みで表示しています。'
                     '投稿が削除されると表示も消えます。</p>\n</section>\n'
                     % (e(r["account"]), e(r["kind"]), "".join(cards)))
            anchor = '<section class="pad sec">'
            html = html.replace(anchor, block + anchor, 1) if anchor in html else html + block

        out = os.path.join(args.out, "%s.html" % ch["slug"])
        with open(out, "w", encoding="utf-8") as f:
            f.write(_wrap(html, css, args.lazy))
        print("○  %s（%s）  %s" % (ch["name"], r["kind"], out))


def _put_in_hero(html, ch, r, cards):
    """パッケージ写真の枠を埋め込みに差し替える。

    このページはVNDBのライセンス上キャラクター画像を出せず、代わりに代表作の
    パッケージを置いていた。公式ポストが手に入るならそちらのほうが「その人の
    ページ」らしくなるので、枠ごと入れ替える。見出しは付けない。

    代表作へのリンクは元の説明文に入っていたので、短くして残す。ここが
    キャラ→作品の動線になっている。ポストが無いキャラはパッケージのまま。
    """
    head = '<div class="hero-img">'
    i = html.find(head)
    if i < 0:
        return html
    j = html.find("</div>", i)
    if j < 0:
        return html
    cap = ('<p class="text-muted hero-cap">%s ／ <a href="%s">%s</a></p>'
           % (e(r["account"]), e(ch["main_url"] or "/"), e(ch["main_title"] or "")))
    html = html[:i] + head + "".join(cards) + cap + html[j:]
    return html.replace('<div class="hero hero-2">', '<div class="hero hero-2 hero-x">', 1)


def _preview_work(con, best, css, args):
    """作品ページに「全キャラ1投稿ずつ」の節を足す。

    既存のキャラクター一覧はそのまま残す。あれが作品→個別ページの動線なので、
    置き換えると導線が消える。こちらの見出しからも個別ページに飛べるようにして
    動線を2本にする。
    """
    w = con.execute("select vid, title, slug from work where vid=?", (args.work,)).fetchone()
    if not w:
        sys.exit("そんな作品はいない: %s" % args.work)
    src = os.path.join(SITE, "game", w["slug"], "index.html")
    if not os.path.exists(src):
        sys.exit("ページが未生成: %s" % src)

    rows = con.execute("""select cid, name, slug, cv, role_label, age
                          from character where main_vid=?
                          order by role_label desc, cid""", (args.work,)).fetchall()
    items, missing = [], []
    for ch in rows:
        r = best.get(ch["cid"])
        cards = _cards([r], args.lazy) if r else []
        if not cards:
            missing.append(ch["name"])
            continue
        meta = " / ".join(x for x in [ch["role_label"],
                                      ("CV. %s" % ch["cv"]) if ch["cv"] else "",
                                      ("%d歳" % ch["age"]) if ch["age"] else ""] if x)
        items.append(
            '<div class="xcard"><div class="xcard-head">'
            '<span class="nm"><a href="/otomedana-test/v2/character/%s/">%s</a></span>'
            '<span class="meta">%s</span><span class="kind">%s</span></div>%s</div>'
            % (e(ch["slug"]), e(ch["name"]), e(meta), e(r["kind"]), cards[0]))

    note = "全%d人中%d人ぶん。" % (len(rows), len(items))
    if missing:
        note += "未収集: " + "、".join(missing) + "。"
    block = ('<section class="pad sec xsec">\n'
             '<div class="sec-head" style="margin-top:0"><h2>公式アカウントの紹介</h2>'
             '<span class="text-muted" style="font-size:12px">名前を押すと個別ページへ</span>'
             '</div>\n<div class="xgrid">%s</div>\n'
             '<p class="text-muted x-note">%s X の公式埋め込みで表示しています。'
             '投稿が削除されると表示も消えます。</p>\n</section>\n'
             % ("".join(items), e(note)))

    with open(src, encoding="utf-8") as f:
        html = f.read()
    # キャラクター一覧の直後に差し込む
    k = html.find("キャラクター</h2>")
    j = html.find('<section class="pad', k) if k >= 0 else -1
    html = (html[:j] + block + html[j:]) if j > 0 else html.replace("</main>", block + "</main>", 1)
    out = os.path.join(args.out, "work-%s.html" % w["slug"])
    with open(out, "w", encoding="utf-8") as f:
        f.write(_wrap(html, css, args.lazy))
    print("○  %s  %d/%d人  %s" % (w["title"], len(items), len(rows), out))



def read_sheet_rows(path):
    """記入用ブック（xlsx / csv）を行ごとに読む。

    人が「このキャラの行」にURLを書いている以上、その割り当てが正解。
    本文から推測し直すと、キャラ名が出てこない投稿（主人公のアイコン配布や
    OPムービーの告知など）を機械が勝手に捨ててしまう。cid 列を信じる。

    戻り値: [(cid, url), ...]。ブックの形でなければ None。
    """
    rows = []
    if path.lower().endswith(".xlsx"):
        try:
            from openpyxl import load_workbook
        except ImportError:
            return None
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb["キャラ一覧"] if "キャラ一覧" in wb.sheetnames else wb[wb.sheetnames[0]]
        it = ws.iter_rows(values_only=True)
        head = [str(c or "") for c in next(it, [])]
        body = ([str(c) if c is not None else "" for c in r] for r in it)
    else:
        import csv as _csv
        with open(path, encoding="utf-8", errors="replace") as f:
            data = list(_csv.reader(f))
        if not data:
            return None
        head, body = data[0], data[1:]

    try:
        i_cid = head.index("cid")
    except ValueError:
        return None
    i_url = 0
    for n, h in enumerate(head):
        if "ポストURL" in h:
            i_url = n
            break
    for r in body:
        if len(r) <= max(i_cid, i_url):
            continue
        cid, cell = (r[i_cid] or "").strip(), (r[i_url] or "").strip()
        if not cid or not cell:
            continue
        for m in SCAN.finditer(cell):
            rows.append((cid, "https://x.com/%s/status/%s" % (m.group(1), m.group(2))))
    return rows


def cmd_intake(args):
    """URLを並べただけのテキストを読み込む。cid も vid も書かなくてよい。

    集める人にやってほしいのは「投稿を開いてURLをコピーする」だけにしたい。
    どの作品のどのキャラかは、投稿者と本文からこちらで判定できる。

      1. 見つけたURLをテキストファイルに1行ずつ貼る（何が混ざっていてもよい）
      2. python3 scripts/x_posts.py intake urls.txt
      3. python3 scripts/x_posts.py verify

    作品の決め方は2段階。まず x_accounts.tsv で投稿者から作品を引く。
    未登録のアカウントなら、本文に名前が出てくるキャラを全作品から探して、
    一番多く当たった作品にする。推測した場合はその旨を出す。
    """
    # 記入用ブックなら、人が付けた cid をそのまま使う
    fixed = []
    plain = []
    for path in args.files:
        rows = read_sheet_rows(path)
        if rows:
            fixed += rows
        else:
            plain.append(path)

    text = ""
    for path in plain:
        with open(path, encoding="utf-8", errors="replace") as f:
            text += f.read() + "\n"
    ids, order = {}, []
    for m in SCAN.finditer(text):
        sid = m.group(2)
        if sid not in ids:
            ids[sid] = m.group(1)
            order.append(sid)
    if not ids and not fixed:
        sys.exit("投稿URLが1件も見つかりません")

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    chars = [dict(r) for r in con.execute(
        "select cid, name, name_latin, main_vid from character")]
    titles = {r[0]: r[1] for r in con.execute("select vid, title from work")}
    # 代表作（main_vid）だけで引くと、続編やシリーズ共通アカウントの投稿を
    # 取りこぼす。DBには代表作以外にも登場するキャラが756人いる。
    # character_work（登場作品）も足して引く
    by_vid, seen_pair = {}, set()
    by_cid = {c["cid"]: c for c in chars}
    for c in chars:
        by_vid.setdefault(c["main_vid"], []).append(c)
        seen_pair.add((c["main_vid"], c["cid"]))
    for vid, cid in con.execute("select vid, cid from character_work"):
        if (vid, cid) not in seen_pair and cid in by_cid:
            by_vid.setdefault(vid, []).append(by_cid[cid])
            seen_pair.add((vid, cid))
    # アカウント → 作品。1アカウントが続編と共用のこともあるので候補は複数持つ
    acct_vids = {}
    for vid, handles in load_accounts().items():
        for h in handles:
            acct_vids.setdefault(h, []).append(vid)

    rows, unknown, dead = [], [], []
    for sid in order:
        d = oembed(sid, ids[sid])
        if d is None:
            dead.append(sid)
            continue
        handle, txt = d["_handle"], d["_text"]
        cands = acct_vids.get(handle.lower())
        if not cands:
            # 投稿者から作品を引けない場合は推測しない。本文に出てくる名前から
            # 当てにいくと、短い名前が別作品のキャラに当たって黙って間違える。
            # アカウントを1行足してもらってから読み直すほうが確実で早い。
            unknown.append((sid, handle, "@%s が x_accounts.tsv に未登録" % handle))
            continue
        guessed = False

        # 候補作品のうち、名前が一番よく当たるものを採る
        best_vid, best_hits, best_match = None, [], "part"
        for vid in cands:
            pool = by_vid.get(vid, [])
            t = titles.get(vid, "")
            full = [c for c in pool if name_hit(txt, c["name"], t, c["name_latin"]) == "full"]
            part = [c for c in pool if name_hit(txt, c["name"], t, c["name_latin"]) == "part"]
            hits, match = (full, "full") if full else (part,
                          "unique" if len(part) == 1 else "part")
            if len(hits) > len(best_hits) or best_vid is None and hits:
                best_vid, best_hits, best_match = vid, hits, match
        if not best_hits:
            unknown.append((sid, handle, "本文にキャラ名が出てこない"))
            continue
        if len(best_hits) > args.max_chars:
            unknown.append((sid, handle, "%d人が並ぶ一覧的な投稿" % len(best_hits)))
            continue
        for c in best_hits:
            rows.append({"cid": c["cid"], "vid": best_vid, "character": c["name"],
                         "status_url": "https://x.com/%s/status/%s" % (handle, sid),
                         "match": best_match,
                         "note": ("紹介らしい" if INTRO_HINT.search(txt) else "要確認")
                                 + " ｜ " + txt.strip().replace("\n", " ")[:60]})

    # 人が指定したぶん。実在だけ確かめて、そのまま採る
    hand, hand_dead = [], []
    chars_by_cid = {c["cid"]: c for c in chars}
    for cid, url in fixed:
        acct, sid = parse_status(url)
        c = chars_by_cid.get(cid)
        if not c:
            unknown.append((sid or "?", acct or "?", "cid %s がDBに無い" % cid))
            continue
        d = oembed(sid, acct or "i")
        if d is None:
            hand_dead.append((cid, c["name"], url))
            continue
        txt = d["_text"]
        hand.append({"cid": cid, "vid": c["main_vid"], "character": c["name"],
                     "status_url": "https://x.com/%s/status/%s" % (d["_handle"], sid),
                     "match": "手入力",
                     "note": ("紹介らしい" if INTRO_HINT.search(txt) else "手で指定")
                             + " ｜ " + txt.strip().replace("\n", " ")[:60]})

    head = ["cid", "vid", "character", "status_url", "match", "note"]
    keep = {(r["cid"], r["status_url"]): r for r in read_tsv(QUEUE)}
    for r in rows + hand:
        keep[(r["cid"], r["status_url"])] = r
    write_tsv(QUEUE, head, sorted(keep.values(), key=lambda r: (r["vid"], r["cid"])),
              preamble="# 検索で拾った候補。ここは間違いが混ざっていてよい。\n"
                       "# x_posts.py verify が oEmbed で実在・投稿者・本文を確かめ、\n"
                       "# 通ったものだけ x_posts.tsv に、怪しいものは _review_x.tsv に振り分ける。\n")

    got = {}
    for r in rows + hand:
        got.setdefault(titles.get(r["vid"], r["vid"]), set()).add(r["character"])
    print("手入力 %d件 / 本文から判定 %d件 → 合計 %d件"
          % (len(hand), len(rows), len(hand) + len(rows)))
    for t in sorted(got):
        print("  %s: %s" % (t, "、".join(sorted(got[t]))))
    need = sorted({h for _, h, w in unknown if "未登録" in w})
    for sid, handle, why in unknown:
        print("  ? https://x.com/%s/status/%s  (%s)" % (handle, sid, why))
    if need:
        print("\n未登録のアカウント。corrections/x_accounts.tsv に作品IDを添えて足してください:")
        for h in need:
            print("  <作品ID>\t<作品名>\t@%s\thttps://x.com/%s\t%s\t公式アカウント"
                  % (h, h, datetime.date.today().isoformat()))
        print("  足したら intake をもう一度流せば拾えます。")
    for sid in dead:
        print("  × 削除済み・非公開: %s" % sid)
    for cid, nm, url in hand_dead:
        print("  ×!! 手で指定されたが投稿が消えている: %s %s %s" % (cid, nm, url))
    if rows:
        print("\n次: python3 scripts/x_posts.py verify")


def cmd_sheet(args):
    """収集用の作業表を出す。作品ごとに、公式アカウントと検索URLを並べる。

    上から順に検索URLを開いて、それらしい投稿のURLを控えていくだけでよい。
    キャラの取りこぼしが分かるように、未収集の人だけを出す。
    """
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    got = {r["cid"] for r in read_tsv(POSTS)}
    accounts = load_accounts()
    rows = con.execute("""
        select w.vid, w.title, w.year, w.votecount
        from work w join character c on c.main_vid = w.vid
        where w.year >= ? group by w.vid
        order by w.votecount desc, w.year desc""", (args.since,)).fetchall()

    n = 0
    for w in rows:
        chars = con.execute("""select cid, name, cv from character
                               where main_vid=? order by role_label desc, cid""",
                            (w["vid"],)).fetchall()
        left = [c for c in chars if c["cid"] not in got and c["cid"] not in skip_char]
        if not left:
            continue
        n += 1
        if n > args.limit:
            break
        handles = sorted(accounts.get(w["vid"], []))
        acct = handles[0] if handles else None
        print("\n## %s（%s年）  %s  残り%d/%d人"
              % (w["title"], w["year"], w["vid"], len(left), len(chars)))
        if not acct:
            print("   公式アカウント: **未特定** — 分かったら corrections/x_accounts.tsv に追記")
            print("   探す: https://x.com/search?q=%s"
                  % urllib.parse.quote("%s 公式" % w["title"]))
            continue
        print("   公式アカウント: @%s" % acct)
        for c in left:
            base = re.sub(r"[（(][^）)]*[）)]", "", c["name"]).strip()
            key = max(re.split(r"[\s・･]", base) or [base], key=len)
            q = ("from:%s %s %s" % (acct, key, args.word)).strip()
            print("   %-24s %s" % (c["name"],
                                   "https://x.com/search?q=%s&f=live" % urllib.parse.quote(q)))


# 最新の移植版のページ。/switch/ や -switch、ns/ など書き方に幅がある
SWITCH_URL = re.compile(r"(?:^|[/\-_.])(?:switch|ns)(?:[/\-_.]|$)", re.I)
GENERIC_URL = re.compile(r"special-pack|/smp/|web\.archive\.org|/shop|/store", re.I)


def site_score(url):
    """どの公式サイトから先に見るか。新しいページを先に見る。

    古い作品ほど、元のサイトにはXへのリンクが無い。AMNESIA（2011年）の
    公式アカウント @AmnOtomate は2021年開設で、リンクを張っているのは
    Switch版のページ（otomate.jp/amnesia/switch/）のほうだった。
    """
    u = url or ""
    n = 0
    if SWITCH_URL.search(u):
        n += 100
    if u.startswith("https://"):
        n += 10
    if GENERIC_URL.search(u):
        n -= 50
    return n


SITE_CACHE = os.path.join(DATA, "cache", "site_html")

# X の予約パス。アカウント名ではない
RESERVED = {"intent", "share", "home", "search", "hashtag", "i", "about", "privacy",
            "tos", "login", "signup", "explore", "notifications", "messages", "settings",
            "compose", "widgets", "download", "en", "ja", "help", "status", "statuses"}
HANDLE_IN_URL = re.compile(
    r"(?:https?:)?//(?:www\.|mobile\.)?(?:x|twitter)\.com/(?:#!/)?([A-Za-z0-9_]{1,15})")


def fetch_html(url, vid):
    """公式サイトを1回だけ取ってきてキャッシュする。古い作品は大半が死んでいる。"""
    os.makedirs(SITE_CACHE, exist_ok=True)
    cache = os.path.join(SITE_CACHE, "%s.html" % vid)
    if os.path.exists(cache):
        with open(cache, encoding="utf-8", errors="replace") as f:
            return f.read()
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(600000).decode("utf-8", errors="replace")
    except Exception as ex:
        body = "<!-- fetch-failed: %s -->" % ex
    with open(cache, "w", encoding="utf-8") as f:
        f.write(body)
    return body


def cmd_accounts(args):
    """作品の公式サイトを見て、X アカウントの候補を拾う。

    公式アカウントは1作品につき1回調べればよいが、350作品を手で開くのは重い。
    公式サイトにはたいていXへのリンクが張ってあるので、そこから拾う。

    拾えたものは corrections/_queue_x_accounts.tsv に出す。1サイトから複数の
    アカウントが見つかることも多い（ブランド公式・レーベル公式・別作品）ので、
    そのまま x_accounts.tsv には入れない。人が見て正しい1つを選ぶ。
    """
    if args.promote:
        return _promote_accounts()
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    known = load_accounts()
    rows = con.execute("""
        select w.vid, w.title, w.year, w.votecount,
               group_concat(l.url, char(10)) urls
        from work w join work_link l on l.vid = w.vid
        where l.site = 'website'
          and (w.year >= ? or w.year is null)
          and exists (select 1 from character c where c.main_vid = w.vid)
        group by w.vid order by w.votecount desc""", (args.since,)).fetchall()

    out, stats = [], {"既知": 0, "取得失敗": 0, "見つからず": 0, "候補あり": 0}
    for n, w in enumerate(rows[:args.limit], 1):
        if w["vid"] in known and not args.all:
            stats["既知"] += 1
            continue
        # 1作品に公式サイトが複数ぶら下がっていることが多い（本編・移植版・
        # 続編・海外版）。古いページにはXへのリンクが無いので、見つかるまで順に試す
        urls = sorted([u for u in (w["urls"] or "").split("\n") if u.startswith("http")],
                      key=site_score, reverse=True)
        hits, tried, failed = [], 0, 0
        for k, url in enumerate(urls[:args.tries]):
            html = fetch_html(url, "%s-%d" % (w["vid"], k))
            tried += 1
            if html.startswith("<!-- fetch-failed"):
                failed += 1
                continue
            for m in HANDLE_IN_URL.finditer(html):
                h = m.group(1)
                if h.lower() not in RESERVED and h not in hits:
                    hits.append(h)
            if args.delay:
                time.sleep(args.delay)
            if hits:
                break
        if hits:
            stats["候補あり"] += 1
            out.append({"vid": w["vid"], "title": w["title"],
                        "account": " ".join("@" + h for h in hits[:5]),
                        "source_url": urls[0] if urls else "",
                        "note": "候補%d件。正しいものだけ残して x_accounts.tsv へ" % len(hits)})
        elif failed == tried:
            stats["取得失敗"] += 1
            out.append({"vid": w["vid"], "title": w["title"], "account": "",
                        "source_url": urls[0] if urls else "",
                        "note": "サイトが取得できない（%d件試した）" % tried})
        else:
            stats["見つからず"] += 1
            out.append({"vid": w["vid"], "title": w["title"], "account": "",
                        "source_url": urls[0] if urls else "",
                        "note": "サイトにXへのリンクなし（%d件試した）" % tried})
        if n % 25 == 0:
            print("  ... %d/%d" % (n, min(len(rows), args.limit)), flush=True)

    path = os.path.join(CORR, "_queue_x_accounts.tsv")
    write_tsv(path, ["vid", "title", "account", "source_url", "note"], out,
              preamble="# 公式サイトから拾ったXアカウントの候補。\n"
                       "# 1サイトに複数のアカウントが載っていることが多いので、\n"
                       "# 正しいものを選んで corrections/x_accounts.tsv に移すこと。\n")
    print("%s に %d件" % (path, len(out)))
    print("  " + " / ".join("%s%d" % (k, v) for k, v in stats.items()))


def _promote_accounts():
    """候補が1つだけの作品を x_accounts.tsv に上げる。

    複数候補（ブランド公式とレーベル公式が並んでいる等）は上げない。
    どれが作品の公式かは人が見ないと決められない。

    上げたものには「未確認」と書いておく。公式サイトからのリンクなので
    まず間違いないが、イラストレーターの個人アカウントが混ざる可能性はある。
    """
    cand = read_tsv(os.path.join(CORR, "_queue_x_accounts.tsv"))
    known = load_accounts()
    today = datetime.date.today().isoformat()
    add, multi = [], []
    for r in cand:
        handles = [h for h in (r.get("account") or "").split() if h]
        if not handles or r["vid"] in known:
            continue
        (add if len(handles) == 1 else multi).append(r)

    with open(ACCOUNTS, "a", encoding="utf-8") as f:
        for r in add:
            f.write("%s\t%s\t%s\t%s\t%s\t%s\n"
                    % (r["vid"], r["title"], r["account"], r["source_url"], today,
                       "公式サイトからの自動抽出。未確認"))
    print("x_accounts.tsv に %d件を追加した（候補が1つだけのもの）" % len(add))
    print("候補が複数で保留 %d件。作業ブックの「作品」シートで選ぶ:" % len(multi))
    for r in multi[:15]:
        print("  %-30s %s" % (r["title"][:28], r["account"]))


JP_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def cmd_series(args):
    """1件のURLを渡すと、その投稿と同じ日の投稿をまとめて見る検索URLを出す。

    キャラ紹介は同じ日に数分間隔で連投される。Collar×Malice の6人は
    2026年8月18日に連投され、IDもほぼ連番だった。1件見つけたらそこで
    止めず、その日ごと見にいけば残りも一度に取れる。

    キャラ単位で検索を繰り返すと、この連投を見落として誕生日ポストのほうを
    拾ってしまう（実際にそうなった）。
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
        m = JP_DATE.search(d["_text"])
        if not m:
            print("×  日付が読めない: %s" % url)
            continue
        y, mo, da = (int(x) for x in m.groups())
        day = datetime.date(y, mo, da)
        # 連投が数日にまたがることもある。ヴィルシュの紹介は6月と7月の
        # 2波に分かれていた。--days で窓を広げる
        a = day - datetime.timedelta(days=args.days - 1)
        b = day + datetime.timedelta(days=args.days)
        q = "from:%s since:%s until:%s" % (d["_handle"], a.isoformat(), b.isoformat())
        print("%s が %s〜%s に投稿したもの:" % (d["_handle"], a.isoformat(), b.isoformat()))
        print("  https://x.com/search?q=%s&f=live" % urllib.parse.quote(q))


def cmd_gaps(args):
    """紹介ポストの連投を取りこぼしていそうな作品を出す。

    同じ作品で、ある人はプロフィール型が取れているのに別の人は誕生日型、
    という状態は「連投を途中までしか拾っていない」ことが多い。
    """
    con = sqlite3.connect(DB)
    best = best_posts()
    titles = {r[0]: r[1] for r in con.execute("select vid, title from work")}
    by_vid = {}
    for r in best.values():
        by_vid.setdefault(r["vid"], []).append(r)
    hits = []
    for vid, rows in by_vid.items():
        kinds = {r["kind"] for r in rows}
        if "プロフィール" in kinds and kinds - {"プロフィール"}:
            prof = [r for r in rows if r["kind"] == "プロフィール"][0]
            other = [r for r in rows if r["kind"] != "プロフィール"]
            hits.append((titles.get(vid, vid), vid, prof, other))
    if not hits:
        print("混在している作品はない")
        return
    print("プロフィール型と他の型が混ざっている作品。連投を取りこぼしている可能性がある:")
    for title, vid, prof, other in sorted(hits, key=lambda x: -len(x[3])):
        print("\n## %s（%s）プロフィール型でない人 %d名: %s"
              % (title, vid, len(other), "、".join(r["character"] for r in other)))
        print("   同じ日を見る: python3 scripts/x_posts.py series %s" % prof["status_url"])


# --- ここから cadence ---

# X の投稿IDは snowflake で、上位ビットに投稿時刻(ms)が入っている。
# oEmbed を叩かなくてもURLだけからミリ秒単位の投稿時刻が復元できる。
# oEmbed が返す日本語表記の日付は「日」までしか無く、連投の間隔（秒〜分）が
# 測れない。ここが測れると「バーストなのか日次連載なのか」を判別できる。
SNOWFLAKE_EPOCH = 1288834974657


def status_time(status_id):
    """投稿IDから投稿時刻(JST)を復元する。ネットワークは使わない。"""
    ms = (int(status_id) >> 22) + SNOWFLAKE_EPOCH
    return (datetime.datetime.utcfromtimestamp(ms / 1000.0)
            + datetime.timedelta(hours=9))


def _fmt_gap(sec):
    if sec < 120:
        return "%d秒" % sec
    if sec < 7200:
        return "%d分" % (sec / 60)
    if sec < 172800:
        return "%.1f時間" % (sec / 3600)
    return "%.2f日" % (sec / 86400)


def classify_run(times):
    """投稿時刻の並びから連投の型と周期を返す。

    実測（手集めした18アカウント116件）ではっきり3つに分かれた。

      バースト型  薄桜鬼9件が1秒以内、psy_otomate 5件が18秒間隔、
                  CM_otomate 25秒、OtomateWeb 28秒、pf_otomate 3分。
                  18アカウント中12がこれ。同じ日を見れば全員取れる。
      日次連載型  OZMAFIA!! 13件が24時間ちょうどで14日間、
                  norn9_anime 13件が24時間＋30分後の補足投稿。
      周期連載型  StarrySky_hb がきっかり7.00日おき、
                  even if TEMPEST が14日/24日/65日（毎回12:00）、
                  CP_otomate が28日おき。

    後ろ2つは「同じ日」を見ても1件しか出てこない。現に StarrySky は
    3件中1件、OZMAFIA は13件中1件しか取れていなかった。周期が分かれば
    残りは検索し直さなくても窓を広げるだけで拾える。
    """
    if len(times) < 2:
        return "単発", None, []
    gaps = [(times[i + 1] - times[i]).total_seconds()
            for i in range(len(times) - 1)]
    span = (times[-1] - times[0]).total_seconds()
    if span <= 3600:
        return "バースト", None, gaps
    # 補足投稿（本編の数十分後に投げる次回予告など）は周期の判定から外す
    main = [g for g in gaps if g > 3600] or gaps
    main_sorted = sorted(main)
    med = main_sorted[len(main_sorted) // 2]
    # ばらつきが中央値の25%以内なら「等間隔」とみなす。実測の
    # StarrySky(7.00日×2)・virche(24.0時間)・OZMAFIA(24時間±3%)が通る
    if med > 0 and all(abs(g - med) <= med * 0.25 for g in main):
        if med < 129600:                       # 36時間未満
            return "日次連載", med, gaps
        return "周期連載", med, gaps
    return "不定期", med, gaps


def cluster_runs(rows, split_days=45):
    """確定ぶんを「ひとつながりの連投」ごとに割る。

    同じ作品でも、発売前の紹介連投と、数年後の周年企画やFDの追加紹介が
    混ざる。ピオフィオーレは2017年の連投5件と2021年の1件、大正×対称アリスは
    2014年と2020年で、まとめて1本の周期とみなすと『周期2170日』になり、
    検索窓が2002年〜2032年に広がって使いものにならなかった。
    間が45日以上あいたら別の波として扱う。
    """
    runs, cur = [], []
    for i, item in enumerate(rows):
        if cur and (item[0] - cur[-1][0]).total_seconds() > split_days * 86400:
            runs.append(cur)
            cur = []
        cur.append(item)
    if cur:
        runs.append(cur)
    return runs


def cmd_cadence(args):
    """確定ぶんの投稿間隔を測り、取りこぼしていそうな作品と次の検索窓を出す。

    gaps は「プロフィール型と誕生日型が混ざっている作品」しか検出できない。
    だが実際に一番取りこぼすのは、型が揃ったまま件数だけ足りない場合
    （OZMAFIA!! 13人中1人、StarrySky 4人中3人）で、これは混在しないので
    gaps に出てこなかった。件数で見て、周期から窓を作る。
    """
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    got = {}
    for r in read_tsv(POSTS):
        _, sid = parse_status(r["status_url"])
        if sid:
            got.setdefault(r["vid"], []).append((status_time(sid), r))
    titles, roster = {}, {}
    for r in con.execute("select w.vid, w.title, count(c.cid) n "
                         "from work w join character c on c.main_vid = w.vid "
                         "group by w.vid"):
        titles[r["vid"]], roster[r["vid"]] = r["title"], r["n"]
    out = []
    for vid, rows in got.items():
        rows.sort(key=lambda x: x[0])
        n_char = len({r["cid"] for _, r in rows})
        total = roster.get(vid, n_char)
        if n_char >= total and not args.all:
            continue
        out.append((total - n_char, vid, n_char, total, cluster_runs(rows)))
    if not out:
        print("件数が足りない作品はない")
        return
    print("確定ぶんのある作品のうち、まだ埋まっていないもの（残りが多い順）\n")
    for left, vid, n_char, total, runs in sorted(out, key=lambda x: -x[0]):
        acct = runs[0][0][1]["account"]
        got_cids = {r["cid"] for run in runs for _, r in run}
        print("## %s（%s）%s  %d/%d人"
              % (titles.get(vid, vid), vid, acct, n_char, total))
        for run in runs:
            times = [t for t, _ in run]
            kind, period, gaps = classify_run(times)
            t0, t1 = times[0], times[-1]
            head = "   %s〜%s  %d件  型=%s%s" % (
                t0.strftime("%Y-%m-%d %H:%M"), t1.strftime("%m-%d %H:%M"),
                len(run), kind, "  周期=%s" % _fmt_gap(period) if period else "")
            print(head)
            if gaps:
                print("     間隔: %s" % " ".join(_fmt_gap(g) for g in gaps))
            # 窓の作り方。バーストは同じ日で足りる。周期があるなら、残り人数ぶん
            # だけ前後に伸ばす。伸ばしすぎても検索結果が読み切れないので上限を置く
            if kind in ("バースト", "単発"):
                a = t0.date() - datetime.timedelta(days=1)
                b = t1.date() + datetime.timedelta(days=2)
            elif period:
                pad = min(period * (left + 1), 120 * 86400.0)
                a = (t0 - datetime.timedelta(seconds=pad)).date()
                b = (t1 + datetime.timedelta(seconds=pad)).date() + datetime.timedelta(days=1)
            else:
                a = t0.date() - datetime.timedelta(days=30)
                b = t1.date() + datetime.timedelta(days=31)
            q = "from:%s since:%s until:%s" % (acct.lstrip("@"), a.isoformat(), b.isoformat())
            print("     窓: https://x.com/search?q=%s&f=live" % urllib.parse.quote(q))
        miss = [r["character"] for r in con.execute(
            "select cid, name character from character where main_vid = ?", (vid,))
            if r["cid"] not in got_cids]
        if miss:
            print("   未収集 %d名: %s" % (len(miss), "、".join(miss[:12])
                                          + ("…" if len(miss) > 12 else "")))
        print()

# --- ここから hunt / none ---

NONE = os.path.join(CORR, "x_none.tsv")

# 足がかりを見つけるための語。作品ごとに1件見つかればよく、そこから先は
# cadence が出す日付の窓で連投ごと拾える。
#
# 手集めした113件を数えたところ、見出しの語彙はまるで揃っていなかった。
#   キャラ紹介 / キャラクター紹介 / 攻略キャラクター紹介 / 登場人物情報 /
#   Character Profile / SubCharacter5 / character紹介 /
#   【緋影】「セリフ」 / “クールな一匹狼”（キャッチコピーだけ）/ MOZU（名前だけ）
# 「紹介」だけで引くと、足がかりが見つかる作品は21作品中8作品（38%）しかない。
# CV表記を足すと16作品（76%）、【 で始まる定型を足すと18作品（86%）になる。
# 逆にPV告知やブログ更新も「紹介」を含むので、「紹介」単独はノイズも多い。
SEED_WORDS = ["年齢", "身長", "紹介", "登場人物", "プロフィール", "Profile", "キャラクター"]


def cmd_hunt(args):
    """作品ごとに「まず何を検索すればいいか」を段取りの順で出す。

    キャラ1人ずつ検索すると、1作品ぶんで7回叩くことになるうえ、連投を
    途中までしか拾えない（実際にカラーマリスで6人中1人しか取れなかった）。
    作品ごとに1回で足がかりを見つけ、そこから窓を広げるほうが、
    リクエストも減って取りこぼしも減る。

      ① 足がかり  from:アカウント (CV OR 紹介 OR …) を1回
      ② 窓        見つけたURLを cadence／series に渡して同じ波を全部見る
      ③ 取りこぼし 名前で直接引く（セリフ型・キャッチコピー型はこれしかない）
    """
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    got = {r["cid"] for r in read_tsv(POSTS)}
    # 誕生日ポストでしか埋まっていない人は「埋まっている」と数えない。
    # 誕生日は描き下ろしイラストが付く代わりに人物の説明が無いことが多く、
    # 最終手段として置くもの。紹介ポストが別にあるなら差し替えたい。
    best = best_posts()
    weak = {cid for cid, r in best.items()
            if (r.get("kind") or "その他") in args.weak_kinds}
    if args.weak:
        got -= weak
    accounts = {r["vid"]: r.get("account", "") for r in read_tsv(ACCOUNTS)}
    # x_none.tsv には作品(vid)とキャラ(cid)が混ざって入る。ヒロインは
    # 紹介ポストが作られないことが多く、作品ごと飛ばすわけにはいかない
    noneids = {r.get("id") or r.get("vid") for r in read_tsv(NONE)}
    skip = {i for i in noneids if i and i.startswith("v")}
    skip_char = {i for i in noneids if i and i.startswith("c")}
    rows = con.execute("""
        select w.vid, w.title, w.year, w.votecount
        from work w join character c on c.main_vid = w.vid
        group by w.vid order by w.votecount desc, w.year desc""").fetchall()
    n = 0
    for r in rows:
        vid = r["vid"]
        if vid in skip:
            continue
        acct = accounts.get(vid, "")
        if not acct:
            continue                      # アカウント未特定は先に accounts で埋める
        chars = con.execute(
            "select cid, name, role from character where main_vid = ? order by role desc",
            (vid,)).fetchall()
        left = [c for c in chars if c["cid"] not in got]
        if len(left) < args.min_left:
            continue
        n += 1
        if n > args.limit:
            break
        handle = acct.split()[0].lstrip("@")
        print("## %s（%s年）%s  残り%d/%d人  %s"
              % (r["title"], r["year"], vid, len(left), len(chars), acct))
        if DERIVED_ACCOUNT.search(handle):
            # 先頭はゲーム公式の場所。アニメ版しか登録されていないなら、
            # 先にゲーム公式を探したほうがよい。古い作品はゲーム公式が
            # 存在しないこともあり、その場合はアニメ版でよい
            print("   ⚠ 先頭がアニメ/舞台版らしい。ゲーム公式を探す: "
                  "https://x.com/search?q=%s"
                  % urllib.parse.quote("%s 公式" % r["title"]))
        q = "from:%s (%s)" % (handle, " OR ".join(SEED_WORDS))
        print("   ① 足がかり: https://x.com/search?q=%s&f=live" % urllib.parse.quote(q))
        # ①が空振りする作品がある。黒蝶／灰鷹のサイケデリカは
        # 「【紋白】「セリフ」（CV：日野聡）」というセリフ型で、年齢も身長も
        # 「紹介」も書かない。その場合はキャラ名そのものが一番強い鍵になる。
        # 1人ずつ叩くと6リクエストかかるので、OR でまとめて1回にする
        full = [re.sub(r"[（(][^）)]*[）)]", "", c["name"]).strip()
                for c in left if c["role"] != "主人公"][:8]
        if full:
            q2 = "from:%s (%s)" % (handle, " OR ".join('"%s"' % n for n in full))
            print("   ② 名前でまとめて: https://x.com/search?q=%s&f=live"
                  % urllib.parse.quote(q2))
        print("   ③ 見つけたら窓を広げる: python3 scripts/x_posts.py series <そのURL>")
        # ③ は姓（または最初の語）で引く。セリフ型・キャッチコピー型の作品は
        # 定型の見出しが無いので、これしか手が無い
        # フルネームだと表記ゆれ（中黒の有無・カナ違い）で外れるので、
        # 姓名のうち長いほうを1語だけ使う。sheet / plan と同じ扱いにする
        for c in [c for c in left if c["role"] != "主人公"][:3]:
            base = re.sub(r"[（(][^）)]*[）)]", "", c["name"]).strip()
            key = max(re.split(r"[\s・･]", base) or [base], key=len)
            q3 = "from:%s %s" % (handle, key)
            print("   ④ %-12s https://x.com/search?q=%s&f=live"
                  % (key, urllib.parse.quote(q3)))
        fresh = [c["name"] for c in left if c["cid"] not in weak]
        upgrade = [c["name"] for c in left if c["cid"] in weak]
        if fresh:
            print("   未収集: %s" % "、".join(fresh[:12]))
        if upgrade:
            print("   誕生日どまり（差し替えたい）: %s" % "、".join(upgrade[:12]))
        print()
    if n == 0:
        print("公式アカウントが分かっていて未完了の作品はない")


def cmd_none(args):
    """「探したが紹介ポストが無かった」作品を記録する。

    記録しておかないと、同じ作品を何度も探し直すことになる。3700人ぶんを
    回すあいだ、これが無いと同じ空振りを繰り返す。公式が紹介ポストを
    出していない作品は実際にあり（依頼者の手集めでも『該当なし』が7件）、
    その場合キャラページは今までどおり代表作のパッケージを出す。
    """
    if not os.path.exists(NONE):
        with open(NONE, "w", encoding="utf-8") as f:
            f.write("# 探したが紹介ポストが見つからなかった作品／キャラ。hunt がここを飛ばす。\n")
            f.write("# id には vid でも cid でも書ける。見つかったら行を消せば対象に戻る。\n")
            f.write("id\tchecked_at\tnote\n")
    today = datetime.date.today().isoformat()
    with open(NONE, "a", encoding="utf-8") as f:
        for i in args.vids:
            f.write("%s\t%s\t%s\n" % (i, today, args.note))
    print("%d件を x_none.tsv に記録した" % len(args.vids))

EMBEDS = os.path.join(CORR, "x_embeds.json")


def cmd_embeds(args):
    """確定ぶんの埋め込みHTMLを corrections/x_embeds.json に書き出す。

    埋め込みHTMLは収集時に oEmbed から取って data/cache/ に置いてあるが、
    data/ は .gitignore されていて GitHub Actions の runner には存在しない。
    ワークフローは runner 上で docs/v2 を作り直すので、キャッシュだけに
    頼るとビルドで埋め込みが全部消える。git 管理下に持つ。

    ビルド中にXを叩く手もあるが、生成のたびに数百リクエストが飛ぶうえ、
    Xが落ちているとサイトが作れなくなる。取ったものを持ち回るほうがよい。
    """
    out = {}
    for cid, r in sorted(best_posts().items()):
        _, sid = parse_status(r["status_url"])
        if not sid:
            continue
        path = os.path.join(CACHE, "%s.json" % sid)
        if not os.path.exists(path):
            print("・ キャッシュなし: %s %s" % (cid, r["character"]))
            continue
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("_dead") or not d.get("html"):
            continue
        out[cid] = {"html": d["html"], "account": r["account"],
                    "kind": r.get("kind", ""), "url": r["status_url"],
                    "character": r["character"]}
    with open(EMBEDS, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("%s に %d人ぶん（%.0fKB）" % (EMBEDS, len(out),
                                       os.path.getsize(EMBEDS) / 1024.0))


def cmd_verify(args):
    chars = {}
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    for r in con.execute("select cid, name, name_latin, main_vid, main_title from character"):
        chars[r["cid"]] = dict(r)
    official = load_accounts()
    titles = {r[0]: r[1] for r in con.execute("select vid, title from work")}

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
        hit = name_hit(text, ch["name"], titles.get(ch["main_vid"], ""),
                       ch["name_latin"])
        reasons = []
        if want and handle.lower() not in want:
            reasons.append("@%s は公式(%s)ではない" % (handle, "/".join("@" + w for w in sorted(want))))
        if not want:
            reasons.append("公式アカウント未登録")
        if not hit:
            reasons.append("本文にキャラ名なし")
        elif hit == "part" and row.get("match") != "unique":
            reasons.append("名前が部分一致")

        rec = {"cid": cid, "vid": row.get("vid") or ch["main_vid"], "character": ch["name"],
               "account": "@" + handle, "status_url": "https://x.com/%s/status/%s" % (handle, sid),
               "kind": post_kind(text, ch["name"]), "checked_at": today,
               "note": (row.get("note") or "").strip() or text.strip().replace("\n", " ")[:80]}
        # 人が「このキャラの行」に書いたものは落とさない。
        # 本文にキャラ名が出ない投稿（主人公のアイコン配布やOP公開の告知など）は
        # 機械には判定できないが、人はそれを分かって選んでいる。
        # 気づいた点は note に添えるだけにして、採否は人の判断に従う。
        if row.get("match") == "手入力":
            if reasons:
                rec["note"] = "手入力（%s） ｜ %s" % (" / ".join(reasons), rec["note"])
            ok.append(rec)
            continue
        (ok if not reasons else review).append(
            rec if not reasons else dict(rec, note="要確認: " + " / ".join(reasons) + " ｜ " + rec["note"]))

    keep = {(r["cid"], r["status_url"]): r for r in read_tsv(POSTS)}
    for r in ok:
        keep[(r["cid"], r["status_url"])] = r
    head = ["cid", "vid", "character", "account", "status_url", "kind", "checked_at", "note"]
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
        where w.year >= ? or w.year is null
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
    pl = sub.add_parser("plan")
    pl.add_argument("vid")
    pl.add_argument("--word", default="",
                    help="キャラ名に足す語。既定は無し（名前だけで引く）。"
                         "既定を『誕生』にしていたころは誕生日ポストばかり集まっていた。"
                         "紹介ポストが先に要るので、絞るなら『紹介』を渡す")
    pl.set_defaults(fn=cmd_plan)
    ik = sub.add_parser("intake")
    ik.add_argument("files", nargs="+", help="URLを貼ったテキスト。書式は問わない")
    ik.add_argument("--max-chars", type=int, default=3, dest="max_chars")
    ik.set_defaults(fn=cmd_intake)
    sh = sub.add_parser("sheet")
    sh.add_argument("--since", type=int, default=0)
    sh.add_argument("--limit", type=int, default=10)
    sh.add_argument("--word", default="")
    sh.set_defaults(fn=cmd_sheet)
    se = sub.add_parser("series")
    se.add_argument("urls", nargs="+")
    se.add_argument("--days", type=int, default=1,
                    help="前後何日ぶんを見るか。連投が数日に分かれる作品がある")
    se.set_defaults(fn=cmd_series)
    gp = sub.add_parser("gaps")
    gp.set_defaults(fn=cmd_gaps)
    hu = sub.add_parser("hunt")
    hu.add_argument("--limit", type=int, default=5)
    hu.add_argument("--weak", action="store_true",
                    help="誕生日ポストでしか埋まっていない人も対象に含める")
    hu.add_argument("--weak-kinds", nargs="*", default=["誕生日"],
                    help="差し替えたい種類。既定は誕生日だけ")
    hu.add_argument("--min-left", type=int, default=1,
                    help="未収集がこの人数以上の作品だけ出す。残り1人はたいてい主人公で、"
                         "紹介ポスト自体が無いことが多い")
    hu.set_defaults(fn=cmd_hunt)
    nn = sub.add_parser("none")
    nn.add_argument("vids", nargs="+")
    nn.add_argument("--note", default="紹介ポストが見つからなかった")
    nn.set_defaults(fn=cmd_none)
    cd = sub.add_parser("cadence")
    cd.add_argument("--all", action="store_true", help="埋まっている作品も出す")
    cd.set_defaults(fn=cmd_cadence)
    em = sub.add_parser("embeds")
    em.set_defaults(fn=cmd_embeds)
    ac = sub.add_parser("accounts")
    ac.add_argument("--since", type=int, default=0)
    ac.add_argument("--limit", type=int, default=500)
    ac.add_argument("--delay", type=float, default=1.0)
    ac.add_argument("--all", action="store_true", help="登録済みの作品も見直す")
    ac.add_argument("--tries", type=int, default=3,
                    help="1作品につき試す公式サイトURLの数")
    ac.add_argument("--promote", action="store_true",
                    help="候補が1つだけの作品を x_accounts.tsv に上げる")
    ac.set_defaults(fn=cmd_accounts)
    pv = sub.add_parser("preview")
    pv.add_argument("cids", nargs="*")
    pv.add_argument("--out", default="preview_x")
    pv.add_argument("--work", help="作品ID。全キャラを1投稿ずつ並べた作品ページを作る")
    pv.add_argument("--place", choices=["hero", "section"], default="hero",
                    help="hero=パッケージ写真の枠に置く / section=独立した節にする")
    pv.add_argument("--lazy", action="store_true",
                    help="押すまで読み込まない形にする")
    pv.set_defaults(fn=cmd_preview)
    q = sub.add_parser("queue")
    q.add_argument("--since", type=int, default=0)
    q.add_argument("--limit", type=int, default=40)
    q.set_defaults(fn=cmd_queue)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
