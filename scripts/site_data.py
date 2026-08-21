"""【フェーズ2】ブラウザ内で絞り込むための検索インデックスを書き出す。

  python3 scripts/site_data.py

声優・タグ・属性は語彙表に辞書化し、作品側はその添字だけを持つ（サイズを抑えるため）。
"""
import os, json, gzip, sqlite3
from collections import defaultdict
from common import DATA
import re
from series import build_series
from vndb_build import PLATFORM_JA
from site_config import (AGE_TIERS, IMAGE_MODE, age_tier, year_bucket,
                         year_label, year_sort)

# 訳が用意できていない英語のままの語は絞り込み候補に出さない
is_en = lambda t: bool(re.fullmatch(r"[\x20-\x7e]+", t or ""))

OUT = os.path.join(DATA, "site")

# 機種のグループ（VNDBのコード基準）
PLAT_GROUP = [
    ("据置ゲーム機", {"ps1", "ps2", "ps3", "ps4", "ps5", "swi", "sw2", "xbo", "xxs",
                   "xb3", "sat", "drc", "nes", "sfc", "pce"}),
    ("携帯ゲーム機", {"psp", "psv", "nds", "n3d", "gba", "gbc"}),
    ("PC", {"win", "mac", "lin", "p88", "p98", "x68", "fmt", "fm7", "msx", "dos", "x1s"}),
    ("スマートフォン", {"and", "ios", "mob"}),
]
HOME = ("Switch", "PS", "ニンテンドー", "Xbox")


def main():
    con = sqlite3.connect(os.path.join(DATA, "vndb_otome.db"))
    cond = " OR ".join("platforms LIKE '%%%s%%'" % k for k in HOME)
    games = con.execute("""SELECT vid, title, title_latin, released, platforms,
                                  developers, rating, votecount, image_url, cv_status,
                                  minage
                           FROM games WHERE %s ORDER BY released DESC""" % cond).fetchall()
    vids = [g[0] for g in games]
    ph = ",".join("?" * len(vids))

    slug = {(k, key): url for k, key, url in
            con.execute("SELECT kind, key, url FROM slugs")}

    # 一覧カードの画像は site_build.py の cover_of() と同じものを使う。
    # ここだけ games.image_url（VNDB）を見ていたため、検索結果とページで
    # 表紙が食い違っていた（affiliate モードでもVNDBの画像が出ていた）
    shop_img = dict(con.execute("SELECT vid, url FROM shop_images"))
    # ダウンロード専売の作品は箱が存在しないので VNDB の画像に戻す
    dl_only = {r[0] for r in con.execute("SELECT vid FROM vndb_image_ok")}

    def cover(vid, vndb_url):
        if IMAGE_MODE == "vndb":
            return vndb_url
        return shop_img.get(vid) or (vndb_url if vid in dl_only else None)

    # 語彙表（ページ化されるものだけを絞り込み候補にする）
    def vocab(kind, sql):
        items = [r[0] for r in con.execute(sql % ph, vids)]
        pages = {key for k, key, p in
                 con.execute("SELECT kind, key, is_page FROM slugs") if k == kind and p}
        keep = [i for i in items if i in pages]
        if kind in ("tag",):
            keep = [i for i in keep if not is_en(i)]
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
    trs = [t for t in trs if t in tr_pages and not is_en(t.split(":", 1)[1])]
    tr_ix = {v: i for i, v in enumerate(trs)}

    # 別名義 → 代表名
    alias = dict(con.execute("SELECT alias, canonical FROM slug_aliases"))

    # スタッフ（人物ページを持つ人だけを絞り込み候補にする）
    person = {sid: (url, label) for sid, url, label in
              con.execute("SELECT sid, url, label FROM person_pages")}
    st_rows = con.execute("""SELECT sid, MAX(name), COUNT(DISTINCT vid) n,
                                    GROUP_CONCAT(DISTINCT role)
                             FROM staff_credits WHERE vid IN (%s)
                             GROUP BY sid HAVING n >= 2 ORDER BY n DESC""" % ph, vids).fetchall()
    staff = [(sid, nm, roles) for sid, nm, n, roles in st_rows if sid in person]
    st_ix = {sid: i for i, (sid, _, _) in enumerate(staff)}

    # シリーズ
    ser = build_series(con, set(vids))
    ser_list = sorted(ser.items(), key=lambda kv: -len(kv[1]["members"]))
    ser_ix = {k: i for i, (k, _) in enumerate(ser_list)}
    g_ser = {}
    for k, v in ser.items():
        for m in v["members"]:
            g_ser[m] = ser_ix[k]

    # 発売元
    pubs, pub_ix = vocab("publisher", """SELECT TRIM(value) FROM games,
        json_each('["' || REPLACE(REPLACE(publishers,'"','')," / ",'","') || '"]')
        WHERE vid IN (%s) AND publishers <> '' GROUP BY 1 ORDER BY COUNT(*) DESC""")
    g_pub = defaultdict(set)
    for vid, pv in con.execute("SELECT vid, publishers FROM games WHERE vid IN (%s)" % ph, vids):
        for d in (pv or "").split(" / "):
            if d.strip() in pub_ix:
                g_pub[vid].add(pub_ix[d.strip()])

    g_cv, g_tag, g_tr, g_pl, g_st = (defaultdict(set) for _ in range(5))
    for vid, sid in con.execute(
            "SELECT vid, sid FROM staff_credits WHERE vid IN (%s)" % ph, vids):
        if sid in st_ix:
            g_st[vid].add(st_ix[sid])
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

    code_of = {v: k for k, v in PLATFORM_JA.items()}

    def plat_group(label):
        code = code_of.get(label, label)
        for name, codes in PLAT_GROUP:
            if code in codes:
                return name
        return "その他"

    items = []
    for vid, title, latin, rel, plat, dev, rating, votes, img, cvst, minage in games:
        items.append({
            "v": vid,
            "u": slug.get(("game", vid)),
            "t": title,
            "l": latin,
            "r": rel,
            "p": sorted(g_pl.get(vid, [])),
            "c": sorted(g_cv.get(vid, [])),
            "k": sorted(g_tag.get(vid, [])),
            "s": sorted(g_st.get(vid, [])),
            "b": sorted(g_pub.get(vid, [])),
            "e": g_ser.get(vid),
            "d": dev,
            "g": rating,
            "n": votes,
            "i": cover(vid, img),
            "a": age_tier(minage),
            "y": year_bucket(rel),
        })

    index = {
        "generated": None,           # ビルド時刻はデプロイ時に埋める
        "count": len(items),
        "vocab": {
            "cv": [{"n": c, "u": slug.get(("cv", c))} for c in cvs],
            "tag": [{"n": t, "u": slug.get(("tag", t))} for t in tags],
            "platform": [{"n": p, "u": slug.get(("platform", p)),
                          "g": plat_group(p)} for p in plats],
            "staff": [{"n": nm, "r": (roles or "").split(",")[0],
                       "u": person[sid][0]} for sid, nm, roles in staff],
            "publisher": [{"n": p, "u": slug.get(("publisher", p))} for p in pubs],
            "series": [{"n": v["name"], "u": slug.get(("series", k)),
                        "c": len(v["members"])} for k, v in ser_list],
            "age": [{"v": k, "n": lab} for k, lab, _lo, _hi in AGE_TIERS],
            # 発売年は実際にある区分だけを新しい順に
            "year": [{"v": b, "n": year_label(b)} for b in
                     sorted({i["y"] for i in items if i["y"]}, key=year_sort, reverse=True)],
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

    # 入力補完と横断検索のための索引。検索欄を触ったときだけ読み込む
    sug = []
    for it in items:
        sug.append({"t": "作品", "n": it["t"], "k": it["l"], "u": it["u"]})
    cv_latin = dict(con.execute(
        "SELECT cv, MAX(cv_latin) FROM characters WHERE cv IS NOT NULL GROUP BY cv"))
    cv_url = {c: slug.get(("cv", c)) for c in cvs}
    for i, c in enumerate(cvs):
        sug.append({"t": "声優", "n": c, "u": cv_url[c], "i": i, "k": cv_latin.get(c)})
    st_latin = dict(con.execute(
        "SELECT sid, MAX(name_latin) FROM staff_credits GROUP BY sid"))
    for sid, nm, roles in staff:
        sug.append({"t": "スタッフ", "n": nm, "u": person[sid][0],
                    "r": (roles or "").split(",")[0], "k": st_latin.get(sid)})
    vid_ix = {it["v"]: i for i, it in enumerate(items)}
    chv = defaultdict(set)
    for cid, vid_ in con.execute(
            """SELECT cid, vid FROM characters
               WHERE vid IN (%s) AND role IN ('主人公','攻略対象')""" % ph, vids):
        if vid_ in vid_ix:
            chv[cid].add(vid_ix[vid_])
    for cid, nm, cvn, clat in con.execute(
            """SELECT cid, MAX(name), MAX(cv), MAX(name_latin) FROM characters
               WHERE vid IN (%s) AND role IN ('主人公','攻略対象') AND name IS NOT NULL
               GROUP BY cid""" % ph, vids):
        u = slug.get(("character", cid))
        if u:
            sug.append({"t": "キャラ", "n": nm, "u": u, "c": cvn, "k": clat,
                        "v": sorted(chv.get(cid, []))})

    os.makedirs(OUT, exist_ok=True)
    written = []
    for name, obj in (("index.json", index), ("traits.json", traits_file),
                      ("suggest.json", sug)):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
        path = os.path.join(OUT, name)
        open(path, "wb").write(body)
        written.append((name, len(body), len(gzip.compress(body, 9))))

    print("作品 %d件 / 語彙 声優%d・スタッフ%d・シリーズ%d・発売元%d・タグ%d・属性%d・機種%d"
          % (len(items), len(cvs), len(staff), len(ser_list), len(pubs),
             len(tags), len(trs), len(plats)))
    print()
    for name, raw, gz in written:
        print("  %-12s %7.1f KB  (gzip %6.1f KB)" % (name, raw / 1024, gz / 1024))
    print()
    print("  初回ロードは index.json のみ。")
    print("  検索欄に触れたとき suggest.json（%d件）、属性で絞るとき traits.json を追加取得する"
          % len(sug))
    print("-> %s" % OUT)
    con.close()


if __name__ == "__main__":
    main()
