"""【フェーズ3】静的サイトを生成する。3,627ページ + sitemap + robots。

  python3 scripts/site_build.py

各ページはJSなしで読める実体のHTML。絞り込みだけをJSで足す。
"""
import os, re, json, html, shutil, sqlite3, datetime
from collections import defaultdict
from common import DATA, ROOT

from site_config import (SITE_NAME, SITE_DESC, SITE_URL, REPO_URL, SCOPE_NOTE,
                         PUBLISH, BASE_PATH)

# GitHub Pages は リポジトリ直下 か docs/ からしか配信できないため docs/ に出す
OUT = os.path.join(ROOT, "docs")
BASE_URL = SITE_URL + BASE_PATH
HOME = ("Switch", "PS", "ニンテンドー", "Xbox")

e = lambda s: html.escape(str(s), quote=True) if s is not None else ""


# ---------------------------------------------------------------- テンプレート

def layout(title, desc, canonical, body, jsonld=None, breadcrumb=None, og_image=None):
    crumb = ""
    if breadcrumb:
        parts = " ".join(
            '<li><a href="%s">%s</a></li>' % (e(u), e(t)) if u else "<li>%s</li>" % e(t)
            for t, u in breadcrumb)
        crumb = '<nav class="crumb"><ol>%s</ol></nav>' % parts
    ld = ""
    for obj in (jsonld or []):
        ld += '<script type="application/ld+json">%s</script>' % json.dumps(
            obj, ensure_ascii=False)
    return """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<meta name="description" content="%(desc)s">
<link rel="canonical" href="%(canon)s">
%(robots)s
<meta property="og:type" content="website">
<meta property="og:title" content="%(title)s">
<meta property="og:description" content="%(desc)s">
<meta property="og:url" content="%(canon)s">
<meta property="og:site_name" content="%(site)s">
%(ogimg)s<meta name="twitter:card" content="summary_large_image">
<link rel="stylesheet" href="/assets/style.css">
%(ld)s</head>
<body>
%(notice)s
<header class="site">
  <a class="brand" href="/">%(site)s</a>
  <form class="q" action="/" method="get"><input type="search" name="q" placeholder="作品・声優で探す" aria-label="検索"></form>
</header>
%(crumb)s
<main>
%(body)s
</main>
<footer class="site">
  <p><strong>%(site)s</strong> — %(sitedesc)s</p>
  <p class="src">作品・キャラクター・声優のデータは <a href="https://vndb.org/" rel="noopener">VNDB</a> より取得し、
  <a href="https://opendatacommons.org/licenses/odbl/1-0/" rel="noopener">Open Database License (ODbL)</a> のもとで利用しています。
  本サイトの派生データベースも同ライセンスで提供します。
  <a href="%(repo)s" rel="noopener">生成スクリプト</a>を公開しています。</p>
  <p class="src">価格・在庫は各ストアの情報です。最新の内容は各ストアでご確認ください。</p>
</footer>
<script>window.BASE_PATH=%(basepath)s;</script>\n<script src="/assets/app.js" defer></script>
</body>
</html>""" % dict(title=e(title), desc=e(desc), canon=e(canonical), site=e(SITE_NAME),
                  sitedesc=e(SITE_DESC), ld=ld, crumb=crumb, body=body, repo=e(REPO_URL),
                  basepath=json.dumps(BASE_PATH),
                  robots="" if PUBLISH else '<meta name="robots" content="noindex,nofollow">\n',
                  notice=('<p class="ad-notice">当サイトはアフィリエイト広告を利用しています</p>'
                          if PUBLISH else
                          '<p class="ad-notice test">テスト環境 — 内容は未確定です。'
                          '購入リンクにアフィリエイトIDは設定されていません</p>'),
                  ogimg=('<meta property="og:image" content="%s">\n' % e(og_image)) if og_image else "")


def ja_date(iso):
    """2020-07-30 -> 2020年7月30日。年月だけ・年だけも扱う"""
    if not iso:
        return None
    p = iso.split("-")
    if len(p) == 3:
        return "%s年%d月%d日" % (p[0], int(p[1]), int(p[2]))
    if len(p) == 2:
        return "%s年%d月" % (p[0], int(p[1]))
    return "%s年" % p[0]


def with_base(content):
    """ルート相対の href/src に BASE_PATH を付ける。外部URL(http〜)は対象外"""
    if not BASE_PATH:
        return content
    return re.sub(r'(href|src)="/', r'\1="%s/' % BASE_PATH, content)


def write(path, content):
    full = os.path.join(OUT, path.strip("/"), "index.html") if path != "/" \
        else os.path.join(OUT, "index.html")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    open(full, "w", encoding="utf-8").write(with_base(content))


def card_list(items):
    """作品カードの一覧（一覧系ページ共通）"""
    out = ['<ul class="cards">']
    for url, title, released, plat, img, extra in items:
        thumb = '<img src="%s" alt="" loading="lazy" width="90">' % e(img) if img else \
                '<span class="noimg"></span>'
        out.append(
            '<li><a href="%s">%s<span class="meta"><b>%s</b>'
            '<small>%s</small><small>%s</small>%s</span></a></li>'
            % (e(url), thumb, e(title), e(released or ""), e(plat or ""),
               ('<small class="ex">%s</small>' % e(extra)) if extra else ""))
    out.append("</ul>")
    return "\n".join(out)


# ---------------------------------------------------------------- 本体

def main():
    con = sqlite3.connect(os.path.join(DATA, "vndb_otome.db"))
    con.row_factory = sqlite3.Row
    cond = " OR ".join("platforms LIKE '%%%s%%'" % k for k in HOME)
    games = {r["vid"]: r for r in con.execute(
        "SELECT * FROM games WHERE %s" % cond)}
    vids = list(games)
    ph = ",".join("?" * len(vids))
    print("対象作品: %d件" % len(vids))

    slug = {}
    label = {}
    npages = {}
    for r in con.execute("SELECT kind,key,url,label,n_works,is_page FROM slugs"):
        slug[(r["kind"], r["key"])] = r["url"]
        label[(r["kind"], r["key"])] = r["label"]
        npages[(r["kind"], r["key"])] = (r["n_works"], r["is_page"])

    chars = defaultdict(list)
    for r in con.execute("SELECT * FROM characters WHERE vid IN (%s)" % ph, vids):
        chars[r["vid"]].append(r)
    shops = defaultdict(list)
    for r in con.execute("SELECT * FROM shop_urls WHERE vid IN (%s) ORDER BY priority" % ph, vids):
        shops[r["vid"]].append(r)
    gtags = defaultdict(list)
    for r in con.execute("SELECT vid,tag FROM vn_tags WHERE vid IN (%s)" % ph, vids):
        gtags[r["vid"]].append(r["tag"])
    gtraits = defaultdict(set)
    for r in con.execute("SELECT vid,category,trait FROM traits WHERE vid IN (%s)" % ph, vids):
        gtraits[r["vid"]].add("%s:%s" % (r["category"], r["trait"]))
    gplats = defaultdict(list)
    for r in con.execute("SELECT vid,platform FROM platforms WHERE vid IN (%s)" % ph, vids):
        gplats[r["vid"]].append(r["platform"])
    rels = defaultdict(list)
    for r in con.execute("SELECT * FROM relations WHERE vid IN (%s)" % ph, vids):
        rels[r["vid"]].append(r)
    sc_by_vid = defaultdict(list)
    sc_by_sid = defaultdict(list)
    for r in con.execute("SELECT * FROM staff_credits WHERE vid IN (%s)" % ph, vids):
        sc_by_vid[r["vid"]].append(r)
        sc_by_sid[r["sid"]].append(r)
    person = {sid: (url, label, kind) for sid, url, label, kind in
              con.execute("SELECT sid, url, label, kind FROM person_pages")}
    sid_of_cv = {label: sid for sid, (u, label, k) in person.items() if k == "cv"}

    alias_of = defaultdict(list)
    for a, c in con.execute("SELECT alias,canonical FROM slug_aliases"):
        alias_of[c].append(a)

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    urls = []

    def brief(g):
        return "%s（%s）" % (g["title"], (g["platforms"] or "").split(" / ")[0])

    # ---------------- 作品ページ ----------------
    for vid, g in games.items():
        url = slug[("game", vid)]
        cs = chars[vid]
        cvnames = [c["cv"] for c in cs if c["cv"]]
        plat = g["platforms"] or ""
        desc = "%s（%s）の攻略キャラクターと声優一覧。" % (g["title"], plat)
        if g["released"]:
            desc += "%s発売" % ja_date(g["released"])
        if g["developers"]:
            desc += "、%s" % g["developers"].split(" / ")[0]
        desc += "。"
        if cvnames:
            names, ln = [], 0
            for c in cs:
                if not c["cv"]:
                    continue
                piece = "%s（%s）" % (c["name"], c["cv"])
                if ln + len(piece) > 44:
                    break
                names.append(piece); ln += len(piece) + 1
            desc += "、".join(names)
            if len(names) < len(cvnames):
                desc += " ほか計%d名" % len(cvnames)
            desc += "のキャストを掲載。"
        desc += "楽天・Amazon・駿河屋・アニメイト・メルカリの購入リンクつき。"

        b = ['<article class="game">']
        b.append('<div class="hero">')
        if g["image_url"]:
            b.append('<img class="cover" src="%s" alt="%sのパッケージ" width="220">'
                     % (e(g["image_url"]), e(g["title"])))
        b.append("<div>")
        b.append("<h1>%s</h1>" % e(g["title"]))
        if g["title_latin"]:
            b.append('<p class="latin">%s</p>' % e(g["title_latin"]))
        rows = [("機種", " / ".join('<a href="%s">%s</a>' % (e(slug[("platform", p)]), e(p))
                                    for p in gplats[vid] if ("platform", p) in slug)),
                ("発売日", e(ja_date(g["released"]) or "—")),
                ("メーカー", " / ".join(
                    '<a href="%s">%s</a>' % (e(slug[("maker", d)]), e(d))
                    if ("maker", d) in slug and npages[("maker", d)][1] else e(d)
                    for d in (g["developers"] or "").split(" / ") if d)),
                ("プレイ時間", e(g["length"] or "—")),
                ("対応言語", e(g["languages"] or "—")),
                ("年齢制限", e(("%s歳以上" % g["minage"]) if g["minage"] else "—")),
                ("評価", ("%.2f（%d票）" % (g["rating"], g["votecount"])) if g["rating"] else "—")]
        b.append('<table class="facts">' + "".join(
            "<tr><th>%s</th><td>%s</td></tr>" % (k, v) for k, v in rows) + "</table>")
        b.append("</div></div>")

        # 購入導線
        b.append('<section id="buy"><h2>買えるお店</h2>')
        b.append('<p class="ad-inline">%s</p>' % (
            "以下はアフィリエイトリンクを含みます" if PUBLISH
            else "テスト環境のため、以下は通常の検索リンクです（アフィリエイト未設定）"))
        # 表示順は shop_urls の priority に従う（発売年で新品/中古が入れ替わる）
        first = shops[vid][0]["condition"] if shops[vid] else "新品"
        order = [first] + [c for c in ("新品", "中古") if c != first]
        for cond_name in order:
            group = [s for s in shops[vid] if s["condition"] == cond_name]
            if not group:
                continue
            b.append('<h3>%s%s</h3><ul class="shops">' % (
                cond_name, "（こちらがおすすめ）" if cond_name == first else ""))
            seen = set()
            for s in group:
                if s["channel"] in seen:
                    continue
                seen.add(s["channel"])
                b.append('<li><a class="shop" href="%s" rel="nofollow sponsored noopener" target="_blank">%s で探す</a></li>'
                         % (e(s["url"]), e(s["channel"])))
            b.append("</ul>")
        if (g["released"] or "9999") <= "2015":
            b.append('<p class="note">2015年以前の作品です。新品は流通していない可能性があります。</p>')
        b.append("</section>")

        # キャスト
        if cs:
            b.append('<section id="cast"><h2>キャラクターと声優</h2><table class="cast">')
            b.append("<tr><th>区分</th><th>キャラクター</th><th>声優</th></tr>")
            for c in cs:
                cv = ""
                if c["cv"]:
                    key = c["cv"]
                    u = slug.get(("cv", key))
                    if not u:
                        for canon, alist in alias_of.items():
                            if key in alist:
                                u = slug.get(("cv", canon)); break
                    cv = '<a href="%s">%s</a>' % (e(u), e(c["cv"])) if u else e(c["cv"])
                b.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
                         % (e(c["role"] or ""), e(c["name"] or ""), cv))
            b.append("</table></section>")

        if sc_by_vid[vid]:
            byrole = defaultdict(list)
            for c in sc_by_vid[vid]:
                u = person.get(c["sid"], (None,))[0]
                byrole[c["role"]].append(
                    '<a href="%s">%s</a>' % (e(u), e(c["name"])) if u else e(c["name"]))
            ORDER_R = ["シナリオ", "原画", "キャラクターデザイン", "音楽", "主題歌",
                       "監督", "翻訳", "編集", "QA"]
            keys = [r for r in ORDER_R if r in byrole] + \
                   [r for r in byrole if r not in ORDER_R]
            b.append('<section id="staff"><h2>スタッフ</h2><table class="facts">')
            for r in keys:
                b.append("<tr><th>%s</th><td>%s</td></tr>" % (e(r), "・".join(byrole[r])))
            b.append("</table></section>")

        tg = [t for t in gtags[vid] if ("tag", t) in slug and npages[("tag", t)][1]]
        if tg:
            b.append('<section><h2>タグ</h2><p class="tags">%s</p></section>' % " ".join(
                '<a href="%s">%s</a>' % (e(slug[("tag", t)]), e(t)) for t in tg[:20]))

        rl = [r for r in rels[vid] if r["related_vid"] in games]
        if rl:
            b.append('<section><h2>関連作品</h2><ul class="rel">')
            for r in rl:
                rg = games[r["related_vid"]]
                b.append('<li><span class="rt">%s</span> <a href="%s">%s</a></li>'
                         % (e(r["type"]), e(slug[("game", rg["vid"])]), e(rg["title"])))
            b.append("</ul></section>")

        if g["jawiki_url"]:
            b.append('<p class="ext"><a href="%s" rel="noopener">日本語版Wikipediaで見る</a></p>'
                     % e(g["jawiki_url"]))
        b.append('<p class="ext"><a href="%s" rel="noopener">VNDBの原データ</a></p>' % e(g["url"]))
        b.append("</article>")

        ld = {"@context": "https://schema.org", "@type": "VideoGame",
              "name": g["title"], "url": BASE_URL + url,
              "gamePlatform": gplats[vid],
              "inLanguage": "ja"}
        if g["released"] and len(g["released"]) == 10:
            ld["datePublished"] = g["released"]
        if g["image_url"]:
            ld["image"] = g["image_url"]
        if g["developers"]:
            ld["publisher"] = {"@type": "Organization",
                               "name": g["developers"].split(" / ")[0]}
        actors = [{"@type": "Person", "name": c["cv"]} for c in cs if c["cv"]]
        if actors:
            ld["actor"] = actors[:30]
        if g["rating"] and g["votecount"]:
            ld["aggregateRating"] = {"@type": "AggregateRating",
                                     "ratingValue": round(g["rating"], 2),
                                     "ratingCount": g["votecount"],
                                     "bestRating": 10, "worstRating": 1}
        crumbs = [(SITE_NAME, "/")]
        p0 = gplats[vid][0] if gplats[vid] else None
        if p0 and ("platform", p0) in slug:
            crumbs.append((p0, slug[("platform", p0)]))
        crumbs.append((g["title"], None))
        title = "%s（%s）の攻略キャラ・声優一覧｜%s" % (
            g["title"], (plat.split(" / ")[0] if plat else ""), SITE_NAME)
        write(url, layout(title, desc, BASE_URL + url, "\n".join(b), [ld], crumbs,
                          g["image_url"]))
        urls.append((url, g["released"]))

    # ---------------- 一覧系ページ ----------------
    def listing(kind, heading_fmt, desc_fmt, member_sql, extra_col=None):
        made = 0
        for (k, key), (n, is_page) in npages.items():
            if k != kind or not is_page:
                continue
            url = slug[(k, key)]
            lab = label[(k, key)]
            if kind == "cv":
                keys = [key] + alias_of.get(key, [])
                rows = con.execute(
                    "SELECT DISTINCT vid, name FROM characters WHERE cv IN (%s)"
                    % ",".join("?" * len(keys)), keys).fetchall()
            else:
                rows = con.execute(member_sql, (key,) if "?" in member_sql else ()).fetchall() \
                    if "?" in member_sql else []
            items = []
            for r in rows:
                g = games.get(r["vid"])
                if not g:
                    continue
                items.append((slug[("game", g["vid"])], g["title"], g["released"],
                              (g["platforms"] or "").split(" / ")[0], g["image_url"],
                              r[extra_col] if extra_col and extra_col in r.keys() else None))
            if not items:
                continue
            items.sort(key=lambda x: (x[2] or "0000"), reverse=True)
            body = ["<h1>%s</h1>" % e(heading_fmt % lab),
                    '<p class="lead">%s</p>' % e(desc_fmt % (lab, len(items)))]
            if kind == "cv" and alias_of.get(key):
                body.append('<p class="alias">別名義: %s</p>' % e("、".join(alias_of[key])))
            body.append(card_list(items))
            ld = {"@context": "https://schema.org", "@type": "ItemList",
                  "name": heading_fmt % lab,
                  "numberOfItems": len(items),
                  "itemListElement": [
                      {"@type": "ListItem", "position": i + 1,
                       "url": BASE_URL + it[0], "name": it[1]}
                      for i, it in enumerate(items[:50])]}
            write(url, layout("%s｜%s" % (heading_fmt % lab, SITE_NAME),
                              desc_fmt % (lab, len(items)), BASE_URL + url,
                              "\n".join(body), [ld], [(SITE_NAME, "/"), (lab, None)]))
            urls.append((url, None))
            made += 1
        return made

    def person_page(kind, key, url, lab, n):
        """1人物1ページ。出演（声優）とスタッフ参加の両方を載せる"""
        sid = key if kind == "staff" else sid_of_cv.get(key)
        body = []
        cast_items, staff_items = [], []

        if kind == "cv":
            keys = [key] + alias_of.get(key, [])
            for r in con.execute(
                    "SELECT DISTINCT vid, name FROM characters WHERE cv IN (%s)"
                    % ",".join("?" * len(keys)), keys):
                g = games.get(r["vid"])
                if g:
                    cast_items.append((slug[("game", g["vid"])], g["title"],
                                       ja_date(g["released"]),
                                       (g["platforms"] or "").split(" / ")[0],
                                       g["image_url"], r["name"]))
        if sid:
            seen = {}
            for c in sc_by_sid.get(sid, []):
                g = games.get(c["vid"])
                if not g:
                    continue
                seen.setdefault(c["vid"], []).append(c["role"])
            for v2, roles in seen.items():
                g = games[v2]
                staff_items.append((slug[("game", v2)], g["title"], ja_date(g["released"]),
                                    (g["platforms"] or "").split(" / ")[0],
                                    g["image_url"], "・".join(sorted(set(roles)))))

        cast_items.sort(key=lambda x: (x[2] or ""), reverse=True)
        staff_items.sort(key=lambda x: (x[2] or ""), reverse=True)
        total = len({i[0] for i in cast_items} | {i[0] for i in staff_items})
        if not total:
            return False

        roles_txt = "・".join(sorted({c["role"] for c in sc_by_sid.get(sid, [])})) if sid else ""
        if kind == "cv":
            head = "%s が出演する乙女ゲーム" % lab
            desc = ("%s が声を担当した乙女ゲーム %d作品の一覧。"
                    "キャラクター名・発売日・機種と、買えるお店へのリンクをまとめています。"
                    % (lab, len(cast_items)))
        else:
            head = "%s が手がけた乙女ゲーム" % lab
            desc = ("%s（%s）が参加した乙女ゲーム %d作品の一覧。"
                    % (lab, roles_txt or "スタッフ", len(staff_items)))
        body.append("<h1>%s</h1>" % e(head))
        body.append('<p class="lead">%s</p>' % e(desc))
        if kind == "cv" and roles_txt:
            body.append('<p class="alias">スタッフとしての参加: %s</p>' % e(roles_txt))
        if alias_of.get(key):
            body.append('<p class="alias">別名義: %s</p>' % e("、".join(alias_of[key])))
        if cast_items:
            if staff_items:
                body.append("<h2>出演</h2>")
            body.append(card_list(cast_items))
        if staff_items:
            body.append("<h2>スタッフとしての参加</h2>")
            body.append(card_list(staff_items))

        ld = {"@context": "https://schema.org", "@type": "ItemList", "name": head,
              "numberOfItems": total,
              "itemListElement": [{"@type": "ListItem", "position": i + 1,
                                   "url": BASE_URL + it[0], "name": it[1]}
                                  for i, it in enumerate((cast_items + staff_items)[:50])]}
        write(url, layout("%s｜%s" % (head, SITE_NAME), desc, BASE_URL + url,
                          "\n".join(body), [ld], [(SITE_NAME, "/"), (lab, None)]))
        urls.append((url, None))
        return True

    n_cv = n_staff = 0
    for (k, key), (n, is_page) in npages.items():
        if k in ("cv", "staff") and is_page:
            if person_page(k, key, slug[(k, key)], label[(k, key)], n):
                if k == "cv":
                    n_cv += 1
                else:
                    n_staff += 1

    n_tag = listing("tag", "「%s」の乙女ゲーム",
                    "「%s」に該当する乙女ゲーム %d作品の一覧。",
                    "SELECT DISTINCT vid FROM vn_tags WHERE tag = ?")
    n_plat = listing("platform", "%s の乙女ゲーム",
                     "%s で遊べる乙女ゲーム %d作品の一覧。",
                     "SELECT DISTINCT vid FROM platforms WHERE platform = ?")
    n_maker = listing("maker", "%s の乙女ゲーム",
                      "%s が開発した乙女ゲーム %d作品の一覧。",
                      "SELECT vid FROM games WHERE developers LIKE '%' || ? || '%'")

    # 属性ページ（キーが category:trait）
    n_tr = 0
    for (k, key), (n, is_page) in npages.items():
        if k != "trait" or not is_page:
            continue
        cat, tr = key.split(":", 1)
        rows = con.execute(
            "SELECT DISTINCT vid, name FROM traits WHERE category=? AND trait=?",
            (cat, tr)).fetchall()
        items = []
        for r in rows:
            g = games.get(r["vid"])
            if g:
                items.append((slug[("game", g["vid"])], g["title"], g["released"],
                              (g["platforms"] or "").split(" / ")[0], g["image_url"],
                              r["name"]))
        if not items:
            continue
        items.sort(key=lambda x: (x[2] or "0000"), reverse=True)
        url = slug[(k, key)]
        d = "「%s」のキャラクターが登場する乙女ゲーム %d作品の一覧。" % (tr, len(items))
        body = ['<h1>「%s」のキャラクターがいる乙女ゲーム</h1>' % e(tr),
                '<p class="lead">%s（%s）</p>' % (e(d), e(cat)), card_list(items)]
        write(url, layout("「%s」のキャラクターがいる乙女ゲーム｜%s" % (tr, SITE_NAME),
                          d, BASE_URL + url, "\n".join(body), None,
                          [(SITE_NAME, "/"), (tr, None)]))
        urls.append((url, None))
        n_tr += 1

    # ---------------- トップ（検索） ----------------
    today = datetime.date.today().isoformat()
    released = [g for g in games.values() if g["released"] and g["released"] <= today]
    upcoming = [g for g in games.values() if g["released"] and g["released"] > today]
    recent = sorted(released, key=lambda g: g["released"], reverse=True)[:24]
    upcoming.sort(key=lambda g: g["released"])
    body = ['<h1>%s</h1>' % e(SITE_NAME),
            '<p class="lead">%s 家庭用ゲーム%d作品・声優%d人を収録。</p>' % (e(SITE_DESC), len(games), n_cv),
            '<div id="app"><noscript><p>絞り込みにはJavaScriptが必要です。'
            '下の新着一覧と各ページはそのままご覧いただけます。</p></noscript></div>',
            ]
    if upcoming:
        body += ["<h2>発売予定</h2>",
                 card_list([(slug[("game", g["vid"])], g["title"], ja_date(g["released"]),
                             (g["platforms"] or "").split(" / ")[0], g["image_url"], None)
                            for g in upcoming])]
    body += ["<h2>最近発売された作品</h2>",
             card_list([(slug[("game", g["vid"])], g["title"], ja_date(g["released"]),
                         (g["platforms"] or "").split(" / ")[0], g["image_url"],
                         ("★ %.2f" % g["rating"]) if g["rating"] else None)
                        for g in recent])]
    write("/", layout("%s｜乙女ゲームを声優・キャラ属性から探す" % SITE_NAME, SITE_DESC,
                      BASE_URL + "/", "\n".join(body), None, None))
    urls.append(("/", None))

    # ---------------- assets / sitemap / robots ----------------
    assets_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    shutil.copytree(assets_src, os.path.join(OUT, "assets"))
    shutil.copy(os.path.join(DATA, "site", "index.json"),
                os.path.join(OUT, "assets", "index.json"))
    shutil.copy(os.path.join(DATA, "site", "traits.json"),
                os.path.join(OUT, "assets", "traits.json"))

    # 404（Cloudflare Pages は /404.html を自動で使う）
    nf = ['<h1>ページが見つかりません</h1>',
          '<p class="lead">URLが変わったか、削除された可能性があります。'
          'トップから探し直してください。</p>',
          '<p><a href="/">%s のトップへ</a></p>' % e(SITE_NAME)]
    open(os.path.join(OUT, "404.html"), "w", encoding="utf-8").write(
        with_base(layout("ページが見つかりません｜%s" % SITE_NAME,
               "お探しのページは見つかりませんでした。", BASE_URL + "/404.html",
               "\n".join(nf), None, [(SITE_NAME, "/"), ("404", None)])))

    # Cloudflare Pages 用のキャッシュ指定
    open(os.path.join(OUT, "_headers"), "w", encoding="utf-8").write(
        "/assets/*\n  Cache-Control: public, max-age=3600\n\n"
        "/*\n  Cache-Control: public, max-age=600\n")

    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u, _ in urls:
        sm.append("<url><loc>%s%s</loc><lastmod>%s</lastmod></url>" % (BASE_URL, u, today))
    sm.append("</urlset>")
    open(os.path.join(OUT, "sitemap.xml"), "w", encoding="utf-8").write("\n".join(sm))
    open(os.path.join(OUT, "robots.txt"), "w", encoding="utf-8").write(
        ("User-agent: *\nAllow: /\n\nSitemap: %s/sitemap.xml\n" % BASE_URL) if PUBLISH
        else "User-agent: *\nDisallow: /\n")

    print()
    print("  作品      %5d" % len(games))
    print("  声優      %5d" % n_cv)
    print("  スタッフ   %5d" % n_staff)
    print("  キャラ属性 %5d" % n_tr)
    print("  タグ      %5d" % n_tag)
    print("  メーカー   %5d" % n_maker)
    print("  機種      %5d" % n_plat)
    print("  トップ        1")
    print("  ------------------")
    print("  合計      %5d ページ" % len(urls))
    con.close()


if __name__ == "__main__":
    main()
