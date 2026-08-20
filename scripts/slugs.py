"""【フェーズ2】URLスラッグを確定して slugs テーブルに入れる。

  python3 scripts/slugs.py

方式は案C：作品と声優は必ずVNDB IDを後置し、衝突を原理的に起こさない。
公開後にURLは変えられないので、ここで確定させる。
"""
import os, re, sqlite3, unicodedata
from collections import defaultdict, Counter
from common import DATA, ROOT
from vndb_build import PLATFORM_JA
from series import build_series

DB_DUMP = os.path.join(ROOT, "vndb", "db")
MAXLEN = 60                       # スラッグの最大長
HOME = ("Switch", "PS", "ニンテンドー", "Xbox")

# しきい値（これ未満は独立ページを作らない）
MIN_WORKS = {"cv": 2, "staff": 2, "tag": 3, "trait": 3, "maker": 2,
             "publisher": 2, "series": 2, "platform": 1, "game": 1,
             "character": 1}

DIR = {"game": "game", "cv": "cv", "staff": "staff", "tag": "tag",
       "trait": "trait", "platform": "platform", "maker": "maker",
       "publisher": "publisher", "series": "series",
       "character": "character"}

SCHEMA = """
DROP TABLE IF EXISTS slugs;
CREATE TABLE slugs (
    kind TEXT,        -- game / cv / tag / trait / platform / maker
    key TEXT,         -- DB内での識別子（vid、声優名、タグ名 など）
    slug TEXT,
    url TEXT,
    label TEXT,       -- 表示名（日本語）
    n_works INTEGER,
    is_page INTEGER   -- 1ならページを生成する
);
CREATE UNIQUE INDEX idx_slug_url ON slugs(url);
CREATE INDEX idx_slug_kind ON slugs(kind, key);
DROP TABLE IF EXISTS person_pages;
CREATE TABLE person_pages (sid TEXT PRIMARY KEY, url TEXT, label TEXT, kind TEXT);
DROP TABLE IF EXISTS slug_aliases;
CREATE TABLE slug_aliases (alias TEXT, canonical TEXT);
CREATE INDEX idx_alias ON slug_aliases(alias);
"""


def unesc(v):
    if v == "\\N":
        return None
    return (v.replace("\\t", "\t").replace("\\n", "\n").replace("\\\\", "\\")
            if "\\" in v else v)


def read(table):
    path = os.path.join(DB_DUMP, table)
    cols = open(path + ".header", encoding="utf-8").read().rstrip("\n").split("\t")
    with open(path, encoding="utf-8") as f:
        for line in f:
            yield dict(zip(cols, (unesc(v) for v in line.rstrip("\n").split("\t"))))


def slugify(s, maxlen=MAXLEN):
    """ローマ字文字列をURLスラッグにする。日本語しかない場合はNone"""
    if not s:
        return None
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"['’`~]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        return None
    if len(s) > maxlen:                       # ハイフン境界で切る
        cut = s[:maxlen]
        if "-" in cut[maxlen // 2:]:
            cut = cut[:cut.rfind("-")]
        s = cut.strip("-")
    return s or None


def main():
    con = sqlite3.connect(os.path.join(DATA, "vndb_otome.db"))
    cond = " OR ".join("platforms LIKE '%%%s%%'" % k for k in HOME)
    home = {r[0]: r for r in con.execute(
        "SELECT vid, title, title_latin, platforms, developers FROM games WHERE %s" % cond)}
    print("対象（家庭用機）: %d件" % len(home))
    ph = ",".join("?" * len(home)); L = list(home)

    rows = []          # (kind, key, slug, label, n, is_page)
    alias_rows = []    # (別名義, 代表名)
    person_rows = []   # (sid, url, label, kind)
    used = set()

    def add(kind, key, base, label, n, ident=None):
        """ident があれば必ず後置する（案C）。無い種別は衝突時のみ連番"""
        s = base or slugify(label) or re.sub(r"[^a-z0-9]+", "-", (key or "").lower()).strip("-")
        if not s:
            s = "x"
        if ident:
            s = "%s-%s" % (s, ident)
        url = "/%s/%s/" % (DIR[kind], s)
        if url in used:                        # ident なし種別の保険
            i = 2
            while "/%s/%s-%d/" % (DIR[kind], s, i) in used:
                i += 1
            s = "%s-%d" % (s, i)
            url = "/%s/%s/" % (DIR[kind], s)
        used.add(url)
        rows.append((kind, key, s, url, label, n, 1 if n >= MIN_WORKS[kind] else 0))

    # ---------- 作品 ----------
    for vid, (v, title, latin, plat, dev) in sorted(home.items()):
        add("game", vid, slugify(latin or title), title, 1, ident=vid)

    # ---------- 声優（staff IDで統合。別名義は1ページにまとめる） ----------
    name2sid, sid_latin = {}, {}
    for r in read("staff_alias"):
        name2sid.setdefault(r["name"], r["id"])
        if r["latin"]:
            sid_latin.setdefault(r["id"], r["latin"])
    cvs = con.execute("""SELECT cv, MAX(cv_latin), COUNT(DISTINCT vid) n FROM characters
                         WHERE cv IS NOT NULL AND vid IN (%s) GROUP BY cv""" % ph, L).fetchall()
    bysid = defaultdict(lambda: {"n": 0, "names": [], "latin": None})
    for name, latin, n in cvs:
        sid = name2sid.get(name, "s0-" + (slugify(latin) or "x"))
        e = bysid[sid]
        e["n"] += n
        e["names"].append((n, name))
        e["latin"] = e["latin"] or sid_latin.get(sid) or latin
    merged = sum(1 for e in bysid.values() if len(e["names"]) > 1)
    cv_sids = set()
    for sid, e in sorted(bysid.items(), key=lambda kv: -kv[1]["n"]):
        label = max(e["names"])[1]          # 一番多く使われている名義を代表にする
        add("cv", label, slugify(e["latin"]), label, e["n"], ident=sid)
        if e["n"] >= MIN_WORKS["cv"]:
            cv_sids.add(sid)
            person_rows.append((sid, rows[-1][3], label, "cv"))
        for _, alt in e["names"]:           # 別名義も同じページを指すよう記録
            if alt != label:
                alias_rows.append((alt, label))
    print("  声優の別名義を統合: %d人（%d名義）" % (merged, sum(len(e["names"]) for e in bysid.values() if len(e["names"]) > 1)))

    # ---------- スタッフ（声優ページがある人は作らない＝1人物1ページ） ----------
    st = con.execute("""SELECT sid, MAX(name), MAX(name_latin), COUNT(DISTINCT vid) n
                        FROM staff_credits WHERE vid IN (%s)
                        GROUP BY sid ORDER BY n DESC""" % ph, L).fetchall()
    n_skip = 0
    for sid, name, latin, n in st:
        if sid in cv_sids:
            n_skip += 1
            continue          # 声優ページ側にスタッフ参加も載せる
        add("staff", sid, slugify(latin), name, n, ident=sid)
        if n >= MIN_WORKS["staff"]:
            person_rows.append((sid, rows[-1][3], name, "staff"))
    print("  スタッフ %d人（うち %d人は声優ページに統合）" % (len(st), n_skip))

    # ---------- タグ（英語原文からスラッグ） ----------
    tag_en = {}
    for r in read("tags"):
        tag_en[r["name"]] = r["name"]
    from ja_labels import TAG_JA
    ja2en = {v: k for k, v in TAG_JA.items()}
    tags = con.execute("""SELECT tag, COUNT(DISTINCT vid) n FROM vn_tags
                          WHERE vid IN (%s) GROUP BY tag""" % ph, L).fetchall()
    for label, n in sorted(tags, key=lambda x: -x[1]):
        en = ja2en.get(label, label)                      # 未訳ならそのまま英語
        add("tag", label, slugify(en), label, n)

    # ---------- キャラ属性（グループ名を前置して曖昧さを消す） ----------
    from ja_labels import TRAIT_JA, TRAIT_GROUP_JA
    ja_group_en = {v: k for k, v in TRAIT_GROUP_JA.items()}
    rev = {}
    for k, v in TRAIT_JA.items():
        g_en, n_en = k.split(" > ")
        rev[(TRAIT_GROUP_JA.get(g_en, g_en), v)] = (g_en, n_en)
    trs = con.execute("""SELECT category, trait, COUNT(DISTINCT vid) n FROM traits
                         WHERE vid IN (%s) GROUP BY category, trait""" % ph, L).fetchall()
    for cat, label, n in sorted(trs, key=lambda x: -x[2]):
        g_en, n_en = rev.get((cat, label), (ja_group_en.get(cat, cat), label))
        add("trait", "%s:%s" % (cat, label), slugify("%s-%s" % (g_en, n_en)), label, n)

    # ---------- 機種（VNDBのコードをスラッグに） ----------
    code_of = {v: k for k, v in PLATFORM_JA.items()}
    READABLE = {"swi": "switch", "sw2": "switch-2", "psv": "ps-vita", "psp": "psp",
                "ps1": "ps", "ps2": "ps2", "ps3": "ps3", "ps4": "ps4", "ps5": "ps5",
                "nds": "nintendo-ds", "n3d": "nintendo-3ds", "win": "windows"}
    plats = con.execute("""SELECT platform, COUNT(DISTINCT vid) n FROM platforms
                           WHERE vid IN (%s) GROUP BY platform""" % ph, L).fetchall()
    for label, n in sorted(plats, key=lambda x: -x[1]):
        code = code_of.get(label, label)
        add("platform", label, READABLE.get(code, slugify(code) or slugify(label)), label, n)

    # ---------- キャラクター（主人公と攻略対象のみ。サブキャラは作らない） ----------
    ch = con.execute("""SELECT cid, MAX(name), MAX(name_latin), COUNT(DISTINCT vid) n
                        FROM characters
                        WHERE vid IN (%s) AND role IN ('主人公','攻略対象')
                          AND name IS NOT NULL
                        GROUP BY cid ORDER BY n DESC, cid""" % ph, L).fetchall()
    for cid, name, latin, n in ch:
        add("character", cid, slugify(latin), name, n, ident=cid)
    print("  キャラクター %d体（主人公・攻略対象のみ）" % len(ch))

    # ---------- シリーズ（関連作品のグラフから判定） ----------
    ser = build_series(con, set(home))
    for key, v in sorted(ser.items(), key=lambda kv: -len(kv[1]["members"])):
        add("series", key, slugify(v["latin"] or v["name"]), v["name"],
            len(v["members"]), ident=key)
    print("  シリーズ %d件（所属 %d作品）"
          % (len(ser), sum(len(v["members"]) for v in ser.values())))

    # ---------- メーカー ----------
    prod_latin = {}
    for r in read("producers"):
        if r["latin"]:
            prod_latin[r["name"]] = r["latin"]
    for kind, col in (("maker", "developers"), ("publisher", "publishers")):
        cnt = Counter()
        for (val,) in con.execute("SELECT %s FROM games WHERE vid IN (%s)" % (col, ph), L):
            for d in (val or "").split(" / "):
                if d.strip():
                    cnt[d.strip()] += 1
        for name, n in cnt.most_common():
            add(kind, name, slugify(prod_latin.get(name)), name, n)

    con.executescript(SCHEMA)
    con.executemany("INSERT INTO slugs VALUES (?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO slug_aliases VALUES (?,?)", alias_rows)
    con.executemany("INSERT OR IGNORE INTO person_pages VALUES (?,?,?,?)", person_rows)
    con.commit()

    print()
    print("=== 生成したスラッグ ===")
    total = 0
    for kind in ("game", "character", "cv", "staff", "trait", "tag", "series",
                 "maker", "publisher", "platform"):
        allc = sum(1 for r in rows if r[0] == kind)
        page = sum(1 for r in rows if r[0] == kind and r[6])
        total += page
        print("  %-9s %5d件中 %5d件をページ化（しきい値 %d作品以上）"
              % (kind, allc, page, MIN_WORKS[kind]))
    print("  %-9s %5s       %5d ページ" % ("合計", "", total))

    dup = con.execute("SELECT COUNT(*) - COUNT(DISTINCT url) FROM slugs").fetchone()[0]
    print()
    print("URL重複: %d件" % dup)
    print()
    print("=== 例 ===")
    for kind in ("game", "character", "cv", "staff", "tag", "trait", "series",
                 "maker", "publisher", "platform"):
        for k, u, lab in con.execute(
                "SELECT key,url,label FROM slugs WHERE kind=? AND is_page=1 ORDER BY n_works DESC LIMIT 2",
                (kind,)):
            print("  %-9s %-46s %s" % (kind, u, lab))
    con.close()


if __name__ == "__main__":
    main()
