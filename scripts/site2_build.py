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
from collections import OrderedDict, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, ROOT
import affiliate_config as AF
from site_config import (SITE_NAME, SITE_DESC, SITE_URL, REPO_URL, SCOPE_NOTE,
                         PUBLISH, BASE_PATH, AGE_TIERS, year_bucket, year_label, year_sort)
# 五十音の行分けと並び替えは v1 で作り込んであるものを借りる。
# 「ch/ts は た行」「拗音は い段＋や行に開く」といった判断を2つ持ちたくない。
# site_build は import しても main() が走らないので、v1 の出力には影響しない。
from site_build import KANA_ROWS, kana_row, kana_sort_key, slug_key

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

def layout(title, desc, path, body, crumbs=None, current="", og_image=None, jsonld=None,
           bottom_bar=None, extra_js=""):
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
    # スマホの下部は場所が1つしかない。作品ページでは全体ナビより
    # 「いくらで買えるか」のほうが要るので、あればそちらに差し替える。
    # デザインのモバイル版も作品ページだけ下部が購入バーになっていた。
    tabbar = bottom_bar if bottom_bar else '<nav class="tabbar" aria-label="下部ナビ">%s</nav>' % "".join(
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
%(tabbar)s
</div>
<script>window.V2_BASE=%(prefix)s;</script>
<script src="/assets/app.js" defer></script>%(extrajs)s
</body>
</html>""" % dict(
        title=e(title), desc=e(desc), canon=e(canon), site=e(SITE_NAME),
        sitedesc=e(SITE_DESC), scope=e(SCOPE_NOTE), repo=e(REPO_URL),
        body=body, crumb=crumb, ld=ld, navlinks=nav_links, tabbar=tabbar,
        prefix=json.dumps(PREFIX), extrajs=extra_js,
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


def hero_cover(w, alt):
    """作品ページ・キャラクターページのヒーローに置く表紙。

    パッケージの縦横比は実測で 0.562〜1.667 とばらつく（最多は「とても縦長」の
    203枚）。3:4 に固定して object-fit:cover で埋めると、縦長の箱ほど上下が
    切り落とされる。ここでは比率を決め打ちせず、DBに入っている実寸を
    width/height 属性に書いて本来の形のまま出す。属性を書いておけば
    読み込み前から場所が正しく確保されるので、ガタつきも起きない。
    """
    if not w["cover"]:
        return '<div class="ph" style="aspect-ratio:3/4"><span>画像なし</span></div>'
    size = ('width="%d" height="%d"' % (w["cover_w"], w["cover_h"])) \
        if w["cover_w"] and w["cover_h"] else ""
    return ('<img class="cover cover-hero" src="%s" alt="%s" %s>'
            % (e(w["cover"]), e(alt), size))


def work_card(w):
    """トップと一覧に並ぶ作品カード。所持マークはJSが後から入れる"""
    meta = " ".join(x for x in [w["released_ja"], w["platform_top"]] if x)
    return ("""<a class="wcard" href="%(url)s" data-vid="%(vid)s">%(img)s<div class="body">
<div class="t">%(title)s</div><div class="m">%(meta)s</div>
<div class="r"><span class="%(scls)s">%(score)s</span><span class="own" data-own></span></div>
</div></a>""" % dict(url=e(w["url"]), vid=e(w["vid"]), img=thumb(w),
                     title=e(w["title"]), meta=e(meta), score=e(w["rating_text"]),
                     scls="score" if w["rating"] else "score score-none"))


# ---------------------------------------------------------------- 購入導線

def lowest_price(eds, offers):
    """このページで実際に出せる最安値。24時間以内に取れた価格だけを見る。
    合本（他作品とのセット）はこの作品の相場ではないので除く"""
    ps = []
    for r in eds:
        if r["is_combo"]:
            continue
        for o in offers.get(r["eid"], []):
            p = fresh_price(o["price"], o["fetched_at"])
            if p:
                ps.append(p)
    return min(ps) if ps else None


def buy_section(w, eds, offers, picked=None):
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
    if picked is not None:
        picked["eid"] = first_eid
        picked["name"] = ed_names.get(first_eid, "")
        ps = [fresh_price(o["price"], o["fetched_at"]) for o in offers.get(first_eid, [])]
        ps = [x for x in ps if x]
        picked["price"] = min(ps) if ps else None
    b.append('<div class="step">3. 販売店 — <span data-ed-name>%s</span></div>'
             % e(ed_names.get(first_eid, "")))
    # 新品・中古・ダウンロードは別の買いものなので節を分ける。
    # ひとまとめにして価格順に並べると「楽天 新品 ¥6,700」の下に
    # 「楽天 中古 ¥7,381」が来て、中古のほうが高いのに並んで見えてしまう。
    # 最安の印も節ごとに付ける（新品の最安と中古の最安は別々に知りたい）。
    COND_ORDER = {"新品": 0, "中古": 1, "ダウンロード": 2}
    b.append('<div class="wrap-x"><table class="table" data-shops><thead><tr>'
             '<th>販売店</th><th>価格</th><th>在庫</th><th></th>'
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
            by_cond = defaultdict(list)
            for o in best.values():
                by_cond[o["condition"] or "その他"].append(o)
            for cond in sorted(by_cond, key=lambda c: COND_ORDER.get(c, 9)):
                rows_o = sorted(by_cond[cond], key=lambda o: (
                    0 if fresh_price(o["price"], o["fetched_at"]) else 1,
                    fresh_price(o["price"], o["fetched_at"]) or 0, o["priority"]))
                cheapest = min([fresh_price(o["price"], o["fetched_at"]) for o in rows_o
                                if fresh_price(o["price"], o["fetched_at"])] or [None])
                b.append('<tr class="cond-row" data-eid="%s"><th colspan="4" scope="colgroup">'
                         '%s</th></tr>' % (e(r["eid"]), e(cond)))
                for o in rows_o:
                    p = None if r["is_combo"] else fresh_price(o["price"], o["fetched_at"])
                    verb = ("で見る" if cond == "ダウンロード" or o["link_type"] == "item"
                            else "で探す")
                    b.append("""<tr data-eid="%(eid)s" data-best="%(best)s">
<td>%(shop)s</td><td class="price">%(price)s</td><td>%(stock)s</td>
<td><a class="btn btn-primary" href="%(url)s" rel="nofollow sponsored noopener"
   target="_blank">%(shop)s%(verb)s</a></td></tr>""" % dict(
                        eid=e(r["eid"]), best="1" if p and p == cheapest else "0",
                        shop=e(o["channel"]),
                        price=yen(p) if p else "—",
                        stock=e(o["availability"] or ("配信中" if cond == "ダウンロード" else "各店で確認")),
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


# 最新の移植版のページ。/switch/ や -switch、ns/ など書き方に幅がある
SWITCH_URL = re.compile(r"(?:^|[/\-_.])(?:switch|ns)(?:[/\-_.]|$)", re.I)
# 作品そのものではないページ。代表には選びたくない
GENERIC_URL = re.compile(r"special-pack|/smp/|web\.archive\.org|/shop|/store", re.I)


def link_score(url, has_switch):
    """同じホストの中で、どのURLを代表にするかの点数。

    Switch版がある作品は Switch のページを最優先する。そこが一番新しく、
    生きている可能性も高い。次に https、最後に「作品そのもののページか」。
    """
    u = url or ""
    n = 0
    if has_switch and SWITCH_URL.search(u):
        n += 100
    if u.startswith("https://"):
        n += 10
    if GENERIC_URL.search(u):
        n -= 50
    # 同点なら短いほう（トップページ）を採る
    return n - len(u) / 1000.0


def game_page(w, chars, ctraits, tags, staff, links, series, eds, offers, meta, ch_url):
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
    # スマホでは facts 8行（機種が11個並ぶ作品もある）だけで 700px を超え、
    # 購入導線がさらに下へ押し出される。4行だけ出して残りは畳む。
    # 畳むのはJSが data-facts-collapsed を付けたときだけなので、JSが無ければ全部出る。
    n_facts = sum(1 for k, v in facts if v)
    facts_toggle = ('<button type="button" class="facts-toggle" data-facts-toggle'
                    ' aria-expanded="false">ほか%d項目を見る</button>' % (n_facts - 4)) \
        if n_facts > 4 else ""

    hero = """<div class="hero">
<div class="hero-img">%(cover)s</div>
<div class="hero-main">
<div class="kicker">%(genre)s</div>
<h1>%(title)s</h1>
<div class="hero-sub">%(lead)s</div>
<div class="tags" style="margin-bottom:20px">%(chips)s</div>
<dl class="facts" data-facts>%(facts)s</dl>%(ftoggle)s
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
</div>""" % dict(cover=hero_cover(w, "%s のパッケージ" % title),
                 genre=e(w["genre_label"]), title=e(title), lead=e(" / ".join(lead)),
                 chips="".join(chips), facts=facts_html, ftoggle=facts_toggle,
                 vid=e(w["vid"]))

    # スマホでは1ページが11画面分あり、購入導線が2画面下に沈む。
    # デザインのモバイル版と同じく「買う / 作品 / 好み」で畳む。
    # data-sec は表示の出し分けにだけ使い、JSが無ければ全部そのまま並ぶ。
    tabs = ('<div class="sec-tabs" role="tablist" aria-label="表示する内容">'
            '<button type="button" role="tab" data-sec-tab="buy" aria-selected="true">買う</button>'
            '<button type="button" role="tab" data-sec-tab="work" aria-selected="false">作品</button>'
            '<button type="button" role="tab" data-sec-tab="taste" aria-selected="false">好み</button>'
            '</div>')
    picked = {}
    parts = [hero, tabs, '<div data-sec="buy">', buy_section(w, eds, offers, picked), '</div>',
             '<div data-sec="work">']

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
            nm = ('<a href="%s">%s</a>' % (e(ch_url[c["cid"]]), e(c["name"]))) \
                if c["cid"] in ch_url else e(c["name"])
            cards.append("""<div class="char">
<div class="thumb ph" aria-hidden="true"></div>
<div><div class="n">%(name)s</div><div class="cv">%(cv)s</div>
<div class="text-muted" style="font-size:12px">%(bits)s</div>
<div class="tags" style="margin-top:8px">%(chip)s</div></div></div>"""
                         % dict(name=nm, cv=cv,
                                bits=e(" / ".join(bits)), chip=chip))
        parts.append("""<section class="pad sec">
<div class="sec-head" style="margin-top:0"><h2>キャラクター</h2>
<span class="text-muted" style="font-size:12px">属性を押すと同じ属性の作品を探せます</span></div>
<div class="chars">%s</div>%s</section>""" % (
            "".join(cards),
            ('<p class="text-muted" style="font-size:12px;margin-top:18px">'
             'このほか %d人が登場します。</p>' % len(sub_chars)) if sub_chars else ""))

    # 好みとの一致（MY棚のデータを使うのでJSで描く）
    parts.append('</div><div data-sec="taste">')
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
    # 「公式サイト」が35行並んでも区別が付かないため、表示もホスト名にする。
    #
    # どれを代表にするかは順番任せにしない。移植のたびに新しいページが立ち、
    # 古いページは情報が古いまま残る（Collar×Malice なら 2016年の
    # http://…/collar_malice/ ではなく https://…/collar_malice/switch/ を出したい）。
    has_switch = any("switch" in ((ed["platform"] or "") + (ed["platform_ja"] or "")).lower()
                     for ed in eds)
    by_host = OrderedDict()
    for l in links:
        u = l["url"] or ""
        host = re.sub(r"^www\.", "", u.split("/")[2]) if "//" in u else u
        if not host:
            continue
        by_host.setdefault(host, []).append(l)
    link_rows = []
    for host, group in by_host.items():
        l = max(group, key=lambda x: link_score(x["url"], has_switch))
        link_rows.append((l["label"], host, l["url"]))
    link_html = "".join(
        '<div class="lrow"><a href="%s" rel="noopener nofollow" target="_blank">%s</a></div>'
        % (e(u), e(host)) for lab, host, u in link_rows[:8]) \
        or '<p class="empty">公式サイトの情報がありません。</p>'
    parts.append('</div><div data-sec="work">')
    parts.append("""<div class="cols3">
<div><h3 class="col-h">スタッフ</h3>%(staff)s</div>
<div><h3 class="col-h">タグ</h3><div class="tags">%(tags)s</div>%(ser)s</div>
<div><h3 class="col-h">リンク</h3>%(links)s
<div class="lrow"><a href="https://vndb.org/%(vid)s" rel="noopener">VNDB のページ</a></div></div>
</div>""" % dict(staff=staff_html, tags=tag_html, links=link_html, vid=e(w["vid"]),
                 ser=('<h3 class="col-h">シリーズ</h3>%s' % ser_html) if series else ""))
    parts.append('</div>')

    # 「最安」とだけ書くと、どの機種のどの状態の値段か分からない。
    # 選ばれている版の名前と一緒に出し、版を切り替えたらJSが書き換える。
    bar = ('<div class="buybar">'
           '<div style="min-width:0"><span class="buybar-lb" data-bar-name>%s</span>'
           '<b class="buybar-price" data-bar-price>%s</b></div>'
           '<a class="btn btn-primary" href="#buy" data-go-buy>購入先を見る</a></div>'
           % (e(picked.get("name") or "販売店"),
              (yen(picked["price"]) + "〜") if picked.get("price") else "各店で確認")) \
        if eds and picked.get("eid") else None

    desc = "%s（%s）の攻略キャラクター・声優・買えるお店。" % (title, w["platform_top"] or "")
    crumbs = [(SITE_NAME, "/"), ("作品を探す", "/"),
              (w["platform_top"] or "作品", None), (title, None)]
    ld = [{"@context": "https://schema.org", "@type": "VideoGame", "name": title,
           "url": BASE_URL + url, "gamePlatform": (w["platforms"] or "").split(" / "),
           "datePublished": w["released"] or None,
           "publisher": w["publisher"] or None, "author": w["brand"] or None,
           "inLanguage": "ja"}]
    return layout(("%s | %s" % (title, SITE_NAME)), desc, url, "".join(parts),
                  crumbs=crumbs, current="作品を探す", og_image=w["cover"], jsonld=ld,
                  bottom_bar=bar)


# ---------------------------------------------------------------- トップ

def home_page(works, meta, upcoming, plat_opts, year_opts):
    n = lambda k: int(meta.get(k, 0))
    entries = [("character", "キャラクター", "人",
                "主人公・攻略対象を名前の五十音から。声優と属性も分かります。")] + \
              [(k, lab, "件", note) for k, lab, note in CATS]
    cats = "".join("""<a class="cat-card" href="/%(k)s/">
<b>%(lab)sから探す</b><div class="n">%(n)s%(unit)s</div><div class="note">%(note)s</div></a>"""
                   % dict(k=k, lab=lab, unit=unit,
                          n=format(n("n_" + ("character" if k == "character" else k)), ","),
                          note=note)
                   for k, lab, unit, note in entries)

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
<div class="combo">
<input class="input" id="q" type="search" autocomplete="off" role="combobox"
 aria-expanded="false" aria-controls="q-sug" aria-autocomplete="list"
 placeholder="作品名・声優名・キャラクター名（かな・ローマ字も可）" style="min-height:48px">
<ul class="suggest" id="q-sug" role="listbox" aria-label="入力候補" data-suggest hidden></ul>
</div>
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
<h2 class="col-h" style="font-size:20px">データの持ち方</h2>
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


# ---------------------------------------------------------------- キャラクターページ

# プロフィール表に出す属性のカテゴリ。全部出すと1人90個になって表にならない
PROFILE_CATS = ["役柄", "性格", "境遇", "行動", "持ち物", "外見", "髪", "瞳", "服装"]


# キャラクター紹介の公式Xポスト。corrections/x_posts.tsv に確定分がある。
# data/ は再生成で消えるので、収集結果は git 管理下に置いてここで読む。
X_EMBEDS = os.path.join(ROOT, "corrections", "x_embeds.json")


def load_x_posts():
    """cid → 埋め込みHTML。corrections/x_embeds.json を読むだけ。

    このファイルは x_posts.py embeds が書く。埋め込みHTMLは収集時に
    oEmbed から取ったものだが、data/ は .gitignore されていて Actions の
    runner には無い。ワークフローは runner 上で docs/v2 を作り直すので、
    キャッシュだけに頼るとビルドで埋め込みが全部消える。git 管理下に持つ。

    ビルド中にXを叩く手もあるが、生成のたびに数百リクエストが飛ぶうえ、
    Xが落ちているとサイトが作れなくなる。
    """
    if not os.path.exists(X_EMBEDS):
        return {}
    try:
        with open(X_EMBEDS, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {cid: v for cid, v in data.items() if v.get("html")}


def character_page(ch, works, traits, same_cv, by_vid, xpost=None):
    """キャラクター1人のページ。

    画像について:
      公開モードではキャラクター画像を出せない（VNDBの画像は第三者への利用許諾では
      ないため）。代わりに代表作の表紙を置き、「どの作品の人か」を先に伝える。
      表紙はアフィリエイトAPIが返すURLなので出してよい。
    """
    name = ch["name"]
    sub = " / ".join(x for x in [ch["name_latin"],
                                 ("CV. %s" % ch["cv"]) if ch["cv"] else ""] if x)

    chips = ['<span class="tag tag-accent">%s</span>' % e(ch["role_label"])]
    if ch["n_work"] > 1:
        chips.append('<span class="tag tag-neutral">%d作品に登場</span>' % ch["n_work"])
    if ch["age"]:
        chips.append('<span class="tag tag-outline">%d歳</span>' % ch["age"])
    if ch["height"]:
        chips.append('<span class="tag tag-outline">%dcm</span>' % ch["height"])

    cv_cell = ('<a href="%s">%s</a>' % (e(ch["cv_url"]), e(ch["cv"]))) if ch["cv_url"] \
        else (e(ch["cv"]) if ch["cv"] else "未収録")
    facts = [("声優", cv_cell),
             ("役割", e(ch["role_label"])),
             ("誕生日", e(ch["birthday"] or "")),
             ("年齢", "%d歳" % ch["age"] if ch["age"] else ""),
             ("身長", "%dcm" % ch["height"] if ch["height"] else ""),
             ("体重", "%dkg" % ch["weight"] if ch["weight"] else ""),
             ("血液型", e(ch["blood"] or "")),
             ("VNDB", '<a href="https://vndb.org/%s" rel="noopener">%s</a>'
              % (e(ch["cid"]), e(ch["cid"])))]
    facts_html = "".join("<dt>%s</dt><dd>%s</dd>" % (e(k), v) for k, v in facts if v)

    # 公式ポストがあるなら、パッケージ写真の枠ごと差し替える。
    # このページはVNDBのライセンス上キャラクター画像を出せず、代わりに代表作の
    # パッケージと「画像は掲載していません」という断りを置いていた場所。
    # 権利者自身が公開したイラスト付きの投稿が入るなら、そちらのほうがよい。
    # 見出しは付けない。枠の中身が変わるだけに見せる。
    hero_cls = "hero hero-2"
    if xpost:
        hero_cls += " hero-x"
        cover = ('%s<p class="text-muted hero-cap">%s ／ <a href="%s">%s</a></p>'
                 % (xpost["html"], e(xpost["account"]),
                    e(ch["main_url"] or "/"), e(ch["main_title"] or "")))
    elif ch["main_vid"] in by_vid:
        cover = hero_cover(by_vid[ch["main_vid"]], "%s のパッケージ" % ch["main_title"])
    else:
        cover = '<div class="ph" style="aspect-ratio:3/4"><span>画像なし</span></div>'

    cap = "" if xpost else ("""
<p class="text-muted" style="font-size:12px;margin-top:10px">代表作
<a href="%s">%s</a> のパッケージ。キャラクター画像は掲載していません。</p>"""
        % (e(ch["main_url"] or "/"), e(ch["main_title"] or "")))
    hero = """<div class="%(hcls)s">
<div class="hero-img">%(cover)s%(cap)s</div>
<div class="hero-main">
<div class="kicker">%(role)s</div>
<h1>%(name)s</h1>
<div class="hero-sub">%(sub)s</div>
<div class="tags" style="margin-bottom:20px">%(chips)s</div>
<dl class="facts">%(facts)s</dl>
</div>
</div>""" % dict(cover=cover, cap=cap, hcls=hero_cls,
                 role=e(ch["role_label"]), name=e(name), sub=e(sub),
                 chips="".join(chips), facts=facts_html)

    parts = [hero]

    # 属性。カテゴリごとにまとめると「性格はこう、見た目はこう」と読める
    by_cat = defaultdict(list)
    for t in traits:
        by_cat[t["cat"]].append(t)
    cat_html = ""
    for cat in PROFILE_CATS:
        if not by_cat.get(cat):
            continue
        chips_ = "".join(
            ('<a class="tag tag-outline" href="%s">%s</a>' % (e(t["url"]), e(t["trait"])))
            if t["url"] else ('<span class="tag tag-neutral">%s</span>' % e(t["trait"]))
            for t in by_cat[cat])
        cat_html += ('<div class="trait-row"><span class="trait-cat">%s</span>'
                     '<span class="tags">%s</span></div>' % (e(cat), chips_))
    if cat_html:
        parts.append("""<section class="pad sec">
<div class="sec-head" style="margin-top:0"><h2>属性</h2>
<span class="text-muted" style="font-size:12px">押すと同じ属性の作品を探せます</span></div>
<div style="margin-top:14px">%s</div></section>""" % cat_html)

    # 登場作品
    ws = [by_vid[w["vid"]] for w in works if w["vid"] in by_vid]
    parts.append("""<section class="pad sec">
<div class="sec-head" style="margin-top:0"><h2>登場作品</h2>
<span class="text-muted" style="font-size:13px">%d作品</span></div>
<div class="grid">%s</div></section>""" % (len(ws), "".join(work_card(w) for w in ws)))

    # 同じ声優が演じたキャラ
    if same_cv:
        rows = "".join(
            '<a class="idx-name" href="%s"><b>%s</b>'
            '<span class="n">%s</span></a>' % (e(c["url"]), e(c["name"]), e(c["main_title"]))
            for c in same_cv[:24])
        parts.append("""<section class="pad sec-top">
<div class="sec-head" style="margin-top:0"><h2>%(cv)s が演じた他のキャラクター</h2>
<a href="%(cvurl)s">%(cv)s の担当作品</a></div>
<div class="idx-cols" style="margin-top:14px">%(rows)s</div></section>"""
                     % dict(cv=e(ch["cv"]), cvurl=e(ch["cv_url"] or "/cv/"), rows=rows))

    desc = "%s（%s）の声優・属性・登場作品。%s" % (
        name, ch["main_title"] or "", ("CV. %s。" % ch["cv"]) if ch["cv"] else "")
    # 埋め込みがあるページだけ widgets.js を積む。全ページに置くと、
    # ポストが無いキャラのページからも X にリクエストが飛ぶ
    xjs = ('\n<script async src="https://platform.twitter.com/widgets.js" '
           'charset="utf-8"></script>') if xpost else ""
    return layout("%s | %s" % (name, SITE_NAME), desc, ch["url"], "".join(parts),
                  crumbs=[(SITE_NAME, "/"), ("キャラクターから探す", "/character/"),
                          (ch["main_title"] or "", ch["main_url"]), (name, None)],
                  current="作品を探す", extra_js=xjs)


def character_index_page(rows_by_id, total):
    """/character/ — 3,709人を1ページに並べると重いので、五十音の行ごとに分ける"""
    cards = "".join(
        '<a class="cat-card" href="/character/%s/"><b>%s</b>'
        '<div class="n">%s人</div></a>' % (rid, e(rname), format(len(rs), ","))
        for rid, rname, rs in rows_by_id if rs)
    body = """<div class="pad">
<div class="home-head"><div><h1>キャラクターから探す</h1>
<p class="home-lead">掲載作品の主人公と攻略対象を、名前の五十音から辿れます。
同じ人物が続編にも出ている場合は1ページにまとめています。</p></div>
<span class="text-muted" style="font-size:13px">%(n)s人</span></div>
<div class="grid" style="margin-top:18px">%(cards)s</div>
<p class="text-muted" style="font-size:13px;margin-top:20px">
名前が分かっているときは、<a href="/#search">トップの検索</a>にキャラクター名を入れると候補が出ます。</p>
</div>""" % dict(n=format(total, ","), cards=cards)
    return layout("キャラクターから探す | " + SITE_NAME,
                  "掲載作品の主人公・攻略対象%d人を五十音から探せます。" % total,
                  "/character/", body,
                  crumbs=[(SITE_NAME, "/"), ("キャラクターから探す", None)])


def character_row_page(rid, rname, chs):
    """/character/a/ — その行のキャラを読み順に並べる"""
    rows = "".join(
        '<a class="idx-name" href="%s" data-k="%s"><b>%s</b>'
        '<span class="n">%s</span></a>'
        % (e(c["url"]), e(c["kana_key"]), e(c["name"]),
           e(("CV. %s" % c["cv"]) if c["cv"] else c["main_title"] or ""))
        for c in chs)
    body = """<div class="pad">
<div class="home-head"><div><h1>%(r)s のキャラクター</h1></div>
<span class="text-muted" style="font-size:13px">%(n)s人</span></div>
<label class="sr-only" for="cq">名前で絞り込む</label>
<input class="input" id="cq" type="search" placeholder="名前で絞り込む（かな・ローマ字も可）"
 style="margin-top:18px;max-width:420px" data-list-filter>
<div class="idx-cols" style="margin-top:18px" data-list-grid>%(rows)s</div>
<p class="empty" data-list-empty hidden>該当する項目がありません。</p>
</div>""" % dict(r=e(rname), n=format(len(chs), ","), rows=rows)
    return layout("%s のキャラクター | %s" % (rname, SITE_NAME),
                  "名前が%sで始まるキャラクター%d人。" % (rname, len(chs)),
                  "/character/%s/" % rid, body,
                  crumbs=[(SITE_NAME, "/"), ("キャラクターから探す", "/character/"), (rname, None)])


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


# 五十音の行で分けるのは人名・作品名のように「読み」がある索引だけ。
# タグと属性のスラッグは英語（/tag/otome-game/）なので、頭文字から行を決めると
# でたらめな行に入る。こちらは件数順のままにする。
KANA_KINDS = {"cv", "staff", "maker", "publisher", "series"}


def kana_key_of(c):
    """五十音の行と並び順を決める読み。

    表示名そのもののローマ字（catalog.reading）を最優先にする。スラッグから
    取ると、別名義をまとめている声優ページで行を間違える
    （長谷川 育美 → /cv/akabane-kyouko-s14378/ → あ行に入ってしまった）。
    読みが無いメーカー・発売元はスラッグに戻す。
    """
    return re.sub(r"[^a-z]", "", (c["reading"] or "").lower()) or slug_key(c["url"])


def name_rows(entries, key_of):
    return "".join(
        '<a class="idx-name" href="%s" data-k="%s"><b>%s</b>'
        '<span class="n">%s作品</span></a>'
        % (e(c["url"]), e(key_of(c)), e(c["label"]), format(c["n_works"], ","))
        for c in entries)


def kana_sections(entries, key_of, id_prefix="r"):
    """五十音の行ごとの節と、その先頭に置く行ナビを返す。

    行内はローマ字読み順（ABC順ではない。ABC順だと「あ え い お う」になる）。
    """
    by = defaultdict(list)
    for c in entries:
        by[kana_row(key_of(c))].append(c)
    jump, secs = [], []
    for rid, rname in KANA_ROWS:
        rows = sorted(by.get(rid, []),
                      key=lambda c: (kana_sort_key(key_of(c)), c["label"]))
        if not rows:
            continue
        jump.append('<a href="#%s-%s">%s<span>%d</span></a>' % (id_prefix, rid, e(rname), len(rows)))
        secs.append('<section class="idx-sec" id="%s-%s"><h2>%s'
                    '<span class="idx-n">%d</span></h2>'
                    '<div class="idx-cols">%s</div></section>'
                    % (id_prefix, rid, e(rname), len(rows), name_rows(rows, key_of)))
    nav = ('<nav class="kana-nav" aria-label="五十音で移動">%s</nav>' % "".join(jump)) if jump else ""
    return nav, "".join(secs)


def catalog_index_page(kind, label, note, entries):
    """/cv/ /tag/ のような「索引の索引」。名前で絞り込め、読みのあるものは五十音で辿れる"""
    key_of = kana_key_of
    if kind in KANA_KINDS:
        nav, sections = kana_sections(entries, key_of)
        listing = nav + '<div data-list-grid>' + sections + '</div>'
    else:
        listing = ('<div class="idx-cols" style="margin-top:18px" data-list-grid>%s</div>'
                   % name_rows(entries, key_of))
    body = """<div class="pad">
<div class="home-head"><div><h1>%(lab)sから探す</h1><p class="home-lead">%(note)s</p></div>
<span class="text-muted" style="font-size:13px">%(n)s件</span></div>
<label class="sr-only" for="cq">名前で絞り込む</label>
<input class="input" id="cq" type="search" placeholder="名前で絞り込む（かな・ローマ字も可）"
 style="margin-top:18px;max-width:420px" data-list-filter>
%(listing)s
<p class="empty" data-list-empty hidden>該当する項目がありません。</p>
</div>""" % dict(lab=e(label), note=e(note), n=format(len(entries), ","), listing=listing)
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

    characters = list(con.execute("SELECT * FROM character"))
    ch_url = {c["cid"]: c["url"] for c in characters}
    ch_works = defaultdict(list)
    for r in con.execute("SELECT * FROM character_work ORDER BY cid, sort DESC"):
        ch_works[r["cid"]].append(r)
    ch_traits = defaultdict(list)
    for r in con.execute("SELECT * FROM character_trait"):
        if r["trait"] in ok_trait:      # 訳のない英語の属性は出さない（他ページと同じ規則）
            ch_traits[r["cid"]].append(r)
    by_cv = defaultdict(list)
    for c in characters:
        if c["cv"]:
            by_cv[c["cv"]].append(c)

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
                          eds[w["vid"]], offers, meta, ch_url)
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

    # ---- キャラクターページ ----
    xposts = load_x_posts()
    if xposts:
        print("  公式Xポストの埋め込み: %d人" % len(xposts))
    for c in characters:
        same = [x for x in by_cv.get(c["cv"] or "", []) if x["cid"] != c["cid"]]
        same.sort(key=lambda x: (x["main_released"] or "0000"), reverse=True)
        write(c["url"], character_page(c, ch_works[c["cid"]], ch_traits[c["cid"]],
                                       same, by_vid, xposts.get(c["cid"])))
        urls.append(c["url"])
    rows_by_id = []
    for rid, rname in KANA_ROWS:
        rs = sorted([c for c in characters if kana_row(c["kana_key"]) == rid],
                    key=lambda c: (kana_sort_key(c["kana_key"]), c["name"] or ""))
        rows_by_id.append((rid, rname, rs))
        if rs:
            write("/character/%s/" % rid, character_row_page(rid, rname, rs))
            urls.append("/character/%s/" % rid)
    write("/character/", character_index_page(rows_by_id, len(characters)))
    urls.append("/character/")
    print("  キャラクターページ %d + 五十音 %d行"
          % (len(characters), sum(1 for _, _, rs in rows_by_id if rs)))

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

    # 入力候補。1行 = 1つの行き先。
    #
    #   t 種別 / n 表示名 / k 照合用のローマ字（正規化済み）/ u 行き先
    #   r 補足（候補の2行目）/ v この語で絞り込んだときに残る作品の番号（index.json の位置）
    #
    # 照合用のキーをここで作っておく。5,786件を1打鍵ごとに正規化し直すと
    # 入力が引っかかるため、正規化はビルド時に済ませる。
    pos = {w["vid"]: i for i, w in enumerate(works)}
    norm = lambda t: re.sub(r"[\s　・:：\-—―ー~〜!！?？'\"’”.,()（）\[\]]", "",
                            (t or "").lower())

    sug = []
    for w in works:
        sug.append({"t": "作品", "n": w["title"], "k": norm(w["title_latin"]),
                    "u": w["url"], "v": [pos[w["vid"]]],
                    "r": " / ".join(x for x in [w["released_ja"], w["platform_top"]] if x)})

    cv_works, cv_url = defaultdict(set), {}
    for c in cat.get("cv", []):
        cv_url[c["label"]] = c["url"]
    for w in works:
        for c in chars[w["vid"]]:
            if c["cv"]:
                cv_works[c["cv"]].add(pos[w["vid"]])
    for name, vs in cv_works.items():
        sug.append({"t": "声優", "n": name, "k": norm(slug_key(cv_url[name]))
                    if name in cv_url else "", "u": cv_url.get(name),
                    "v": sorted(vs), "r": "%d作品" % len(vs)})

    ch_works = defaultdict(set)
    for w in works:
        for c in chars[w["vid"]]:
            if c["role"] in ("主人公", "攻略対象"):
                ch_works[c["cid"]].add(pos[w["vid"]])
    for c in con.execute("SELECT * FROM character"):
        sug.append({"t": "キャラ", "n": c["name"], "k": norm(c["name_latin"]),
                    "u": c["url"], "v": sorted(ch_works.get(c["cid"], [])),
                    "r": " / ".join(x for x in [("CV. %s" % c["cv"]) if c["cv"] else "",
                                                 c["main_title"] or ""] if x)})

    st_works, st_url = defaultdict(set), {}
    for c in cat.get("staff", []):
        st_url[c["label"]] = c["url"]
    for r in con.execute("SELECT vid, name FROM work_staff"):
        if r["name"] in st_url and r["vid"] in pos:
            st_works[r["name"]].add(pos[r["vid"]])
    for name, vs in st_works.items():
        sug.append({"t": "スタッフ", "n": name, "k": norm(slug_key(st_url[name]))
                    if name in st_url else "", "u": st_url.get(name),
                    "v": sorted(vs), "r": "%d作品" % len(vs)})

    sug = [x for x in sug if x["u"]]
    json.dump(sug, open(os.path.join(out, "suggest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print("  入力候補 %d件（作品%d/声優%d/キャラ%d/スタッフ%d）"
          % (len(sug), len(works), len(cv_works), len(ch_works), len(st_works)))

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
