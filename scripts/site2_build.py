# -*- coding: utf-8 -*-
"""【v2 フェーズ2】新デザイン「オトメ棚」のサイトを docs/v2/ に生成する。

    python3 scripts/site2_db.py      # 先に表示用DBを作る
    python3 scripts/site2_build.py

現行サイト（docs/ 直下）には一切触らない。出力は docs/v2/ の中だけ。

デザインの出どころ:
  Claude Design プロジェクト "Otome Index"（Modernist デザインシステム +
  オトメ棚のピンクテーマ）。Home / Game Detail / My Page の3画面と、
  それぞれのモバイル版が与えられている。ここではその構成をそのまま
  静的HTMLに起こし、モバイル版は別ページではなくCSSの折り返しで出す。

JSなしで読めること:
  現行サイトと同じ方針。機種タブ・版の選択・MY棚はJSで動くが、
  JSが無ければ全機種・全版・全店舗がそのまま並んで読める状態にしておく。
  MY棚だけはブラウザ内（localStorage）のデータなので、JSなしでは空になる。
"""
import os, re, sys, json, html, shutil, sqlite3, datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, ROOT
import affiliate_config as AF
from site_config import (SITE_NAME, SITE_DESC, SITE_URL, REPO_URL, SCOPE_NOTE,
                         PUBLISH, BASE_PATH, AGE_TIERS, year_bucket, year_label, year_sort)

DB = os.path.join(DATA, "site2.db")
OUT = os.path.join(ROOT, "docs", "v2")
PREFIX = BASE_PATH + "/v2"              # 配信されるときの実際の接頭辞
BASE_URL = SITE_URL + PREFIX

HAS_AFFILIATE = any(getattr(AF, k) for k in
                    ("RAKUTEN_AFFILIATE_ID", "AMAZON_ASSOCIATE_TAG",
                     "SURUGAYA_AFFILIATE_ID", "ANIMATE_A8_BASE",
                     "MERCARI_AMBASSADOR_ID"))

# 楽天ウェブサービスの規約で、価格・在庫を持っていられるのは取得から24時間まで。
# ビルドが空いたときに古い値段を出さないよう、表示側でも切る。
PRICE_TTL_HOURS = 24

e = lambda s: html.escape(str(s), quote=True) if s is not None else ""

# 索引の種別。(URL接頭辞, 見出し, トップのカードに書く一言)
CATS = [
    ("cv",        "声優",       "名前の五十音から、担当作の一覧へ。"),
    ("maker",     "メーカー",   "オトメイト・クインロゼなど、開発元ごとの作品一覧へ。"),
    ("series",    "シリーズ",   "どれから遊べばよいかを発売順で確認。"),
    ("publisher", "発売元",     "販売しているブランドごとの作品一覧へ。"),
    ("staff",     "スタッフ",   "シナリオ・原画・音楽など、役割ごとに辿る。"),
    ("tag",       "タグ",       "ジャンル・題材・システムから絞り込む。"),
    ("trait",     "キャラ属性", "「ツンデレ」「幼なじみ」など、好みの属性から。"),
    ("platform",  "機種",       "Switch・PS・PSP など、持っている機種から。"),
]
CAT_LABEL = {k: lab for k, lab, _ in CATS}

# 下部の常設ナビ（デザインのモバイル版にある4つ）
TABS = [("ホーム", "/"), ("探す", "/#search"), ("新作", "/upcoming/"), ("MY棚", "/my/")]


def ja_date(iso):
    if not iso:
        return ""
    p = iso.split("-")
    if len(p) == 3:
        return "%s年%d月%d日" % (p[0], int(p[1]), int(p[2]))
    if len(p) == 2:
        return "%s年%d月" % (p[0], int(p[1]))
    return "%s年" % p[0]


def strip_bb(t):
    """VNDBのあらすじは BBCode 混じり。[url=..]〜[/url] や [spoiler] が生で出る"""
    if not t:
        return ""
    t = re.sub(r"\[url=[^\]]*\]", "", t)
    t = re.sub(r"\[/?(url|spoiler|quote|b|i|u|s|code)\]", "", t, flags=re.I)
    t = re.sub(r"\[From[^\]]*\]", "", t, flags=re.I)
    # 「[from Play-Asia]」のような出典行は空の括弧だけ残るので畳む
    t = re.sub(r"\[\s*\]", "", t)
    return t.strip()


def fresh_price(price, fetched_at):
    """24時間以内に取った価格だけ返す。無ければ None"""
    if not price or not fetched_at:
        return None
    try:
        t = datetime.datetime.strptime(fetched_at, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    if (datetime.datetime.now() - t).total_seconds() > PRICE_TTL_HOURS * 3600:
        return None
    return price


def yen(n):
    return "¥%s" % format(int(n), ",")


# ---------------------------------------------------------------- テンプレート

def layout(title, desc, path, body, crumbs=None, current="", og_image=None, jsonld=None):
    """1ページ分のHTML。path はサイト内のルート相対（"/game/xxx/"）"""
    canon = BASE_URL + path
    crumb = ""
    if crumbs:
        crumb = '<nav class="crumbs" aria-label="現在地">%s</nav>' % "<span>/</span>".join(
            ('<a href="%s">%s</a>' % (e(u), e(t))) if u else ("<span>%s</span>" % e(t))
            for t, u in crumbs)
    ld = "".join('<script type="application/ld+json">%s</script>' % json.dumps(o, ensure_ascii=False)
                 for o in (jsonld or []))
    nav_links = "".join(
        '<a href="%s"%s>%s</a>' % (e(u), ' aria-current="page"' if t == current else "", e(t))
        for t, u in [("作品を探す", "/"), ("属性から探す", "/trait/"), ("新作カレンダー", "/upcoming/")])
    tabbar = "".join(
        '<a href="%s"%s>%s</a>' % (e(u), ' aria-current="page"' if t == current else "", e(t))
        for t, u in TABS)
    return """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<meta name="description" content="%(desc)s">
<link rel="canonical" href="%(canon)s">
%(robots)s<meta property="og:type" content="website">
<meta property="og:title" content="%(title)s">
<meta property="og:description" content="%(desc)s">
<meta property="og:url" content="%(canon)s">
<meta property="og:site_name" content="%(site)s">
%(ogimg)s<meta name="twitter:card" content="summary_large_image">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="/assets/style.css">
%(ld)s</head>
<body>
<div class="page">
<p class="banner">%(notice)s</p>
<header class="nav">
  <div class="nav-l">
    <a class="nav-brand" href="/">Otome Index</a>
    <nav class="nav-links" aria-label="主な導線">%(navlinks)s</nav>
  </div>
  <div class="nav-r">
    <span class="nav-count" data-shelf-count hidden></span>
    <a class="btn btn-secondary" href="/my/">MY棚</a>
  </div>
</header>
%(crumb)s
<main>
%(body)s
</main>
<footer class="foot">
  <p><strong>%(site)s</strong> — %(sitedesc)s</p>
  <p>%(scope)s 対象年齢の区分は VNDB の年齢指定をもとにした目安で、CERO の公式レーティングではありません。</p>
  <p>作品・キャラクター・声優のデータは <a href="https://vndb.org/" rel="noopener">VNDB</a> より取得し、
  <a href="https://opendatacommons.org/licenses/odbl/1-0/" rel="noopener">Open Database License (ODbL)</a>
  のもとで利用しています。本サイトの派生データベースも同ライセンスで提供します。
  <a href="%(repo)s" rel="noopener">生成スクリプト</a>を公開しています。</p>
  <p>価格・在庫は各ストアの情報です。最新の内容は各ストアでご確認ください。</p>
  <p><a href="/../">現行サイト（v1）はこちら</a></p>
</footer>
<nav class="tabbar" aria-label="下部ナビ">%(tabbar)s</nav>
</div>
<script>window.V2_BASE=%(prefix)s;</script>
<script src="/assets/app.js" defer></script>
</body>
</html>""" % dict(
        title=e(title), desc=e(desc), canon=e(canon), site=e(SITE_NAME),
        sitedesc=e(SITE_DESC), scope=e(SCOPE_NOTE), repo=e(REPO_URL),
        body=body, crumb=crumb, ld=ld, navlinks=nav_links, tabbar=tabbar,
        prefix=json.dumps(PREFIX),
        robots="" if PUBLISH else '<meta name="robots" content="noindex,nofollow">\n',
        ogimg=('<meta property="og:image" content="%s">\n' % e(og_image)) if og_image else "",
        notice=("当サイトはアフィリエイト広告を利用しています" if PUBLISH else
                "テスト環境 — 内容は未確定です。" + ("購入リンクはアフィリエイトリンクです"
                if HAS_AFFILIATE else "購入リンクにアフィリエイトIDは設定されていません")))


def write(path, content):
    """ルート相対のリンクに配信時の接頭辞を付けて書き出す"""
    content = re.sub(r'(href|src|action)="/', r'\1="%s/' % PREFIX, content)
    full = os.path.join(OUT, path.strip("/"), "index.html") if path != "/" \
        else os.path.join(OUT, "index.html")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------- 部品

def thumb(w, cls="thumb"):
    """表紙。無い作品が88件あるので、そのときは斜線のプレースホルダを出す"""
    if w["cover"]:
        return ('<img class="%s cover" src="%s" alt="" loading="lazy" width="76" height="101">'
                % (cls, e(w["cover"])))
    return '<div class="%s ph"><span>画像なし</span></div>' % cls


def work_card(w):
    """トップと一覧に並ぶ作品カード。所持マークはJSが後から入れる"""
    meta = " ".join(x for x in [w["released_ja"], w["platform_top"]] if x)
    return ("""<a class="wcard" href="%(url)s" data-vid="%(vid)s">%(img)s<div class="body">
<div class="t">%(title)s</div><div class="m">%(meta)s</div>
<div class="r"><span class="score">%(score)s</span><span class="own" data-own></span></div>
</div></a>""" % dict(url=e(w["url"]), vid=e(w["vid"]), img=thumb(w),
                     title=e(w["title"]), meta=e(meta), score=e(w["rating_text"])))


# ---------------------------------------------------------------- 購入導線

def buy_section(w, eds, offers):
    """デザインの「1. 機種 → 2. 版 → 3. 販売店」をそのまま組む。

    JSが無ければ全機種・全版・全店舗がそのまま並んで読める。
    JSは表示の絞り込みだけを担当する。

    値段について:
      - 楽天ウェブサービスの規約により、取得から24時間を過ぎた価格は出さない
      - 合本（他作品とのセット）の版は「合本ぜんぶ」の値段なので出さない。
        リンクは残す。その機種で遊ぶにはそれを買うしかないため
      - 送料・店舗特典はDBに無いので列を置かない（デザインからは削っている）
    """
    if not eds:
        return ('<section class="pad sec" id="buy"><h2>購入する</h2>'
                '<p class="empty">購入できるお店の情報がまだありません。</p></section>')

    # 機種ごとにまとめる。並びは editions の取得順（家庭用機→PC→スマホ、新しい機種が上）
    groups = []
    for r in eds:
        if groups and groups[-1][0] == r["platform"]:
            groups[-1][1].append(r)
        else:
            groups.append((r["platform"], [r]))
    groups = [(c, rows) for c, rows in groups if any(offers.get(r["eid"]) for r in rows)]
    if not groups:
        return ('<section class="pad sec" id="buy"><h2>購入する</h2>'
                '<p class="empty">購入できるお店の情報がまだありません。</p></section>')

    b = ['<section class="pad sec" id="buy">',
         '<div class="buy-head"><h2>購入する</h2>',
         '<span class="text-muted" style="font-size:12px">機種 → 版 → 販売店の順に選びます。'
         '%s</span></div>' % ("以下はアフィリエイトリンクを含みます" if HAS_AFFILIATE
                              else "アフィリエイトIDが未設定のため、通常の検索リンクです")]

    # 1. 機種
    b.append('<div class="step">1. 機種</div>')
    b.append('<div class="plat-tabs" data-plat-tabs role="tablist">')
    for i, (code, rows) in enumerate(groups):
        b.append('<button type="button" role="tab" data-plat="%s" aria-pressed="%s">%s</button>'
                 % (e(code), "true" if i == 0 else "false", e(rows[0]["platform_ja"])))
    b.append('</div>')

    # 2. 版
    b.append('<div class="step">2. 版</div>')
    first_eid = None
    fetched, ed_names = [], {}
    b.append('<div class="eds" data-eds>')
    for i, (code, rows) in enumerate(groups):
        live = [r for r in rows if offers.get(r["eid"])]
        for r in live:
            prices = []
            for o in offers[r["eid"]]:
                p = fresh_price(o["price"], o["fetched_at"])
                if p and not r["is_combo"]:
                    prices.append(p)
                    fetched.append(o["fetched_at"])
            name = r["edition_label"] or ("通常版" if len(live) > 1 else "パッケージ")
            ed_names[r["eid"]] = "%s %s" % (r["platform_ja"], name)
            if first_eid is None:
                first_eid = r["eid"]
            # 版の中身はDBに無いので、代わりに分かっていること（発売日・JAN・合本）を出す
            note = []
            if r["released"]:
                note.append("%s発売" % ja_date(r["released"]))
            if r["is_combo"]:
                note.append("『%s』とのセット。合本なので価格は出していません" % r["rel_title"])
            elif r["rel_title"] and r["rel_title"] != w["title"]:
                note.append("この機種でのタイトルは『%s』" % r["rel_title"])
            if r["gtin"]:
                note.append("JAN %s" % r["gtin"])
            b.append("""<div class="ed" data-plat="%(plat)s" data-eid="%(eid)s" data-sel="%(sel)s">
<div class="ed-top"><span class="ed-name">%(name)s</span>
<span class="ed-own" data-own-ed hidden>✓ 所持</span></div>
<div class="ed-price">%(price)s</div>
<div class="ed-meta">%(nshop)d店舗</div>
<div class="ed-note">%(note)s</div>
<button type="button" class="btn btn-secondary btn-block" data-pick-ed="%(eid)s">販売店を見る</button>
</div>""" % dict(plat=e(code), eid=e(r["eid"]),
                 sel="1" if r["eid"] == first_eid else "0",
                 name=e(name), price=(yen(min(prices)) + "〜") if prices else "価格は各店で",
                 nshop=len(offers[r["eid"]]), note=e(" / ".join(note) or "—")))
    b.append('</div>')

    # 3. 販売店
    b.append('<div class="step">3. 販売店 — <span data-ed-name>%s</span></div>'
             % e(ed_names.get(first_eid, "")))
    b.append('<div class="wrap-x"><table class="table" data-shops><thead><tr>'
             '<th>販売店</th><th>状態</th><th>価格</th><th>在庫</th><th></th>'
             '</tr></thead><tbody>')
    for code, rows in groups:
        for r in rows:
            if not offers.get(r["eid"]):
                continue
            # 同じ店で経路が2つあるとき（駿河屋本体と駿河屋楽天市場店）は
            # 値段の取れているほうを採る。値段のためだけの行は値段が無ければ出さない
            best = {}
            for o in offers[r["eid"]]:
                p = fresh_price(o["price"], o["fetched_at"])
                if o["via"] and not p:
                    continue
                k = (o["condition"], o["channel"])
                cur = best.get(k)
                if cur is None or (p and (not fresh_price(cur["price"], cur["fetched_at"])
                                          or p < fresh_price(cur["price"], cur["fetched_at"]))):
                    best[k] = o
            rows_o = sorted(best.values(), key=lambda o: (
                0 if fresh_price(o["price"], o["fetched_at"]) else 1, o["priority"]))
            cheapest = min([fresh_price(o["price"], o["fetched_at"]) for o in rows_o
                            if fresh_price(o["price"], o["fetched_at"])] or [None])
            for o in rows_o:
                p = None if r["is_combo"] else fresh_price(o["price"], o["fetched_at"])
                verb = ("で見る" if o["condition"] == "ダウンロード" or o["link_type"] == "item"
                        else "で探す")
                b.append("""<tr data-eid="%(eid)s" data-best="%(best)s">
<td>%(shop)s</td><td>%(cond)s</td><td class="price">%(price)s</td><td>%(stock)s</td>
<td><a class="btn btn-primary" href="%(url)s" rel="nofollow sponsored noopener"
   target="_blank">%(shop)s%(verb)s</a></td></tr>""" % dict(
                    eid=e(r["eid"]), best="1" if p and p == cheapest else "0",
                    shop=e(o["channel"]), cond=e(o["condition"] or ""),
                    price=yen(p) if p else "—",
                    stock=e(o["availability"] or ("配信中" if o["condition"] == "ダウンロード" else "各店で確認")),
                    url=e(o["url"]), verb=verb))
    b.append('</tbody></table></div>')

    if fetched:
        newest = max(fetched)
        b.append('<p class="text-muted" style="font-size:12px;margin-top:10px">'
                 '価格は %s %s時点のものです。変わっている場合があるので、購入前に各店でご確認ください。</p>'
                 % (e(ja_date(newest[:10])), e(newest[11:16])))
    else:
        b.append('<p class="text-muted" style="font-size:12px;margin-top:10px">'
                 '価格は取得から24時間を過ぎているため表示していません。各店でご確認ください。</p>')
    if (w["released"] or "9999") <= "2015":
        b.append('<p class="text-muted" style="font-size:12px">'
                 '2015年以前の作品です。新品は流通していない可能性があります。</p>')
    if AF.ANIMATE_A8_BASE and AF.ANIMATE_A8_PIXEL:
        b.append('<img src="%s" alt="" width="1" height="1" loading="lazy" '
                 'style="position:absolute">' % e(AF.ANIMATE_A8_PIXEL))
    b.append('</section>')
    return "".join(b)


# ---------------------------------------------------------------- 作品ページ

# キャラのチップに出す属性のカテゴリ。髪・瞳まで出すと10個以上並んで読めない
# 「色白」「細身」のような外見はどの作品にも並んで区別が付かない。
# 役柄・性格・境遇を先に出し、余ったぶんだけ外見を足す
STAFF_ORDER = {"シナリオ": 0, "キャラクターデザイン": 1, "原画": 2, "音楽": 3,
               "主題歌": 4, "ディレクター": 5, "スタッフ": 9}
CHIP_CATS = ["役柄", "性格", "境遇"]
CHIP_CATS_EXTRA = ["外見", "行動", "持ち物"]


def game_page(w, chars, ctraits, tags, staff, links, series, eds, offers, meta):
    url = w["url"]
    title = w["title"]
    lead = [x for x in [w["title_latin"], w["brand"], w["released_ja"]] if x]

    # ヒーロー右上のタグ。DBで裏が取れるものだけ出す
    chips = []
    if w["n_capture"]:
        chips.append('<span class="tag tag-accent">攻略キャラ%d人</span>' % w["n_capture"])
    if w["voiced"]:
        chips.append('<span class="tag tag-neutral">%s</span>' % e(w["voiced"]))
    if w["age_label"]:
        chips.append('<span class="tag tag-outline">%s</span>' % e(w["age_label"]))
    if w["n_platform"] and w["n_edition"]:
        chips.append('<span class="tag tag-outline">%d機種 / %d版</span>'
                     % (w["n_platform"], w["n_edition"]))

    facts = [("発売日", w["released_ja"]),
             ("機種", w["platforms"]),
             ("ブランド", ('<a href="%s">%s</a>' % (e(w["brand_url"]), e(w["brand"])))
              if w["brand_url"] else e(w["brand"] or "")),
             ("発売元", ('<a href="%s">%s</a>' % (e(w["publisher_url"]), e(w["publisher"])))
              if w["publisher_url"] else e(w["publisher"] or "")),
             ("プレイ時間", w["length_label"]),
             ("攻略キャラ", "%d人" % w["n_capture"] if w["n_capture"] else ""),
             ("評価", ("%s（%s票）" % (w["rating_text"], format(w["votecount"] or 0, ","))
                     if w["rating"] else "評価なし")),
             ("VNDB", '<a href="https://vndb.org/%s" rel="noopener">%s</a>' % (e(w["vid"]), e(w["vid"])))]
    facts_html = "".join('<dt>%s</dt><dd>%s</dd>' % (e(k), v if k in ("ブランド", "発売元", "VNDB") else e(v))
                         for k, v in facts if v)

    hero = """<div class="hero">
<div class="hero-img">%(cover)s</div>
<div class="hero-main">
<div class="kicker">%(genre)s</div>
<h1>%(title)s</h1>
<div class="hero-sub">%(lead)s</div>
<div class="tags" style="margin-bottom:20px">%(chips)s</div>
<dl class="facts">%(facts)s</dl>
</div>
<aside class="mypanel" data-shelf-panel data-vid="%(vid)s" data-title="%(title)s">
<div class="kicker">あなたのコレクション</div>
<div class="myown" data-shelf-own hidden><b></b><small></small></div>
<div style="margin-top:14px">
<div class="kicker" style="margin-bottom:6px">プレイ状況</div>
<div class="seg" data-shelf-status>
<button type="button" data-status="want" aria-pressed="false">欲しい</button>
<button type="button" data-status="owned" aria-pressed="false">所持</button>
<button type="button" data-status="playing" aria-pressed="false">プレイ中</button>
<button type="button" data-status="cleared" aria-pressed="false">クリア</button>
</div>
<div style="font-size:12px;color:var(--color-neutral-800);margin-top:8px" data-shelf-state>未登録</div>
<div class="bar"><i data-shelf-bar style="width:0%%"></i></div>
</div>
<div style="margin-top:18px;border-top:var(--hair);padding-top:14px">
<a href="#buy" class="btn btn-primary btn-block">購入先を見る</a>
<button type="button" class="btn btn-secondary btn-block" data-shelf-edit>記録を編集</button>
<noscript><p class="text-muted" style="font-size:12px;margin-top:8px">
MY棚はブラウザの中に保存されます。JavaScript を有効にするとお使いいただけます。</p></noscript>
</div>
</aside>
</div>""" % dict(cover=(('<img class="cover" src="%s" alt="%s のパッケージ" width="300" height="400">'
                        % (e(w["cover"]), e(title))) if w["cover"] else
                       '<div class="ph" style="aspect-ratio:3/4"><span>画像なし</span></div>'),
                 genre=e(w["genre_label"]), title=e(title), lead=e(" / ".join(lead)),
                 chips="".join(chips), facts=facts_html, vid=e(w["vid"]))

    parts = [hero, buy_section(w, eds, offers)]

    # ストーリー。日本語あらすじはDBに1件も入っていないので、
    # あるときだけ日本語、無ければ英語の原文、どちらも無ければ節ごと出さない
    if w["description_ja"]:
        body = "".join("<p>%s</p>" % e(p) for p in strip_bb(w["description_ja"]).split("\n") if p.strip())
        parts.append('<section class="pad sec"><h2>ストーリー</h2>%s</section>' % body)
    elif w["description"]:
        body = "".join("<p>%s</p>" % e(p) for p in strip_bb(w["description"]).split("\n") if p.strip())
        parts.append('<section class="pad sec"><h2>ストーリー</h2>'
                     '<p class="text-muted" style="font-size:12px">日本語のあらすじは未収録です。'
                     'VNDB の英語原文を表示しています。</p>'
                     '<div lang="en" style="font-size:14px;line-height:1.9">%s</div></section>' % body)

    # キャラクター
    main_chars = [c for c in chars if c["role"] in ("主人公", "攻略対象")]
    sub_chars = [c for c in chars if c["role"] not in ("主人公", "攻略対象")]
    if main_chars:
        cards = []
        for c in main_chars:
            got = ctraits.get(c["cid"], [])
            ts = [t for t in got if t["cat"] in CHIP_CATS]
            ts += [t for t in got if t["cat"] in CHIP_CATS_EXTRA]
            ts = ts[:6]
            chip = "".join('<a class="tag tag-outline" href="%s">%s</a>' % (e(t["url"]), e(t["trait"]))
                           for t in ts)
            cv = ('CV. <a href="%s">%s</a>' % (e(c["cv_url"]), e(c["cv"]))) if c["cv_url"] \
                else ("CV. %s" % e(c["cv"]) if c["cv"] else "CV. 未収録")
            bits = [x for x in [c["role_label"],
                                ("%d歳" % c["age"]) if c["age"] else "",
                                ("%dcm" % c["height"]) if c["height"] else ""] if x]
            cards.append("""<div class="char">
<div class="thumb ph" aria-hidden="true"></div>
<div><div class="n">%(name)s</div><div class="cv">%(cv)s</div>
<div class="text-muted" style="font-size:12px">%(bits)s</div>
<div class="tags" style="margin-top:8px">%(chip)s</div></div></div>"""
                         % dict(name=e(c["name"]), cv=cv,
                                bits=e(" / ".join(bits)), chip=chip))
        parts.append("""<section class="pad sec">
<div class="sec-head" style="margin-top:0"><h2>キャラクター</h2>
<span class="text-muted" style="font-size:12px">属性を押すと同じ属性の作品を探せます</span></div>
<div class="chars">%s</div>%s</section>""" % (
            "".join(cards),
            ('<p class="text-muted" style="font-size:12px;margin-top:18px">'
             'このほか %d人が登場します。</p>' % len(sub_chars)) if sub_chars else ""))

    # 好みとの一致（MY棚のデータを使うのでJSで描く）
    parts.append("""<section class="two" data-taste data-vid="%s">
<div><h2>あなたの好みとの一致</h2>
<p class="text-muted" style="font-size:13px;margin-top:0" data-taste-lead>
MY棚に作品を登録すると、あなたがよく選んでいる属性とこの作品を比べます。</p>
<div data-taste-body></div></div>
<div><h2>好みに近い未所持作品</h2>
<p class="text-muted" style="font-size:13px;margin-top:0">棚の属性傾向から選んでいます。</p>
<div data-taste-rec><p class="empty">MY棚が空です。</p></div></div>
</section>""" % e(w["vid"]))

    # 下段3カラム
    # 同じ人が同じ役割で複数リリースに載っているので (役割, 名前) で畳む。
    # 並びは読み手が知りたい順（シナリオ → 絵 → 音）に固定する
    seen_st, st_rows = set(), []
    for s in sorted(staff, key=lambda s: (STAFF_ORDER.get(s["role"], 99), s["name"] or "")):
        k = (s["role"], s["name"])
        if k in seen_st:
            continue
        seen_st.add(k)
        st_rows.append(s)
    staff_html = "".join(
        '<div class="kv"><span>%s</span><span>%s</span></div>'
        % (e(s["role"]), ('<a href="%s">%s</a>' % (e(s["url"]), e(s["name"]))) if s["url"] else e(s["name"]))
        for s in st_rows) or '<p class="empty">スタッフの情報がありません。</p>'
    # 70個並ぶ作品があり、全部出すと読めない。作品の中身を表すタグを先に24件まで
    tag_sorted = ([t for t in tags if t["cat"] == "内容"] +
                  [t for t in tags if t["cat"] != "内容"])[:24]
    tag_html = "".join('<a class="tag tag-outline" href="%s">%s</a>' % (e(t["url"]), e(t["tag"]))
                       for t in tag_sorted) or '<span class="text-muted">タグなし</span>'
    if len(tags) > len(tag_sorted):
        tag_html += ('<span class="text-muted" style="font-size:11px">ほか%d件</span>'
                     % (len(tags) - len(tag_sorted)))
    ser_html = "".join(
        '<div class="lrow">%s</div>'
        % (('<a href="%s">%s</a>' % (e(s["member_url"]), e(s["member_title"])))
           if s["member_url"] and s["member_vid"] != w["vid"]
           else "<strong>%s</strong>（この作品）" % e(s["member_title"]))
        for s in series)
    # リリースごとに同じ公式サイトが入っているので、ホスト名で1本にまとめる。
    # 「公式サイト」が35行並んでも区別が付かないため、表示もホスト名にする
    seen_host, link_rows = set(), []
    for l in links:
        host = re.sub(r"^www\.", "", (l["url"] or "").split("/")[2]) if "//" in (l["url"] or "") else l["url"]
        if not host or host in seen_host:
            continue
        seen_host.add(host)
        link_rows.append((l["label"], host, l["url"]))
    link_html = "".join(
        '<div class="lrow"><a href="%s" rel="noopener nofollow" target="_blank">%s</a></div>'
        % (e(u), e(host)) for lab, host, u in link_rows[:8]) \
        or '<p class="empty">公式サイトの情報がありません。</p>'
    parts.append("""<div class="cols3">
<div><h4>スタッフ</h4>%(staff)s</div>
<div><h4>タグ</h4><div class="tags">%(tags)s</div>%(ser)s</div>
<div><h4>リンク</h4>%(links)s
<div class="lrow"><a href="https://vndb.org/%(vid)s" rel="noopener">VNDB のページ</a></div></div>
</div>""" % dict(staff=staff_html, tags=tag_html, links=link_html, vid=e(w["vid"]),
                 ser=('<h4 style="margin:26px 0 4px">シリーズ</h4>%s' % ser_html) if series else ""))

    desc = "%s（%s）の攻略キャラクター・声優・買えるお店。" % (title, w["platform_top"] or "")
    crumbs = [(SITE_NAME, "/"), ("作品を探す", "/"),
              (w["platform_top"] or "作品", None), (title, None)]
    ld = [{"@context": "https://schema.org", "@type": "VideoGame", "name": title,
           "url": BASE_URL + url, "gamePlatform": (w["platforms"] or "").split(" / "),
           "datePublished": w["released"] or None,
           "publisher": w["publisher"] or None, "author": w["brand"] or None,
           "inLanguage": "ja"}]
    return layout(("%s | %s" % (title, SITE_NAME)), desc, url, "".join(parts),
                  crumbs=crumbs, current="作品を探す", og_image=w["cover"], jsonld=ld)


# ---------------------------------------------------------------- トップ

def home_page(works, meta, upcoming, plat_opts, year_opts):
    n = lambda k: int(meta.get(k, 0))
    cats = "".join("""<a class="cat-card" href="/%(k)s/">
<b>%(lab)sから探す</b><div class="n">%(n)s件</div><div class="note">%(note)s</div></a>"""
                   % dict(k=k, lab=lab, n=format(n("n_" + k), ","), note=note)
                   for k, lab, note in CATS)

    scopes = "".join(
        '<button type="button" class="scope" data-scope="%s" aria-pressed="%s"><i></i><span>%s</span></button>'
        % (k, "true" if i == 0 else "false", lab)
        for i, (k, lab) in enumerate([("all", "すべて"), ("title", "作品名"),
                                      ("cv", "声優名"), ("char", "キャラクター名")]))
    opt = lambda xs: "".join('<option value="%s">%s</option>' % (e(v), e(t)) for v, t in xs)
    filters = """<select class="input" data-f="plat" aria-label="機種">%s</select>
<select class="input" data-f="year" aria-label="発売年">%s</select>
<select class="input" data-f="age" aria-label="対象年齢">%s</select>
<select class="input" data-f="sort" aria-label="並び順">%s</select>""" % (
        opt([("", "機種：すべて")] + plat_opts),
        opt([("", "発売年：すべて")] + year_opts),
        opt([("", "対象年齢：すべて")] + [(k, lab) for k, lab, _lo, _hi in AGE_TIERS]),
        opt([("date_desc", "発売日が新しい順"), ("date_asc", "発売日が古い順"),
             ("rating", "評価が高い順"), ("votes", "票数が多い順"), ("title", "作品名順")]))

    recent = [w for w in works if not w["is_upcoming"]][:12]
    body = """<div class="pad">
<div class="home-head">
<div><h1>%(site)s</h1><p class="home-lead">%(desc)s
家庭用ゲーム%(nw)s作品・声優%(ncv)s人を収録。</p></div>
<span class="home-mark">Otome Index</span>
</div>

<section class="search-panel" id="search">
<div class="search-title">乙女ゲームを探す</div>
<div class="scopes" role="group" aria-label="検索の対象">%(scopes)s</div>
<label class="sr-only" for="q">検索語</label>
<input class="input" id="q" type="search" autocomplete="off"
 placeholder="作品名・声優名・キャラクター名（かな・ローマ字も可）" style="margin-top:14px;min-height:48px">
<button type="button" class="btn btn-secondary filter-toggle" data-filter-toggle
 aria-expanded="false">絞り込み（機種・年・年齢・並び順）＋</button>
<div class="filters" data-filters>%(filters)s</div>
<div class="search-actions">
<button type="button" class="btn btn-primary" data-search>この条件で探す</button>
<button type="button" class="btn btn-ghost" data-clear>条件をクリア</button>
</div>
<noscript><p class="text-muted" style="font-size:12px;margin-top:10px">
検索と絞り込みは JavaScript で動きます。下の一覧や各カテゴリのページからも辿れます。</p></noscript>
</section>

<section data-results hidden>
<div class="sec-head"><h2>検索結果</h2><span class="text-muted" data-results-count></span></div>
<div class="grid" data-results-grid></div>
<div style="margin-top:20px"><button type="button" class="btn btn-secondary" data-more-results hidden>さらに読み込む</button></div>
</section>

<div data-browse>
<div class="sec-head"><h2>カテゴリから探す</h2></div>
<div class="grid">%(cats)s</div>

%(upcoming)s

<div class="sec-head"><h2>最近発売された作品</h2>
<a href="/all/">すべて見る（%(nw)s作品）</a></div>
<div class="grid">%(recent)s</div>
<div style="margin-top:20px"><a class="btn btn-secondary" href="/all/">作品をすべて見る</a></div>
</div>
</div>""" % dict(
        site=e(SITE_NAME), desc=e(SITE_DESC),
        nw=format(n("n_work"), ","), ncv=format(n("n_cv"), ","),
        scopes=scopes, filters=filters, cats=cats,
        recent="".join(work_card(w) for w in recent),
        upcoming=("""<div class="sec-head"><h2>発売予定</h2><a href="/upcoming/">新作カレンダー</a></div>
<div class="grid">%s</div>""" % "".join(work_card(w) for w in upcoming)) if upcoming else "")
    return layout(SITE_NAME, SITE_DESC + SCOPE_NOTE, "/", body, current="ホーム")


# ---------------------------------------------------------------- MY棚

def my_page():
    stats = "".join(
        """<button type="button" data-stat="%s" aria-pressed="%s">
<div class="lb">%s</div><div class="nb"><b data-n="%s">0</b><span>本</span></div></button>"""
        % (k, "true" if k == "owned" else "false", lab, k)
        for k, lab in [("owned", "所持作品"), ("backlog", "未プレイ"), ("playing", "プレイ中"),
                       ("cleared", "クリア済"), ("want", "欲しい作品")])
    body = """<div class="pad" style="padding-bottom:0">
<div class="home-head">
<div><div class="kicker">My Otome</div><h1 style="font-size:36px;margin:6px 0 0">わたしの乙女ゲーム棚</h1></div>
<div style="display:flex;align-items:center;gap:12px">
<span class="text-muted" style="font-size:13px" data-my-updated></span>
<button type="button" class="btn btn-primary" data-my-add>＋ ゲームを登録</button></div>
</div>
<div class="stats" role="group" aria-label="棚の内訳">%(stats)s</div>
</div>

<div class="callout" data-backlog hidden>
<div><b data-backlog-title></b><div style="font-size:13px;margin-top:4px" data-backlog-note></div></div>
<button type="button" class="btn btn-primary" data-backlog-go>積みゲーを見る</button>
</div>

<div class="pad" style="padding-bottom:0">
<div class="filterbar">
<label class="sr-only" for="myq">棚の中を検索</label>
<input class="input" id="myq" type="search" placeholder="棚のなかを検索（作品名）"
 style="flex:1;min-width:240px" data-my-q>
<div class="plat-tabs" data-my-plats></div>
<label class="sr-only" for="mysort">並び順</label>
<select class="input" id="mysort" data-my-sort style="max-width:200px">
<option value="added_desc">登録が新しい順</option>
<option value="date_desc">購入日が新しい順</option>
<option value="date_asc">購入日が古い順</option>
<option value="price_desc">購入価格が高い順</option>
<option value="title">作品名順</option>
</select>
</div>
<div class="sec-head" style="margin-top:0;border-bottom:0"><h2 data-my-title>所持作品</h2>
<span class="text-muted" style="font-size:13px" data-my-count></span></div>
</div>

<div class="pad" style="padding-top:0" data-my-list></div>

<div class="two sec-top">
<div><h2>あなたの好きな乙女ゲーム</h2>
<p class="text-muted" style="font-size:13px" data-my-trait-lead>棚に登録するとここに集計が出ます。</p>
<div data-my-traits></div></div>
<div><h2>好みに近い未所持作品</h2>
<p class="text-muted" style="font-size:13px">棚の属性傾向から選んでいます。</p>
<div data-my-rec></div></div>
</div>

<dialog class="modal" data-my-modal>
<form method="dialog" data-my-form>
<div class="modal-hd"><b data-my-modal-title>ゲームを登録</b>
<button type="button" class="btn btn-ghost" data-my-close>閉じる</button></div>
<div class="modal-bd">
<div class="flabel">1. 作品を検索</div>
<input class="input" type="search" placeholder="作品名を入力" data-my-search autocomplete="off">
<div data-my-hits style="max-height:180px;overflow:auto"></div>
<div data-my-picked style="margin-top:8px;font-weight:700"></div>

<div class="flabel">2. 機種・版</div>
<div class="two-col">
<select class="input" data-my-plat aria-label="機種"></select>
<select class="input" data-my-ed aria-label="版"></select>
</div>

<div class="flabel">3. ステータス</div>
<div class="seg" data-my-status>
<button type="button" data-status="want" aria-pressed="false">欲しい</button>
<button type="button" data-status="reserved" aria-pressed="false">予約済</button>
<button type="button" data-status="owned" aria-pressed="true">所持</button>
<button type="button" data-status="playing" aria-pressed="false">プレイ中</button>
<button type="button" data-status="cleared" aria-pressed="false">クリア</button>
</div>

<div class="two-col" style="margin-top:18px">
<div><div class="flabel" style="margin-top:0">購入日</div>
<input class="input" type="date" data-my-date></div>
<div><div class="flabel" style="margin-top:0">購入価格</div>
<input class="input" type="number" min="0" step="1" placeholder="12800" data-my-price></div>
</div>

<div class="two-col" style="margin-top:18px">
<div><div class="flabel" style="margin-top:0">読了ルート</div>
<input class="input" type="number" min="0" step="1" placeholder="3" data-my-read></div>
<div><div class="flabel" style="margin-top:0">全ルート</div>
<input class="input" type="number" min="0" step="1" placeholder="6" data-my-total></div>
</div>

<div class="flabel">購入先・メモ</div>
<input class="input" type="text" placeholder="アニメイト通販" data-my-shop>
<textarea class="input" style="margin-top:10px" placeholder="ドラマCD目当てで限定版。"
 data-my-memo></textarea>
</div>
<div class="modal-ft">
<button type="button" class="btn btn-primary" data-my-save>棚に登録する</button>
<button type="button" class="btn btn-secondary" data-my-delete hidden>棚から削除</button>
<button type="button" class="btn btn-ghost" data-my-close>キャンセル</button>
</div>
</form>
</dialog>

<div class="pad sec-top">
<h4>データの持ち方</h4>
<p class="text-muted" style="font-size:13px">MY棚の内容はサーバーには送られず、
このブラウザの中（localStorage）だけに保存されます。別の端末やブラウザからは見えません。
書き出し・読み込みで移せます。</p>
<div style="display:flex;gap:10px;flex-wrap:wrap">
<button type="button" class="btn btn-secondary" data-my-export>棚を書き出す（JSON）</button>
<button type="button" class="btn btn-secondary" data-my-import>棚を読み込む</button>
<input type="file" accept="application/json" data-my-file hidden>
</div>
<noscript><p class="empty">MY棚は JavaScript が必要です。</p></noscript>
</div>""" % dict(stats=stats)
    return layout("MY棚 | " + SITE_NAME, "所持・欲しい・プレイ状況を記録して、好みの傾向を見る。",
                  "/my/", body, crumbs=[(SITE_NAME, "/"), ("MY棚", None)], current="MY棚")


# ---------------------------------------------------------------- 一覧・索引

def list_page(title, desc, path, works, crumbs, lead="", current="作品を探す"):
    body = """<div class="pad">
<div class="home-head"><div><h1>%(t)s</h1>%(lead)s</div>
<span class="text-muted" style="font-size:13px">%(n)s作品</span></div>
<label class="sr-only" for="lq">この一覧を絞り込む</label>
<input class="input" id="lq" type="search" placeholder="この一覧を作品名で絞り込む"
 style="margin-top:18px;max-width:420px" data-list-filter>
<div class="grid" data-list-grid>%(cards)s</div>
<p class="empty" data-list-empty hidden>該当する作品がありません。</p>
</div>""" % dict(t=e(title), n=format(len(works), ","),
                 lead=('<p class="home-lead">%s</p>' % e(lead)) if lead else "",
                 cards="".join(work_card(w) for w in works))
    return layout("%s | %s" % (title, SITE_NAME), desc, path, body,
                  crumbs=crumbs, current=current)


def catalog_index_page(kind, label, note, entries):
    """/cv/ /tag/ のような「索引の索引」。件数の多い順に並べ、名前で絞り込める"""
    rows = "".join(
        '<a class="idx-name" href="%s"><b>%s</b><span class="n">%s作品</span></a>'
        % (e(c["url"]), e(c["label"]), format(c["n_works"], ","))
        for c in entries)
    body = """<div class="pad">
<div class="home-head"><div><h1>%(lab)sから探す</h1><p class="home-lead">%(note)s</p></div>
<span class="text-muted" style="font-size:13px">%(n)s件</span></div>
<label class="sr-only" for="cq">名前で絞り込む</label>
<input class="input" id="cq" type="search" placeholder="名前で絞り込む"
 style="margin-top:18px;max-width:420px" data-list-filter>
<div class="idx-cols" style="margin-top:18px" data-list-grid>%(rows)s</div>
<p class="empty" data-list-empty hidden>該当する項目がありません。</p>
</div>""" % dict(lab=e(label), note=e(note), n=format(len(entries), ","), rows=rows)
    return layout("%sから探す | %s" % (label, SITE_NAME),
                  "%s%s" % (label, note), "/%s/" % kind, body,
                  crumbs=[(SITE_NAME, "/"), ("%sから探す" % label, None)])


# ---------------------------------------------------------------- 本体

def main():
    if not os.path.exists(DB):
        sys.exit("表示用DBが無い。先に python3 scripts/site2_db.py を実行する")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    meta = dict(con.execute("SELECT key, value FROM meta"))
    works = list(con.execute("SELECT * FROM work ORDER BY sort_date DESC, title"))
    by_vid = {w["vid"]: w for w in works}
    print("作品: %d件" % len(works))

    chars = defaultdict(list)
    for r in con.execute("SELECT * FROM work_char ORDER BY vid, sort"):
        chars[r["vid"]].append(r)
    # catalog は英語のままの語を落としてある。本文もそれに揃える
    ok_tag = {r[0] for r in con.execute("SELECT label FROM catalog WHERE kind='tag'")}
    ok_trait = {r[0] for r in con.execute("SELECT label FROM catalog WHERE kind='trait'")}
    ctraits = defaultdict(lambda: defaultdict(list))
    for r in con.execute("SELECT * FROM work_trait WHERE url IS NOT NULL"):
        if r["trait"] in ok_trait:
            ctraits[r["vid"]][r["cid"]].append(r)
    tags = defaultdict(list)
    for r in con.execute("SELECT * FROM work_tag WHERE url IS NOT NULL"):
        if r["tag"] in ok_tag:
            tags[r["vid"]].append(r)
    staff = defaultdict(list)
    for r in con.execute("SELECT * FROM work_staff ORDER BY vid, sort"):
        staff[r["vid"]].append(r)
    links = defaultdict(list)
    for r in con.execute("SELECT * FROM work_link"):
        links[r["vid"]].append(r)
    series = defaultdict(list)
    for r in con.execute("SELECT * FROM work_series ORDER BY vid, sort"):
        series[r["vid"]].append(r)
    eds = defaultdict(list)
    for r in con.execute("SELECT * FROM work_edition ORDER BY vid, sort"):
        eds[r["vid"]].append(r)
    offers = defaultdict(list)
    for r in con.execute("SELECT * FROM work_offer ORDER BY eid, priority"):
        offers[r["eid"]].append(r)

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    # style.css / app.js は手書きの資産なので、消さずに退避して戻す
    assets_src = os.path.join(ROOT, "scripts", "assets", "v2")
    os.makedirs(os.path.join(OUT, "assets"), exist_ok=True)
    for name in ("style.css", "app.js"):
        src = os.path.join(assets_src, name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(OUT, "assets", name))

    urls = []

    # ---- 作品ページ ----
    for w in works:
        html_ = game_page(w, chars[w["vid"]], ctraits[w["vid"]], tags[w["vid"]],
                          staff[w["vid"]], links[w["vid"]], series[w["vid"]],
                          eds[w["vid"]], offers, meta)
        write(w["url"], html_)
        urls.append(w["url"])
    print("  作品ページ %d" % len(works))

    # ---- 索引 ----
    cat = defaultdict(list)
    for c in con.execute("SELECT * FROM catalog ORDER BY n_works DESC, label"):
        cat[c["kind"]].append(c)
    cw = defaultdict(list)
    for r in con.execute("SELECT url, vid FROM catalog_work ORDER BY url, sort DESC"):
        cw[r["url"]].append(r["vid"])

    n_list = 0
    for kind, label, note in CATS:
        entries = cat.get(kind, [])
        write("/%s/" % kind, catalog_index_page(kind, label, note, entries))
        urls.append("/%s/" % kind)
        for c in entries:
            ws = [by_vid[v] for v in cw[c["url"]] if v in by_vid]
            ws.sort(key=lambda w: (w["sort_date"] or "0000"), reverse=True)
            write(c["url"], list_page(
                c["label"], "%s の作品一覧（%d作品）。" % (c["label"], len(ws)),
                c["url"], ws,
                crumbs=[(SITE_NAME, "/"), ("%sから探す" % label, "/%s/" % kind), (c["label"], None)],
                lead="%s：%d作品" % (label, len(ws))))
            urls.append(c["url"])
            n_list += 1
    print("  索引 %d種 / 一覧 %d" % (len(CATS), n_list))

    # ---- 全作品・発売予定 ----
    upcoming = sorted([w for w in works if w["is_upcoming"]],
                      key=lambda w: w["sort_date"] or "9999")
    write("/all/", list_page("すべての作品", "収録している乙女ゲーム%d作品の一覧。" % len(works),
                             "/all/", works, crumbs=[(SITE_NAME, "/"), ("すべての作品", None)],
                             lead=SCOPE_NOTE))
    write("/upcoming/", list_page(
        "新作カレンダー", "これから発売される乙女ゲームの一覧。", "/upcoming/", upcoming,
        crumbs=[(SITE_NAME, "/"), ("新作カレンダー", None)],
        lead="発売日が今日以降の作品です。" if upcoming else
             "現在、発売日が今日以降の作品はデータにありません。",
        current="新作"))
    urls += ["/all/", "/upcoming/"]

    # ---- トップ・MY棚 ----
    plat_opts = [(c["label"], c["label"]) for c in cat.get("platform", [])][:14]
    years = sorted({w["year"] for w in works if w["year"]}, reverse=True)
    year_opts = [(str(y), "%d年" % y) for y in years]
    write("/", home_page(works, meta, upcoming[:4], plat_opts, year_opts))
    write("/my/", my_page())
    urls += ["/", "/my/"]

    # ---- 検索・MY棚が使うデータ ----
    build_assets(con, works, chars, ctraits, cat, cw)

    # ---- sitemap ----
    with open(os.path.join(OUT, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for u in urls:
            f.write("<url><loc>%s%s</loc></url>\n" % (e(BASE_URL), e(u)))
        f.write("</urlset>\n")

    print("  合計 %d ページ → %s" % (len(urls), OUT))


def build_assets(con, works, chars, ctraits, cat, cw):
    """検索・絞り込み・好み一致がブラウザ側で使うデータ。

    index.json  … 作品の一覧（検索と並べ替えの母集合）
    traits.json … 作品ごとの属性。好み一致の計算に使う
    suggest.json… 声優名・キャラクター名からの検索
    """
    out = os.path.join(OUT, "assets")
    os.makedirs(out, exist_ok=True)

    idx = [{"v": w["vid"], "t": w["title"], "l": (w["title_latin"] or "").lower(),
            "u": w["url"], "c": w["cover"], "d": w["released"] or "",
            "dj": w["released_ja"], "p": w["platform_top"] or "",
            "ps": (w["platforms"] or ""), "r": w["rating"], "rt": w["rating_text"],
            "n": w["votecount"] or 0, "y": w["year"], "a": w["age_key"] or "",
            "k": w["n_capture"], "up": w["is_upcoming"],
            "s": w["series_key"] or w["vid"]}
           for w in works]
    json.dump(idx, open(os.path.join(out, "index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))

    # 好み一致に使う属性の選び方。
    #
    # 素直に全属性を集計すると「短髪・茶髪・金髪・黒髪」が上位を占める。
    # 髪や瞳の色は攻略対象が5人もいればどの作品にも1人はいるので、
    # 何作品持っていても必ず1位になり、好みの情報を1ビットも持たない。
    # 実測でも、1作品登録しただけで全作品が「一致99%」になった。
    #
    # 対策は2つ。
    #   1. 見た目のカテゴリ（髪・瞳・服装・外見）を母集合から外す。
    #      残すのは役柄・性格・境遇・行動・持ち物。人物像を表す側だけにする。
    #   2. 残った属性も出現頻度で重みを変える（IDF）。637作品中500作品に
    #      あるような属性は当たっても意味が無いので、df を一緒に配って
    #      ブラウザ側で log(N/df) を掛ける。
    TASTE_CATS = {"役柄", "性格", "境遇", "行動", "持ち物"}
    ok_trait = {c["label"] for c in cat.get("trait", [])}
    df = defaultdict(int)
    raw = {}
    for w in works:
        got = set()
        for cid, ts in ctraits[w["vid"]].items():
            for t in ts:
                if t["cat"] in TASTE_CATS and t["trait"] in ok_trait:
                    got.add(t["trait"])
        raw[w["vid"]] = got
        for name in got:
            df[name] += 1
    # ほぼ全作品にある属性と、1作品にしか無い属性は、どちらも比較に使えない
    n_all = len(works)
    names = sorted([nm for nm, d in df.items() if 2 <= d <= n_all * 0.6],
                   key=lambda nm: -df[nm])
    keep = {nm: i for i, nm in enumerate(names)}
    curl = {c["label"]: c["url"] for c in cat.get("trait", [])}
    tw = {w["vid"]: sorted(keep[nm] for nm in raw[w["vid"]] if nm in keep) for w in works}
    json.dump({"names": names, "urls": [curl.get(nm) for nm in names],
               "df": [df[nm] for nm in names], "n": n_all, "works": tw},
              open(os.path.join(out, "traits.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print("  好み一致に使う属性 %d件（%d件から絞り込み）" % (len(names), len(df)))

    # 声優名・キャラクター名 → 作品。同名は作品ごとに1件ずつ
    cvs, chs = defaultdict(list), defaultdict(list)
    for w in works:
        for c in chars[w["vid"]]:
            if c["cv"]:
                cvs[c["cv"]].append(w["vid"])
            if c["name"] and c["role"] in ("主人公", "攻略対象"):
                chs[c["name"]].append(w["vid"])
    cv_url = {c["label"]: c["url"] for c in cat.get("cv", [])}
    json.dump({"cv": {k: {"u": cv_url.get(k), "w": sorted(set(v))} for k, v in cvs.items()},
               "char": {k: sorted(set(v)) for k, v in chs.items()}},
              open(os.path.join(out, "suggest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))

    # MY棚の登録ダイアログが出す「機種・版」の選択肢
    edmap = defaultdict(lambda: defaultdict(list))
    for r in con.execute("SELECT vid, platform_ja, edition_label, is_dl, sort "
                         "FROM work_edition ORDER BY vid, sort"):
        lab = r["edition_label"] or ("DL版" if r["is_dl"] else "通常版")
        if lab not in edmap[r["vid"]][r["platform_ja"]]:
            edmap[r["vid"]][r["platform_ja"]].append(lab)
    json.dump({v: dict(m) for v, m in edmap.items()},
              open(os.path.join(out, "editions.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))

    for name in ("index.json", "traits.json", "suggest.json", "editions.json"):
        print("  assets/%s %.0fKB" % (name, os.path.getsize(os.path.join(out, name)) / 1024))


if __name__ == "__main__":
    main()
