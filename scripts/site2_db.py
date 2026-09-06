# -*- coding: utf-8 -*-
"""【v2 フェーズ1】新デザイン「オトメ棚」向けの表示用DBを作る。

    python3 scripts/site2_db.py

原本の data/vndb_otome.db は読むだけで一切書き換えない。
そこから「新サイトが1ページ描くのに必要な形」へ組み替えたものを
data/site2.db に出す。site2_build.py はこのDBだけを見ればよい。

なぜ別DBにするか:
  原本は VNDB のダンプをほぼそのまま持つ「素材」で、1ページ描くのに
  games / characters / traits / editions / offers / slugs / staff_credits を
  毎回突き合わせる必要がある。表示のたびに同じ集計（攻略人数・版の数・
  シリーズ・好み一致用の属性ベクトル）をやり直すのは無駄で、
  何より原本を触らずに済むので現行サイトが絶対に壊れない。

原本に無いものは作らない:
  日本語あらすじ・スクリーンショット・店舗特典・送料・レビュー引用は
  DBに存在しないので列だけ用意して空のままにする。
  年齢区分は CERO の公式レーティングではなく VNDB の minage が元なので、
  site_config.AGE_TIERS の「目安」表記をそのまま引き継ぐ。
"""
import os, re, sys, sqlite3, datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA
from series import build_series
from site_config import AGE_TIERS, age_tier, IMAGE_MODE

SRC = os.path.join(DATA, "vndb_otome.db")
DST = os.path.join(DATA, "site2.db")

# 現行サイトと同じ収録範囲（家庭用機で遊べるもの）
HOME = ("Switch", "PS", "ニンテンドー", "Xbox")

AGE_LABEL = {k: lab for k, lab, _lo, _hi in AGE_TIERS}

# カードに1つだけ出す機種の優先順。games.platforms はアルファベット順に
# 並んでいるので、そのまま先頭を取ると「Android」になる作品がある。
# このサイトは家庭用機で遊ぶ人向けなので、据置・携帯機を先に出す。
PLAT_PRIORITY = [
    "Nintendo Switch 2", "Nintendo Switch", "PS5", "PS4", "PS Vita", "PS3", "PSP", "PS2", "PS",
    "ニンテンドー3DS", "ニンテンドーDS", "Wii", "Xbox Series X/S", "Xbox One", "Xbox 360",
    "Windows", "Mac", "Linux", "iOS", "Android",
]


def top_platform(platforms):
    xs = [x for x in (platforms or "").split(" / ") if x]
    for p in PLAT_PRIORITY:
        if p in xs:
            return p
    return xs[0] if xs else ""

# 公式サイト系のリンクだけ出す。ストアは購入導線が別にあるので混ぜない
LINK_LABEL = {
    "website": "サイト",
    "twitter": "公式X（Twitter）",
    "wikidata": "Wikidata",
    "egs": "ErogameScape",
}

SCHEMA = """
PRAGMA journal_mode=OFF;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

-- 作品1行。ヒーロー・カード・facts表がこの1行で描ける
CREATE TABLE work (
  vid TEXT PRIMARY KEY, url TEXT, slug TEXT,
  title TEXT, title_latin TEXT,
  brand TEXT, brand_url TEXT, publisher TEXT, publisher_url TEXT,
  released TEXT, released_ja TEXT, year INTEGER, is_upcoming INTEGER,
  rating REAL, votecount INTEGER, rating_text TEXT,
  length_label TEXT, length_minutes INTEGER,
  minage INTEGER, age_key TEXT, age_label TEXT,
  voiced TEXT,
  n_capture INTEGER, n_char INTEGER, n_platform INTEGER, n_edition INTEGER,
  platforms TEXT, platform_top TEXT,
  cover TEXT,
  description TEXT, description_ja TEXT,     -- description_ja は現状すべて空
  genre_label TEXT,
  series_key TEXT, series_name TEXT,
  sort_date TEXT
);
CREATE INDEX ix_work_date ON work(sort_date DESC);
CREATE INDEX ix_work_up   ON work(is_upcoming);

CREATE TABLE work_char (
  vid TEXT, cid TEXT, name TEXT, name_latin TEXT,
  role TEXT, role_label TEXT, sex TEXT,
  cv TEXT, cv_url TEXT, image_url TEXT,
  birthday TEXT, age INTEGER, height INTEGER, blood TEXT,
  sort INTEGER
);
CREATE INDEX ix_wc_vid ON work_char(vid, sort);

-- 属性。キャラのチップにも、好み一致の計算にも使う
CREATE TABLE work_trait (vid TEXT, cid TEXT, cat TEXT, trait TEXT, url TEXT);
CREATE INDEX ix_wt_vid ON work_trait(vid);
CREATE INDEX ix_wt_tr  ON work_trait(trait);

CREATE TABLE work_tag   (vid TEXT, tag TEXT, url TEXT, cat TEXT);
CREATE INDEX ix_wtag_vid ON work_tag(vid);
CREATE TABLE work_staff (vid TEXT, sid TEXT, role TEXT, name TEXT, url TEXT, sort INTEGER);
CREATE INDEX ix_ws_vid ON work_staff(vid, sort);
CREATE TABLE work_link  (vid TEXT, site TEXT, label TEXT, url TEXT);
CREATE INDEX ix_wl_vid ON work_link(vid);
CREATE TABLE work_series (vid TEXT, member_vid TEXT, member_title TEXT,
                          member_url TEXT, member_released TEXT, sort INTEGER);
CREATE INDEX ix_wsr_vid ON work_series(vid, sort);

-- 購入導線。デザインの「機種 → 版 → 販売店」の3段がそのまま入る
CREATE TABLE work_edition (
  vid TEXT, eid TEXT, platform TEXT, platform_ja TEXT,
  plat_group INTEGER, plat_sort TEXT,
  rel_title TEXT, edition_label TEXT, edition_kind TEXT, edition_rank INTEGER,
  released TEXT, gtin TEXT, is_dl INTEGER, n_vn INTEGER,
  is_combo INTEGER,          -- 合本。値段はこの作品の相場ではないので出さない
  sort INTEGER
);
CREATE INDEX ix_we_vid ON work_edition(vid, sort);
CREATE TABLE work_offer (
  eid TEXT, vid TEXT, channel TEXT, via TEXT, condition TEXT,
  link_type TEXT, url TEXT, price INTEGER, fetched_at TEXT,
  availability TEXT, priority INTEGER
);
CREATE INDEX ix_wo_eid ON work_offer(eid, priority);

-- キャラクター1人1行。同じキャラが続編にも出るので、作品をまたいでまとめる。
-- 掲載637作品の主人公・攻略対象で、延べ4,984件を3,709人に畳んでいる。
CREATE TABLE character (
  cid TEXT PRIMARY KEY, url TEXT, slug TEXT, kana_key TEXT,
  name TEXT, name_latin TEXT,
  role TEXT, role_label TEXT, sex TEXT,
  cv TEXT, cv_url TEXT,
  birthday TEXT, age INTEGER, height INTEGER, weight INTEGER, blood TEXT,
  image_url TEXT,
  n_work INTEGER, main_vid TEXT, main_title TEXT, main_url TEXT, main_released TEXT
);
CREATE INDEX ix_ch_cv ON character(cv);
CREATE TABLE character_work (cid TEXT, vid TEXT, role TEXT, role_label TEXT, sort TEXT);
CREATE INDEX ix_cw2 ON character_work(cid, sort DESC);
CREATE TABLE character_trait (cid TEXT, cat TEXT, trait TEXT, url TEXT);
CREATE INDEX ix_ct2 ON character_trait(cid);

-- 索引（声優・メーカー・シリーズ・発売元・スタッフ・タグ・属性・機種）
CREATE TABLE catalog (
  kind TEXT, key TEXT, url TEXT, label TEXT, reading TEXT,
  n_works INTEGER, cover TEXT, top_work TEXT
);
-- reading には「表示名そのもののローマ字」を入れる。
-- スラッグから読みを取ると五十音の行を間違える。声優ページは別名義を1本に
-- まとめており、URLはVNDBの代表表記、表示名は担当作の一番多い名義になるため、
-- 「長谷川 育美 → /cv/akabane-kyouko-s14378/」のようにズレる。
-- 実データではスラッグ由来だと長谷川さんが「あ行」に並んでいた。
CREATE INDEX ix_cat ON catalog(kind, n_works DESC);
CREATE UNIQUE INDEX ix_cat_url ON catalog(url);
CREATE TABLE catalog_work (kind TEXT, url TEXT, vid TEXT, role TEXT, sort TEXT);
CREATE INDEX ix_cw ON catalog_work(url, sort);
"""

JA_W = "日月火水木金土"


def ja_date(iso):
    if not iso:
        return ""
    p = iso.split("-")
    if len(p) == 3:
        try:
            d = datetime.date(int(p[0]), int(p[1]), int(p[2]))
            return "%d年%d月%d日（%s）" % (d.year, d.month, d.day, JA_W[(d.weekday() + 1) % 7])
        except ValueError:
            pass
    if len(p) >= 2:
        return "%s年%s月" % (p[0], p[1].lstrip("0"))
    return "%s年" % p[0]


def role_label(role, sex):
    """VNDB の primary は「主要キャラ」の意味で、乙女ゲームの攻略対象とは違う。
    男性のときだけ攻略対象と言い切る（site_build.py と同じ判断）"""
    if role == "攻略対象":
        return "攻略対象" if sex == "m" else "主要キャラ"
    return role or ""


def norm_title(t):
    z = {ord(c): ord(c) - 0xFEE0 for c in "".join(chr(0xFF01 + i) for i in range(94))}
    t = (t or "").translate(z).lower().replace("×", "x").replace("✕", "x")
    return re.sub(r"[^0-9a-z぀-ヿ一-鿿]", "", t)


def main():
    if not os.path.exists(SRC):
        sys.exit("原本が見つからない: %s" % SRC)
    src = sqlite3.connect(SRC)
    src.row_factory = sqlite3.Row

    cond = " OR ".join("platforms LIKE '%%%s%%'" % k for k in HOME)
    games = {r["vid"]: r for r in src.execute("SELECT * FROM games WHERE %s" % cond)}

    # ページを作る作品だけを対象にする（slugs.is_page=1）
    slug_url, slug_label, slug_page = {}, {}, {}
    for r in src.execute("SELECT kind,key,url,label,n_works,is_page FROM slugs"):
        slug_url[(r["kind"], r["key"])] = r["url"]
        slug_label[(r["kind"], r["key"])] = r["label"]
        slug_page[(r["kind"], r["key"])] = (r["n_works"] or 0, r["is_page"])

    def page_url(kind, key):
        """実際に生成されるページだけを href にする（is_page=0 は 404 になる）"""
        n = slug_page.get((kind, key))
        return slug_url.get((kind, key)) if n and n[1] else None

    vids = [v for v in games if page_url("game", v)]
    vids.sort()
    ph = ",".join("?" * len(vids))
    print("対象作品: %d件" % len(vids))

    today = datetime.date.today().isoformat()

    if os.path.exists(DST):
        os.remove(DST)
    dst = sqlite3.connect(DST)
    dst.executescript(SCHEMA)

    # ---- 付随データをまとめて読む ----
    chars = defaultdict(list)
    for r in src.execute("SELECT * FROM characters WHERE vid IN (%s)" % ph, vids):
        chars[r["vid"]].append(r)
    traits = defaultdict(list)
    for r in src.execute("SELECT * FROM traits WHERE vid IN (%s)" % ph, vids):
        traits[r["vid"]].append(r)
    tags = defaultdict(list)
    for r in src.execute("SELECT * FROM vn_tags WHERE vid IN (%s)" % ph, vids):
        tags[r["vid"]].append(r)
    staff = defaultdict(list)
    for r in src.execute("SELECT * FROM staff_credits WHERE vid IN (%s)" % ph, vids):
        staff[r["vid"]].append(r)
    links = defaultdict(list)
    for r in src.execute("SELECT * FROM vndb_links WHERE vid IN (%s)" % ph, vids):
        links[r["vid"]].append(r)
    eds = defaultdict(list)
    for r in src.execute("""SELECT * FROM editions WHERE vid IN (%s)
                            ORDER BY plat_group, plat_sort DESC, platform,
                                     is_dl, edition_rank, released""" % ph, vids):
        eds[r["vid"]].append(r)
    offers = defaultdict(list)
    for r in src.execute("SELECT * FROM offers WHERE vid IN (%s) ORDER BY priority" % ph, vids):
        offers[r["eid"]].append(r)
    plats = defaultdict(list)
    for r in src.execute("SELECT vid,platform FROM platforms WHERE vid IN (%s)" % ph, vids):
        plats[r["vid"]].append(r["platform"])

    shop_img = dict(src.execute("SELECT vid, url FROM shop_images"))
    dl_only = {r[0] for r in src.execute("SELECT vid FROM vndb_image_ok")}
    person = {sid: (u, lab, kind) for sid, u, lab, kind in
              src.execute("SELECT sid,url,label,kind FROM person_pages")}
    sid_of_cv = {lab: sid for sid, (u, lab, k) in person.items() if k == "cv"}
    alias_canon = dict(src.execute("SELECT alias, canonical FROM slug_aliases"))

    def cover_of(g):
        """公開できる表紙だけ。affiliate モードでは楽天由来の画像、
        パッケージが存在しないDL専売だけ VNDB の画像に戻す"""
        if IMAGE_MODE == "vndb":
            return g["image_url"]
        return shop_img.get(g["vid"]) or (g["image_url"] if g["vid"] in dl_only else None)

    def cv_url(name):
        """別名義は代表名義のページに寄せる"""
        canon = alias_canon.get(name, name)
        sid = sid_of_cv.get(canon)
        if sid and sid in person:
            return person[sid][0]
        return page_url("cv", canon)

    series = build_series(src, set(vids))
    series_of = {}
    for key, v in series.items():
        for m in v["members"]:
            series_of[m] = key

    # ---- work ----
    W, WC, WT, WG, WS, WL, WSR, WE, WO = [], [], [], [], [], [], [], [], []
    for vid in vids:
        g = games[vid]
        cs = sorted(chars[vid], key=lambda c: (
            {"主人公": 0, "攻略対象": 1, "サブキャラ": 2, "登場のみ": 3}.get(c["role"], 4),
            c["name"] or ""))
        n_capture = sum(1 for c in cs if c["role"] == "攻略対象" and c["sex"] == "m")
        ed = eds[vid]
        rel = g["released"] or ""
        # ジャンル行。日本語のタグから内容カテゴリのものを2つまで
        gtags = [t["tag"] for t in tags[vid] if t["category"] == "内容"
                 and not re.fullmatch(r"[\x20-\x7e]+", t["tag"] or "")]
        genre = " / ".join(["乙女ゲーム"] + gtags[:2])
        skey = series_of.get(vid)
        W.append((
            vid, page_url("game", vid), (page_url("game", vid) or "").strip("/").split("/")[-1],
            g["title"], g["title_latin"],
            (g["developers"] or "").split(" / ")[0],
            page_url("maker", (g["developers"] or "").split(" / ")[0]),
            (g["publishers"] or "").split(" / ")[0],
            page_url("publisher", (g["publishers"] or "").split(" / ")[0]),
            rel, ja_date(rel), int(rel[:4]) if rel[:4].isdigit() else None,
            1 if rel and rel >= today else 0,
            g["rating"], g["votecount"],
            ("★ %.2f" % g["rating"]) if g["rating"] else "評価なし",
            g["length"], g["length_minutes"],
            g["minage"], age_tier(g["minage"]), AGE_LABEL.get(age_tier(g["minage"]), ""),
            g["voiced"],
            n_capture, len(cs), len(set(plats[vid])), len(ed),
            g["platforms"], top_platform(g["platforms"]),
            cover_of(g),
            g["description"], g["description_ja"],
            genre,
            skey, series[skey]["name"] if skey else None,
            rel or "0000",
        ))

        for i, c in enumerate(cs):
            WC.append((vid, c["cid"], c["name"], c["name_latin"], c["role"],
                       role_label(c["role"], c["sex"]), c["sex"],
                       c["cv"], cv_url(c["cv"]) if c["cv"] else None,
                       c["image_url"] if IMAGE_MODE == "vndb" else None,
                       c["birthday"], c["age"], c["height"], c["blood"], i))
        for t in traits[vid]:
            WT.append((vid, t["cid"], t["category"], t["trait"],
                       page_url("trait", "%s:%s" % (t["category"], t["trait"]))
                       or page_url("trait", t["trait"])))
        for t in tags[vid]:
            WG.append((vid, t["tag"], page_url("tag", t["tag"]), t["category"]))
        for i, s in enumerate(staff[vid]):
            u = person[s["sid"]][0] if s["sid"] in person else page_url("staff", s["sid"])
            WS.append((vid, s["sid"], s["role"], s["name"], u, i))
        seen_link = set()
        for l in links[vid]:
            if l["site"] in LINK_LABEL and l["url"] and l["url"] not in seen_link:
                seen_link.add(l["url"])
                WL.append((vid, l["site"], LINK_LABEL[l["site"]], l["url"]))
        if skey:
            for i, m in enumerate(series[skey]["members"]):
                if m in games:
                    WSR.append((vid, m, games[m]["title"], page_url("game", m),
                                games[m]["released"], i))

        title_n = norm_title(g["title"])
        for i, r in enumerate(ed):
            combo = 1 if ((r["n_vn"] or 1) >= 2
                          and norm_title(r["rel_title"] or "") != title_n) else 0
            WE.append((vid, r["eid"], r["platform"], r["platform_ja"], r["plat_group"],
                       r["plat_sort"], r["rel_title"], r["edition_label"], r["edition_kind"],
                       r["edition_rank"], r["released"], r["gtin"], r["is_dl"], r["n_vn"],
                       combo, i))
            for o in offers[r["eid"]]:
                WO.append((r["eid"], vid, o["channel"], o["via"], o["condition"],
                           o["link_type"], o["url"], o["price"], o["fetched_at"],
                           o["availability"], o["priority"]))

    dst.executemany("INSERT INTO work VALUES (%s)" % ",".join("?" * 35), W)
    dst.executemany("INSERT INTO work_char VALUES (%s)" % ",".join("?" * 15), WC)
    dst.executemany("INSERT INTO work_trait VALUES (?,?,?,?,?)", WT)
    dst.executemany("INSERT INTO work_tag VALUES (?,?,?,?)", WG)
    dst.executemany("INSERT INTO work_staff VALUES (?,?,?,?,?,?)", WS)
    dst.executemany("INSERT INTO work_link VALUES (?,?,?,?)", WL)
    dst.executemany("INSERT INTO work_series VALUES (?,?,?,?,?,?)", WSR)
    dst.executemany("INSERT INTO work_edition VALUES (%s)" % ",".join("?" * 16), WE)
    dst.executemany("INSERT INTO work_offer VALUES (%s)" % ",".join("?" * 11), WO)
    print("  work %d / char %d / trait %d / edition %d / offer %d"
          % (len(W), len(WC), len(WT), len(WE), len(WO)))

    # ---- キャラクター ----
    # 同じ cid が続編・ファンディスクにも出るので作品をまたいで1人にまとめる。
    # 代表作は「表紙があるもの > 票数が多いもの」。声優や年齢が作品ごとに違うことは
    # ほとんど無いが、違ったときは代表作の値を採る。
    ch_rows, chw_rows, cht_rows = [], [], []
    per_cid = defaultdict(list)
    for vid in vids:
        for c in chars[vid]:
            if c["role"] in ("主人公", "攻略対象"):
                per_cid[c["cid"]].append((vid, c))
    for cid, items in sorted(per_cid.items()):
        url = page_url("character", cid)
        if not url:
            continue
        items.sort(key=lambda x: (cover_of(games[x[0]]) is not None,
                                  games[x[0]]["votecount"] or 0), reverse=True)
        main_vid, c = items[0]
        g = games[main_vid]
        slug = url.strip("/").split("/")[-1]
        ch_rows.append((
            cid, url, slug, re.sub(r"-c\d+$", "", slug),
            c["name"], c["name_latin"],
            c["role"], role_label(c["role"], c["sex"]), c["sex"],
            c["cv"], cv_url(c["cv"]) if c["cv"] else None,
            c["birthday"], c["age"], c["height"], c["weight"], c["blood"],
            c["image_url"] if IMAGE_MODE == "vndb" else None,
            len(items), main_vid, g["title"], page_url("game", main_vid), g["released"]))
        for v, cc in items:
            chw_rows.append((cid, v, cc["role"], role_label(cc["role"], cc["sex"]),
                             games[v]["released"] or "0000"))
        seen_t = set()
        for t in traits[main_vid]:
            if t["cid"] != cid or (t["category"], t["trait"]) in seen_t:
                continue
            seen_t.add((t["category"], t["trait"]))
            cht_rows.append((cid, t["category"], t["trait"],
                             page_url("trait", "%s:%s" % (t["category"], t["trait"]))))
    dst.executemany("INSERT INTO character VALUES (%s)" % ",".join("?" * 22), ch_rows)
    dst.executemany("INSERT INTO character_work VALUES (?,?,?,?,?)", chw_rows)
    dst.executemany("INSERT INTO character_trait VALUES (?,?,?,?)", cht_rows)
    print("  キャラクター %d人（延べ %d件）/ 属性 %d"
          % (len(ch_rows), len(chw_rows), len(cht_rows)))

    # ---- catalog（索引）----
    # 作品との結び付きは「その索引ページに何を並べるか」そのもの
    cat_rows, cw_rows = [], []
    seen_url = set()

    # 訳が用意できていない英語のままの語はサイトに出さない（現行サイトと同じ規則）。
    # 人名・機種名は英語表記が正しいことがあるので、タグと属性にだけ効かせる。
    is_en = lambda t: bool(re.fullmatch(r"[\x20-\x7e]+", t or ""))

    def add_cat(kind, key, label, members, reading=""):
        reading = reading or ""
        url = page_url(kind, key)
        if not url or url in seen_url:
            return
        if kind in ("tag", "trait") and is_en(label):
            return
        ms = [v for v in members if v in games]
        if not ms:
            return
        seen_url.add(url)
        ms.sort(key=lambda v: (games[v]["released"] or "0000"), reverse=True)
        top = max(ms, key=lambda v: (cover_of(games[v]) is not None,
                                     games[v]["votecount"] or 0))
        cat_rows.append((kind, key, url, label, reading, len(ms),
                         cover_of(games[top]), games[top]["title"]))
        for v in ms:
            cw_rows.append((kind, url, v, "", games[v]["released"] or "0000"))

    by = defaultdict(set)
    for vid in vids:
        g = games[vid]
        for c in chars[vid]:
            if c["cv"]:
                by[("cv", alias_canon.get(c["cv"], c["cv"]))].add(vid)
        for d in (g["developers"] or "").split(" / "):
            if d:
                by[("maker", d)].add(vid)
        for p in (g["publishers"] or "").split(" / "):
            if p:
                by[("publisher", p)].add(vid)
        for s in staff[vid]:
            by[("staff", s["sid"])].add(vid)
        for t in tags[vid]:
            by[("tag", t["tag"])].add(vid)
        for t in traits[vid]:
            by[("trait", "%s:%s" % (t["category"], t["trait"]))].add(vid)
        for p in set(plats[vid]):
            by[("platform", p)].add(vid)
    for key, v in series.items():
        by[("series", key)] = set(v["members"])

    # 表示名 → ローマ字。声優はキャラ側の cv_latin、スタッフは name_latin、
    # シリーズは代表作の title_latin から取る
    latin_of = {}
    for name, latin in src.execute(
            "SELECT cv, MAX(cv_latin) FROM characters WHERE cv IS NOT NULL AND cv<>'' "
            "GROUP BY cv"):
        if latin:
            latin_of[("cv", name)] = latin
    for name, latin in src.execute(
            "SELECT name, MAX(name_latin) FROM staff_credits WHERE name IS NOT NULL "
            "GROUP BY name"):
        if latin:
            latin_of[("staff", name)] = latin
    for key, v in series.items():
        if v.get("latin"):
            latin_of[("series", key)] = v["latin"]

    for (kind, key), members in by.items():
        lab = slug_label.get((kind, key)) or key
        add_cat(kind, key, lab, members,
                reading=latin_of.get((kind, lab)) or latin_of.get((kind, key)) or "")

    dst.executemany("INSERT INTO catalog VALUES (?,?,?,?,?,?,?,?)", cat_rows)
    dst.executemany("INSERT INTO catalog_work VALUES (?,?,?,?,?)", cw_rows)
    n_by_kind = defaultdict(int)
    for r in cat_rows:
        n_by_kind[r[0]] += 1
    print("  catalog: " + " / ".join("%s %d" % (k, n) for k, n in sorted(n_by_kind.items())))

    # ---- meta（トップの数字はここから引く。手で書かない）----
    meta = {"built_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "today": today,
            "n_work": str(len(W)),
            "n_upcoming": str(sum(1 for r in W if r[12])),
            "n_char": str(len(WC)),
            "n_character": str(len(ch_rows))}
    for k, n in n_by_kind.items():
        meta["n_" + k] = str(n)
    dst.executemany("INSERT INTO meta VALUES (?,?)", sorted(meta.items()))

    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    print("→ %s (%.1fMB)" % (DST, os.path.getsize(DST) / 1e6))


if __name__ == "__main__":
    main()
