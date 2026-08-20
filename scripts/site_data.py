"""【フェーズ2】ブラウザ内で絞り込むための検索インデックスを書き出す。

  python3 scripts/site_data.py

声優・タグ・属性は語彙表に辞書化し、作品側はその添字だけを持つ（サイズを抑えるため）。
"""
import os, json, gzip, sqlite3
from collections import defaultdict
from common import DATA

OUT = os.path.join(DATA, "site")
HOME = ("Switch", "PS", "ニンテンドー", "Xbox")


def main():
    con = sqlite3.connect(os.path.join(DATA, "vndb_otome.db"))
    cond = " OR ".join("platforms LIKE '%%%s%%'" % k for k in HOME)
    games = con.execute("""SELECT vid, title, title_latin, released, platforms,
                                  developers, rating, votecount, image_url, cv_status
                           FROM games WHERE %s ORDER BY released DESC""" % cond).fetchall()
    vids = [g[0] for g in games]
    ph = ",".join("?" * len(vids))

    slug = {(k, key): url for k, key, url in
            con.execute("SELECT kind, key, url FROM slugs")}

    # 語彙表（ページ化されるものだけを絞り込み候補にする）
    def vocab(kind, sql):
        items = [r[0] for r in con.execute(sql % ph, vids)]
        pages = {key for k, key, p in
                 con.execute("SELECT kind, key, is_page FROM slugs") if k == kind and p}
        keep = [i for i in items if i in pages]
        return keep, {v: i for i, v in enumerate(keep)}

    cvs, cv_ix = vocab("cv", """SELECT cv FROM characters WHERE cv IS NOT NULL
                                AND vid IN (%s) GROUP BY cv ORDER BY COUNT(DISTINCT vid) DESC""")
    tags, tag_ix = vocab("tag", """SELECT tag FROM vn_tags WHERE vid IN (%s)
                                   GROUP BY tag ORDER BY COUNT(DISTINCT vid) DESC""")
    plats, pl_ix = vocab("platform", """SELECT platform FROM platforms WHERE vid IN (%s)
                                        GROUP BY platform ORDER BY COUNT(DISTINCT vid) DESC""")
    # 属性は category:trait をキーにしている
    tr_pages = {key for k, key, p in
                con.execute("SELECT kind, key, is_page FROM slugs") if k == "trait" and p}
    trs = [r[0] for r in con.execute(
        """SELECT category || ':' || trait FROM traits WHERE vid IN (%s)
           GROUP BY category, trait ORDER BY COUNT(DISTINCT vid) DESC""" % ph, vids)]
    trs = [t for t in trs if t in tr_pages]
    tr_ix = {v: i for i, v in enumerate(trs)}

    # 別名義 → 代表名
    alias = dict(con.execute("SELECT alias, canonical FROM slug_aliases"))

    g_cv, g_tag, g_tr, g_pl = (defaultdict(set) for _ in range(4))
    for vid, cv in con.execute("""SELECT vid, cv FROM characters
                                  WHERE cv IS NOT NULL AND vid IN (%s)""" % ph, vids):
        cv = alias.get(cv, cv)
        if cv in cv_ix:
            g_cv[vid].add(cv_ix[cv])
    for vid, t in con.execute("SELECT vid, tag FROM vn_tags WHERE vid IN (%s)" % ph, vids):
        if t in tag_ix:
            g_tag[vid].add(tag_ix[t])
    for vid, c, t in con.execute("SELECT vid, category, trait FROM traits WHERE vid IN (%s)" % ph, vids):
        k = "%s:%s" % (c, t)
        if k in tr_ix:
            g_tr[vid].add(tr_ix[k])
    for vid, p in con.execute("SELECT vid, platform FROM platforms WHERE vid IN (%s)" % ph, vids):
        if p in pl_ix:
            g_pl[vid].add(pl_ix[p])

    items = []
    for vid, title, latin, rel, plat, dev, rating, votes, img, cvst in games:
        items.append({
            "v": vid,
            "u": slug.get(("game", vid)),
            "t": title,
            "l": latin,
            "r": rel,
            "p": sorted(g_pl.get(vid, [])),
            "c": sorted(g_cv.get(vid, [])),
            "k": sorted(g_tag.get(vid, [])),
            "d": dev,
            "g": rating,
            "n": votes,
            "i": img,
        })

    index = {
        "generated": None,           # ビルド時刻はデプロイ時に埋める
        "count": len(items),
        "vocab": {
            "cv": [{"n": c, "u": slug.get(("cv", c))} for c in cvs],
            "tag": [{"n": t, "u": slug.get(("tag", t))} for t in tags],
            "platform": [{"n": p, "u": slug.get(("platform", p))} for p in plats],
        },
        "items": items,
    }

    # キャラ属性は1作品平均91個と重いので別ファイルにし、
    # 属性で絞り込むときだけ追加で読ませる
    traits_file = {
        "vocab": [{"n": t.split(":", 1)[1], "c": t.split(":", 1)[0],
                   "u": slug.get(("trait", t))} for t in trs],
        "items": {vid: sorted(g_tr[vid]) for vid in vids if g_tr.get(vid)},
    }

    os.makedirs(OUT, exist_ok=True)
    written = []
    for name, obj in (("index.json", index), ("traits.json", traits_file)):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
        path = os.path.join(OUT, name)
        open(path, "wb").write(body)
        written.append((name, len(body), len(gzip.compress(body, 9))))

    print("作品 %d件 / 語彙 声優%d・タグ%d・属性%d・機種%d"
          % (len(items), len(cvs), len(tags), len(trs), len(plats)))
    print()
    for name, raw, gz in written:
        print("  %-12s %7.1f KB  (gzip %6.1f KB)" % (name, raw / 1024, gz / 1024))
    print()
    print("  初回ロードは index.json のみ。属性で絞り込むとき traits.json を追加取得する")
    print("-> %s" % OUT)
    con.close()


if __name__ == "__main__":
    main()
