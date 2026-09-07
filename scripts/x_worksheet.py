# -*- coding: utf-8 -*-
"""キャラクター紹介Xポストを集めるための記入用ブックを作る。

    python3 scripts/x_worksheet.py
    python3 scripts/x_worksheet.py --since 2012 --out /path/to/file.xlsx

なぜExcelなのか
--------------
集める作業は「検索して、URLをコピーして、貼る」の繰り返しになる。
3000人ぶんをテキストファイルでやると、どこまで進んだか分からなくなる。
1行1キャラの表にして、検索リンクと記入欄を同じ行に置く。

書き込むのは「ポストURL」の1列だけでよい。作品IDもキャラIDも触らなくていい。
書き終えたら取り込む。

    python3 scripts/x_worksheet.py --read 記入済み.xlsx > urls.txt
    python3 scripts/x_posts.py intake urls.txt
    python3 scripts/x_posts.py verify

シート構成
----------
  検索の型    コピペで使うXの検索クエリ。アカウント名を差し替えるだけ
  作品        作品ごとの公式アカウント一覧。未特定のものはここを埋める
  キャラ一覧  1行1キャラ。検索リンクと記入欄
"""
import argparse
import datetime
import os
import re
import sqlite3
import sys
import urllib.parse

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x_posts as X                                    # noqa: E402

ROOT = X.ROOT
OUT = os.path.join(ROOT, "corrections", "x_posts_記入用.xlsx")

HEAD_FILL = PatternFill("solid", fgColor="F2EDE6")
INPUT_FILL = PatternFill("solid", fgColor="FFF7D6")     # 記入する列
DONE_FILL = PatternFill("solid", fgColor="EAF3EA")      # 収集済みの行
LINK = Font(color="1155CC", underline="single", size=10)
BOLD = Font(bold=True, size=10)
SMALL = Font(size=10)

# 検索の型。%s にアカウント名が入る
TEMPLATES = [
    ("プロフィール型をまとめて探す",
     "from:%s (キャラクター紹介 OR キャラ紹介 OR 攻略キャラクター紹介 OR 登場人物)",
     "一番ほしい型。年齢やCV、人物の説明が入っている。"
     "公式が発売前に連投していることが多く、1回で複数キャラぶん取れる"),
    ("誕生日ポストをまとめて探す",
     "from:%s (誕生日 OR 誕生祭 OR anniversaire OR compleanno)",
     "描き下ろしイラスト付きで見栄えがよい。毎年投稿されるので数が多い。"
     "英語やフランス語で書く公式もあるので OR で並べる"),
    ("キャラ名で探す（取りこぼしを拾う)",
     "from:%s キャラ名",
     "上2つで出てこなかった人に使う。キャラ名は姓か名のどちらか一語だけにする。"
     "フルネームだと表記ゆれで外れる"),
    ("そのキャラの投稿を古い順に見る",
     "from:%s キャラ名 until:2030-01-01",
     "紹介は発売前に投稿されていることが多い。古い順に見ると見つかりやすい"),
]


def link(ws, cell, url, label):
    cell.value = label
    cell.hyperlink = url
    cell.font = LINK


def sheet_templates(wb):
    ws = wb.create_sheet("検索の型")
    ws["A1"] = "Xの検索欄にそのまま貼る型。◯◯◯ を作品の公式アカウント名に置き換える"
    ws["A1"].font = Font(bold=True, size=12)
    ws["A2"] = ("アカウント名は「作品」シートに入っている。@ は付けない。"
                "検索したあと「最新」タブに切り替えると古い投稿まで辿れる")
    ws["A2"].font = SMALL
    r = 4
    for name, tpl, why in TEMPLATES:
        ws.cell(r, 1, name).font = BOLD
        c = ws.cell(r, 2, tpl % "◯◯◯")
        c.font = Font(name="Menlo", size=11)
        ws.cell(r + 1, 2, why).font = SMALL
        ws.cell(r + 1, 2).alignment = Alignment(wrap_text=True, vertical="top")
        r += 3
    ws.cell(r, 1, "拾わないもの").font = BOLD
    for i, t in enumerate([
            "ショップ（K-BOOKSなど）やファンの投稿。公式アカウント以外は取り込み時に弾かれる",
            "舞台版・アニメ版など別の公式アカウント。使いたい場合は先に相談",
            "グッズの発売告知、イベント告知、ラジオの回。人物の説明が無いので載せない",
            "キャラ全員が並んでいるだけの投稿。誰の紹介か決められない"]):
        ws.cell(r + 1 + i, 2, t).font = SMALL
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 96
    return ws


def sheet_works(wb, con, accounts, got):
    ws = wb.create_sheet("作品")
    head = ["作品", "年", "vid", "公式アカウント", "候補（公式サイトから自動抽出）",
            "プロフィール型を検索", "誕生日を検索", "公式サイト", "残り", "全"]
    for i, h in enumerate(head, 1):
        c = ws.cell(1, i, h)
        c.font = BOLD
        c.fill = HEAD_FILL
    cand = {r["vid"]: r for r in X.read_tsv(
        os.path.join(ROOT, "corrections", "_queue_x_accounts.tsv"))}
    sites = {}
    for vid, url in con.execute(
            "select vid, url from work_link where site='website'"):
        sites.setdefault(vid, url)

    rows = con.execute("""
        select w.vid, w.title, w.year, w.votecount, count(c.cid) n
        from work w join character c on c.main_vid = w.vid
        group by w.vid order by w.votecount desc, w.year desc""").fetchall()
    r = 2
    for w in rows:
        cids = [x for x, in con.execute(
            "select cid from character where main_vid=?", (w["vid"],))]
        left = sum(1 for c in cids if c not in got)
        handles = sorted(accounts.get(w["vid"], []))
        acct = handles[0] if handles else ""
        ws.cell(r, 1, w["title"]).font = SMALL
        ws.cell(r, 2, w["year"]).font = SMALL
        ws.cell(r, 3, w["vid"]).font = SMALL
        c = ws.cell(r, 4, ("@" + acct) if acct else "")
        c.font = SMALL
        if not acct:
            c.fill = INPUT_FILL
        ws.cell(r, 5, (cand.get(w["vid"]) or {}).get("account", "")).font = SMALL
        if acct:
            for col, tpl in ((6, TEMPLATES[0][1]), (7, TEMPLATES[1][1])):
                q = tpl % acct
                link(ws, ws.cell(r, col),
                     "https://x.com/search?q=%s&f=live" % urllib.parse.quote(q), "検索")
        if sites.get(w["vid"]):
            link(ws, ws.cell(r, 8), sites[w["vid"]], "公式サイト")
        ws.cell(r, 9, left).font = SMALL
        ws.cell(r, 10, len(cids)).font = SMALL
        if left == 0:
            for i in range(1, 11):
                ws.cell(r, i).fill = DONE_FILL
        r += 1
    for col, wd in zip("ABCDEFGHIJ", (40, 6, 8, 20, 34, 16, 14, 12, 6, 6)):
        ws.column_dimensions[col].width = wd
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:J%d" % (r - 1)
    return ws


def sheet_chars(wb, con, accounts, got, since):
    ws = wb.create_sheet("キャラ一覧")
    head = ["ポストURL（ここに貼る）", "状態", "作品", "年", "キャラ名", "役割", "CV",
            "この人を検索", "公式アカウント", "cid", "vid", "メモ"]
    for i, h in enumerate(head, 1):
        c = ws.cell(1, i, h)
        c.font = BOLD
        c.fill = HEAD_FILL
    ws.cell(1, 1).fill = INPUT_FILL

    rows = con.execute("""
        select c.cid, c.name, c.role_label, c.cv, w.vid, w.title, w.year, w.votecount
        from character c join work w on w.vid = c.main_vid
        where w.year >= ? or w.year is null
        order by w.votecount desc, w.vid, c.role_label desc, c.cid""", (since,)).fetchall()
    r = 2
    for ch in rows:
        handles = sorted(accounts.get(ch["vid"], []))
        acct = handles[0] if handles else ""
        done = ch["cid"] in got
        ws.cell(r, 1).fill = DONE_FILL if done else INPUT_FILL
        ws.cell(r, 2, "済" if done else "").font = SMALL
        for col, v in ((3, ch["title"]), (4, ch["year"]), (5, ch["name"]),
                       (6, ch["role_label"]), (7, ch["cv"])):
            ws.cell(r, col, v).font = SMALL
        if acct and not done:
            base = re.sub(r"[（(][^）)]*[）)]", "", ch["name"]).strip()
            key = max(re.split(r"[\s・･]", base) or [base], key=len)
            q = "from:%s %s" % (acct, key)
            link(ws, ws.cell(r, 8),
                 "https://x.com/search?q=%s&f=live" % urllib.parse.quote(q), "検索")
        ws.cell(r, 9, ("@" + acct) if acct else "").font = SMALL
        ws.cell(r, 10, ch["cid"]).font = SMALL
        ws.cell(r, 11, ch["vid"]).font = SMALL
        r += 1
    for col, wd in zip("ABCDEFGHIJKL", (46, 6, 34, 6, 22, 10, 16, 10, 20, 10, 8, 30)):
        ws.column_dimensions[col].width = wd
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = "A1:L%d" % (r - 1)
    return ws


def build(args):
    con = sqlite3.connect(X.DB)
    con.row_factory = sqlite3.Row
    accounts = X.load_accounts()
    got = {r["cid"] for r in X.read_tsv(X.POSTS)}

    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("はじめに")
    ws["A1"] = "キャラクター紹介Xポストの収集表"
    ws["A1"].font = Font(bold=True, size=14)
    for i, t in enumerate([
            "",
            "やること: 「キャラ一覧」シートの A列に、そのキャラを紹介している公式ポストのURLを貼る。それだけ。",
            "作品IDもキャラIDも触らなくてよい。どのキャラの投稿かは取り込み時に本文から判定する。",
            "",
            "1. 「検索の型」シートの型をコピーして、アカウント名を差し替えてXで検索する",
            "   （各行の「検索」リンクを押せば、その型で検索した状態で開く）",
            "2. それらしい投稿の「リンクをコピー」して、その人の行のA列に貼る",
            "3. 1人に複数あってよい。同じ行に空白か改行で並べて貼ってよい",
            "",
            "取り込みは以下。書き終えたブックを渡してもらえればこちらでやる。",
            "   python3 scripts/x_worksheet.py --read この表.xlsx > urls.txt",
            "   python3 scripts/x_posts.py intake urls.txt",
            "   python3 scripts/x_posts.py verify",
            "",
            "「作品」シートの公式アカウントが空の作品は、先にそこを埋める。",
            "候補列に公式サイトから自動で拾ったものが入っているので、正しければ左に写す。",
            "", "作成: " + datetime.date.today().isoformat()]):
        ws.cell(3 + i, 1, t).font = SMALL if i else Font(size=10)
    ws.column_dimensions["A"].width = 110

    sheet_templates(wb)
    sheet_works(wb, con, accounts, got)
    sheet_chars(wb, con, accounts, got, args.since)
    wb.save(args.out)
    print("%s" % args.out)
    print("  作品 %d件 / キャラ %d人（%d年以降）/ 収集済み %d人"
          % (con.execute("select count(*) from work").fetchone()[0],
             con.execute("select count(*) from character c join work w on w.vid=c.main_vid "
                         "where w.year>=?", (args.since,)).fetchone()[0],
             args.since, len(got)))


def read_back(path):
    """記入済みのブックからURLだけ抜く。intake に流し込むためのもの。"""
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["キャラ一覧"]
    n = 0
    for row in ws.iter_rows(min_row=2, min_col=1, max_col=1, values_only=True):
        v = row[0]
        if not v:
            continue
        for m in X.SCAN.finditer(str(v)):
            print("https://x.com/%s/status/%s" % (m.group(1), m.group(2)))
            n += 1
    print("# %d件" % n, file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--since", type=int, default=2012)
    ap.add_argument("--read", help="記入済みのブックからURLを抜き出して標準出力に流す")
    args = ap.parse_args()
    if args.read:
        return read_back(args.read)
    build(args)


if __name__ == "__main__":
    main()
