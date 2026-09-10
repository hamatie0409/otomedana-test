# -*- coding: utf-8 -*-
"""未収集キャラごとのX検索URLを作る。

X のキーワード検索は「レジス・ド・ルペルティエ」を丸ごと1語として索引する
ことがあり、「レジス」では引けないのに「ルペルティエ」では引ける。
だから名前を区切って、いちばん特徴のある断片を渡す。

    python3 scripts/x_queries.py v49601
"""
import os, re, sys, sqlite3, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x_posts as X
from common import DATA


def fragments(name):
    """検索に使う断片。長くて特徴のあるものを先に。"""
    parts = [p for p in re.split(r"[・\s　]+", name) if p]
    out = []
    if len(parts) > 1:
        # 姓・二つ名など。3文字以上の断片を長い順に
        out += sorted([p for p in parts if len(p) >= 3], key=len, reverse=True)
        out.append("".join(parts))
    out.append(name.replace(" ", "").replace("　", ""))
    seen, uniq = set(), []
    for f in out:
        if f and f not in seen:
            seen.add(f); uniq.append(f)
    return uniq[:2]


def main():
    vid = sys.argv[1]
    best = set(X.best_posts())
    acc = {r["vid"]: r["account"].strip() for r in X.read_tsv(X.ACCOUNTS)}
    handles = [h.lstrip("@") for h in acc.get(vid, "").split() if h.strip()]
    con = sqlite3.connect(os.path.join(DATA, "site2.db"))
    q = ("select cid,name from character where main_vid=? union "
         "select c.cid,c.name from character c join character_work w "
         "on w.cid=c.cid where w.vid=?")
    rows = [(c, n) for c, n in con.execute(q, (vid, vid)) if c not in best]
    title = con.execute("select title from work where vid=?", (vid,)).fetchone()
    print("# %s %s  未収集%d人  %s" % (vid, title[0] if title else "?",
                                    len(rows), " ".join("@"+h for h in handles)))
    if not handles:
        print("# アカウント未登録"); return
    h = handles[0]
    for cid, name in rows:
        for f in fragments(name):
            u = "https://x.com/search?q=" + urllib.parse.quote(
                "from:%s %s" % (h, f)) + "&f=live"
            print("%s\t%s\t%s" % (name, f, u))


if __name__ == "__main__":
    main()
