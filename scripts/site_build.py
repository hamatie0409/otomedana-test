"""【フェーズ3】静的サイトを生成する。3,627ページ + sitemap + robots。

  python3 scripts/site_build.py

各ページはJSなしで読める実体のHTML。絞り込みだけをJSで足す。
"""
import os, re, json, html, shutil, sqlite3, datetime
from collections import defaultdict
from common import DATA, ROOT

from series import build_series
from site_config import (SITE_NAME, SITE_DESC, SITE_URL, REPO_URL, SCOPE_NOTE,
                         PUBLISH, BASE_PATH, IMAGE_MODE,
                         AGE_TIERS, age_tier, year_bucket, year_label, year_sort)

# GitHub Pages は リポジトリ直下 か docs/ からしか配信できないため docs/ に出す
OUT = os.path.join(ROOT, "docs")
BASE_URL = SITE_URL + BASE_PATH
HOME = ("Switch", "PS", "ニンテンドー", "Xbox")

e = lambda s: html.escape(str(s), quote=True) if s is not None else ""

# 下層ページの導線。索引ページ（/cv/ /maker/ …）への入口を常設する。
# ここに無いと索引ページを作っても誰も辿り着けないので layout() に組み込む。
# トップには出さない。同じ入口をカードで大きく並べている上、
# ブランド名も見出しも「トップへ戻る」も、そのページ自身を指すことになるため。
NAV = '<nav class="site-nav" aria-label="サイト内の移動">%s</nav>' % "".join(
    '<a href="%s">%s</a>' % (u, t) for t, u in
    [("トップ", "/"), ("声優", "/cv/"), ("メーカー", "/maker/"),
     ("シリーズ", "/series/"), ("タグ", "/tag/"), ("キャラ属性", "/trait/")])

# 訳が用意できていない英語のままの語は表示しない（サイトは日本語で統一する）
is_en = lambda t: bool(re.fullmatch(r"[\x20-\x7e]+", t or ""))
ja_only = lambda xs: [x for x in xs if x and not is_en(x)]


# ---------------------------------------------------------------- テンプレート

def layout(title, desc, canonical, body, jsonld=None, breadcrumb=None, og_image=None,
           home=False):
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
%(header)s%(crumb)s
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
                  header="" if home else
                  ('<header class="site">\n'
                   '  <a class="brand" href="/">%s</a>\n'
                   '</header>\n%s\n' % (e(SITE_NAME), NAV)),
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
    return re.sub(r'(href|src|action)="/', r'\1="%s/' % BASE_PATH, content)


def write(path, content):
    full = os.path.join(OUT, path.strip("/"), "index.html") if path != "/" \
        else os.path.join(OUT, "index.html")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    open(full, "w", encoding="utf-8").write(with_base(content))


def card_attrs(g):
    """カードに絞り込み・並べ替え用のデータを添える（app.js が読む）"""
    return (' data-r="%s" data-p="%s" data-a="%s" data-y="%s" data-g="%s" data-n="%s"'
            % (e(g["released"] or ""), e(g["platforms"] or ""),
               age_tier(g["minage"]), year_bucket(g["released"]),
               g["rating"] or "", g["votecount"] or ""))


def card_list(items):
    """作品カードの一覧（一覧系ページ共通）。

    7要素目にデータ属性を渡すと、そのカードは並べ替え・絞り込みの対象になる。"""
    out = ['<ul class="cards">']
    for it in items:
        url, title, released, plat, img, extra = it[:6]
        attrs = it[6] if len(it) > 6 else ""
        thumb = '<img src="%s" alt="" loading="lazy" width="90">' % e(img) if img else \
                '<span class="noimg"></span>'
        out.append(
            '<li%s><a href="%s">%s<span class="meta"><b>%s</b>'
            '<small>%s</small><small>%s</small>%s</span></a></li>'
            % (attrs, e(url), thumb, e(title), e(released or ""), e(plat or ""),
               ('<small class="ex">%s</small>' % e(extra)) if extra else ""))
    out.append("</ul>")
    return "\n".join(out)


def list_tools(gs):
    """カード一覧の上に置く絞り込み・並べ替え。

    選択肢はそのページに実際にある値だけを出す（空振りする項目を並べない）。
    JSが無い環境では何も起きないが、一覧自体は実体のHTMLなので読める。"""
    # 同じ作品が複数行に出ることがある（1作品で2役の声優、同じ属性のキャラが複数）。
    # 選択肢に添える数は作品数なので、ここで作品単位に均す
    uniq = {g["vid"]: g for g in gs}.values()
    plat_n, age_n, year_n = defaultdict(int), defaultdict(int), defaultdict(int)
    for g in uniq:
        for pl in (g["platforms"] or "").split(" / "):
            if pl.strip():
                plat_n[pl.strip()] += 1
        age_n[age_tier(g["minage"])] += 1
        year_n[year_bucket(g["released"])] += 1

    def sel(name, label, opts):
        if len(opts) < 2:
            return ""      # 選ぶ余地が無いなら出さない
        o = "".join('<option value="%s">%s</option>' % (e(v), e(t)) for v, t in opts)
        return ('<select data-f="%s" aria-label="%s">'
                '<option value="">%s：すべて</option>%s</select>'
                % (name, e(label), e(label), o))

    parts = [
        sel("plat", "機種",
            [(k, "%s（%d）" % (k, v)) for k, v in
             sorted(plat_n.items(), key=lambda kv: (-kv[1], kv[0]))]),
        sel("year", "発売年",
            [(b, year_label(b)) for b in
             sorted((k for k in year_n if k), key=year_sort, reverse=True)]),
        sel("age", "対象年齢",
            [(k, lab) for k, lab, _lo, _hi in AGE_TIERS if age_n.get(k)]),
        ('<select data-f="sort" aria-label="並び順">%s</select>' % "".join(
            '<option value="%s">%s</option>' % (v, t) for v, t in
            [("new", "発売日が新しい順"), ("old", "発売日が古い順"),
             ("rate", "評価が高い順"), ("pop", "票数が多い順")])),
    ]
    parts = [x for x in parts if x]
    # 絞る余地が無く、件数も少ないなら道具を置かない（並べ替えだけでは意味が薄い）
    if len(parts) < 2 and len(uniq) < 6:
        return ""
    return ('<div class="list-tools" data-list-tools>%s'
            '<span class="list-count" aria-live="polite"></span>'
            '<button type="button" class="list-reset">条件をクリア</button></div>'
            % "".join(parts))


# ---------------------------------------------------------------- 索引ページ

# カテゴリ索引の定義。この並びがそのままトップの「カテゴリから探す」の並びになる。
#   (kind, URL, 短い名前, 見せ方, 見出し, 説明(件数を1つ埋める), トップでの一言)
CATS = [
    ("cv", "/cv/", "声優", "kana", "声優から探す",
     "出演作のある声優 %d人の一覧。五十音の行から辿るか、絞り込み欄に名前を入れて探せます。",
     "名前の五十音から、担当作の一覧へ。"),
    ("maker", "/maker/", "メーカー", "cards", "メーカーから探す",
     "乙女ゲームを開発したメーカー %d社の一覧。作品数の多い順に並べています。",
     "オトメイト・クインロゼなど、開発元ごとの作品一覧へ。"),
    ("series", "/series/", "シリーズ", "cards", "シリーズから探す",
     "続編・ファンディスクのあるシリーズ %d件の一覧。各ページで発売順（＝遊ぶ順）に並べています。",
     "どれから遊べばよいかを発売順で確認。"),
    ("publisher", "/publisher/", "発売元", "cards", "発売元から探す",
     "乙女ゲームを発売したブランド・パブリッシャー %d社の一覧。",
     "販売しているブランドごとの作品一覧へ。"),
    ("staff", "/staff/", "スタッフ", "byrole", "スタッフから探す",
     "乙女ゲームに参加したスタッフ %d人の一覧。担当した役割ごとにまとめています。"
     "複数の役割を持つ人は、それぞれの役割に出てきます。",
     "シナリオ・原画・音楽など、役割ごとに辿る。"),
    ("tag", "/tag/", "タグ", "chips", "タグから探す",
     "作品の傾向をあらわすタグ %d件の一覧。該当作品の多い順に並べています。",
     "ジャンル・題材・システムから絞り込む。"),
    ("trait", "/trait/", "キャラ属性", "bycat", "キャラ属性から探す",
     "キャラクターの性格・外見・役柄などの属性 %d件の一覧。分類ごとにまとめています。",
     "「ツンデレ」「幼なじみ」など、好みの属性から。"),
    ("platform", "/platform/", "機種", "chips", "機種から探す",
     "収録作品が遊べる機種 %d件の一覧。",
     "Switch・PS・PSP など、持っている機種から。"),
]
CAT_OF = {c[0]: c for c in CATS}

# スタッフ索引に出す役割と、その並び順。作品づくりの流れに沿って並べる。
# ここに無い役割（QA・翻訳・編集）はDBには持つが索引ページには出さない。
STAFF_ROLES = ["シナリオ", "原画", "キャラクターデザイン", "監督", "主題歌", "音楽"]

# 五十音の行。読みはスラッグ（ヘボン式ローマ字）の頭文字から機械的に決める。
# 「鳥海 浩輔 → toriumi-… → た行」のように、漢字表記でも行が引ける。
KANA_ROWS = [("a", "あ行"), ("ka", "か行"), ("sa", "さ行"), ("ta", "た行"),
             ("na", "な行"), ("ha", "は行"), ("ma", "ま行"), ("ya", "や行"),
             ("ra", "ら行"), ("wa", "わ行"), ("etc", "数字・記号")]
_ROW_OF = {}
for _cs, _r in [("aiueo", "a"), ("kgq", "ka"), ("szj", "sa"), ("td", "ta"),
                ("n", "na"), ("hfbpv", "ha"), ("m", "ma"), ("y", "ya"),
                ("rl", "ra"), ("w", "wa")]:
    for _c in _cs:
        _ROW_OF[_c] = _r


def kana_row(key):
    """ローマ字スラッグの頭から五十音の行を決める。ch/ts は「ち・つ」なので た行"""
    k = (key or "").lower()
    if k[:2] in ("ch", "ts"):
        return "ta"
    if k[:1] == "c":           # cinderella → シンデレラ
        return "sa"
    return _ROW_OF.get(k[:1], "etc")


# 五十音順の並び替え。スラッグ（ヘボン式ローマ字）を音節に切り、
# 五十音での位置に置き換えた数列どうしを比べる。
# ローマ字のABC順とは違う（ABC順だと「あ え い お う」の順に並んでしまう）。
_KANA_ORDER = [
    "a", "i", "u", "e", "o",
    "ka", "ga", "ki", "gi", "ku", "gu", "ke", "ge", "ko", "go",
    "sa", "za", "shi", "ji", "su", "zu", "se", "ze", "so", "zo",
    "ta", "da", "chi", "di", "tsu", "du", "te", "de", "to", "do",
    "na", "ni", "nu", "ne", "no",
    "ha", "ba", "pa", "hi", "bi", "pi", "fu", "hu", "bu", "pu",
    "he", "be", "pe", "ho", "bo", "po",
    "ma", "mi", "mu", "me", "mo",
    "ya", "yu", "yo",
    "ra", "ri", "ru", "re", "ro",
    "wa", "wo", "n",
]
_KANA_AT = {syl: i for i, syl in enumerate(_KANA_ORDER)}

# 拗音は「い段＋や行」に開いて比べる（きゃ → き・や）
_YOUON = {}
for _c, _i in [("ky", "ki"), ("gy", "gi"), ("sh", "shi"), ("j", "ji"), ("ch", "chi"),
               ("ny", "ni"), ("hy", "hi"), ("by", "bi"), ("py", "pi"),
               ("my", "mi"), ("ry", "ri")]:
    for _v, _y in [("a", "ya"), ("u", "yu"), ("o", "yo")]:
        _YOUON.setdefault(_c + _v, (_i, _y))

# 長いものから順に当てる（"na" を "n" より先に見る）
_SYLLABLES = sorted(set(list(_YOUON) + list(_KANA_AT)), key=len, reverse=True)


def kana_sort_key(key):
    """ローマ字スラッグ → 五十音での並び順を表す数列"""
    t = re.sub(r"[^a-z]", "", (key or "").lower())
    out, i = [], 0
    while i < len(t):
        # 促音（同じ子音が続く）は読みに影響しないので飛ばす
        if i + 1 < len(t) and t[i] == t[i + 1] and t[i] not in "aiueon":
            i += 1
            continue
        for syl in _SYLLABLES:
            if t.startswith(syl, i):
                if syl in _YOUON:
                    out.extend(_KANA_AT[x] for x in _YOUON[syl])
                else:
                    out.append(_KANA_AT[syl])
                i += len(syl)
                break
        else:
            out.append(900 + ord(t[i]))   # ローマ字として読めない字は最後に回す
            i += 1
    return out


def slug_key(url):
    """/cv/toriumi-kousuke-s45/ → toriumi-kousuke（絞り込みの照合と行分けに使う）"""
    return re.sub(r"-[sv]\d+$", "", url.strip("/").split("/")[-1])


def idx_cards(entries):
    """代表作のカバー付きカード（メーカー・発売元・シリーズ）"""
    out = ['<ul class="cards idx-cards">']
    for it in entries:
        thumb = ('<img src="%s" alt="" loading="lazy" width="90">' % e(it["img"])
                 if it.get("img") else '<span class="noimg"></span>')
        rep_ = ('<small>%s</small>' % e(it["rep"])) if it.get("rep") else ""
        out.append('<li data-k="%s"><a href="%s">%s<span class="meta"><b>%s</b>'
                   '<small>%d作品</small>%s</span></a></li>'
                   % (e(it["k"]), e(it["url"]), thumb, e(it["label"]), it["n"], rep_))
    out.append("</ul>")
    return "\n".join(out)


def idx_names(entries, show_n=True):
    """名前だけを詰めて並べる（声優・スタッフ）。

    show_n=False では作品数を出さない。数字は名前を追うときの邪魔になるので、
    一覧では名前だけを見せ、件数はその人のページで確かめてもらう。"""
    out = ['<ul class="idx-names">']
    for it in entries:
        sub = ('<i>%s</i>' % e(it["sub"])) if it.get("sub") else ""
        n = ('<small>%d</small>' % it["n"]) if show_n else ""
        out.append('<li data-k="%s"><a href="%s"><span>%s</span>%s%s</a></li>'
                   % (e(it["k"]), e(it["url"]), e(it["label"]), sub, n))
    out.append("</ul>")
    return "\n".join(out)


def idx_chips(entries):
    out = ['<ul class="idx-chips">']
    for it in entries:
        out.append('<li data-k="%s"><a href="%s">%s<small>%d</small></a></li>'
                   % (e(it["k"]), e(it["url"]), e(it["label"]), it["n"]))
    out.append("</ul>")
    return "\n".join(out)


def idx_kana(entries):
    """五十音の行ごとの節に分ける。行内はローマ字読み順"""
    by = defaultdict(list)
    for it in entries:
        by[kana_row(it["k"])].append(it)
    jump, secs = [], []
    for rid, rname in KANA_ROWS:
        rows = sorted(by.get(rid, []), key=lambda x: (kana_sort_key(x["k"]), x["label"]))
        if not rows:
            continue
        jump.append('<a href="#r-%s">%s</a>' % (rid, e(rname)))
        secs.append('<section class="idx-sec" id="r-%s" data-sec>'
                    '<h2>%s<span class="idx-n">%d</span></h2>%s</section>'
                    % (rid, e(rname), len(rows), idx_names(rows)))
    return ('<nav class="idx-jump" aria-label="五十音">%s</nav>' % "".join(jump)
            + "\n".join(secs))


def idx_byrole(entries):
    """役割ごとの節に分ける（スタッフ）。

    1人が複数の役割を持つことがあるので、entries を役割の数だけ展開する。
    節の並びは STAFF_ROLES で決め打ちし、節の中は五十音順に並べる。
    参加作品数は一覧には出さない（名前を追う邪魔になるため、人物ページで見せる）。"""
    by = defaultdict(list)
    for it in entries:
        for role, n in sorted((it.get("roles") or {}).items()):
            row = dict(it)
            row["n"] = n
            by[role].append(row)
    jump, secs = [], []
    for i, role in enumerate(STAFF_ROLES, 1):
        if role not in by:
            continue
        rows = sorted(by[role], key=lambda x: (kana_sort_key(x["k"]), x["label"]))
        rid = "role-%d" % i
        jump.append('<a href="#%s">%s</a>' % (rid, e(role)))
        secs.append('<section class="idx-sec" id="%s" data-sec>'
                    '<h2>%s<span class="idx-n">%d人</span></h2>%s</section>'
                    % (rid, e(role), len(rows), idx_names(rows, show_n=False)))
    return ('<nav class="idx-jump" aria-label="役割">%s</nav>' % "".join(jump)
            + "\n".join(secs))


def idx_bycat(entries):
    """分類ごとの節に分ける（キャラ属性）"""
    by = defaultdict(list)
    for it in entries:
        by[it.get("cat") or "その他"].append(it)
    out = []
    for cat in sorted(by, key=lambda c: -len(by[c])):
        rows = sorted(by[cat], key=lambda x: -x["n"])
        out.append('<section class="idx-sec" data-sec>'
                   '<h2>%s<span class="idx-n">%d</span></h2>%s</section>'
                   % (e(cat), len(rows), idx_chips(rows)))
    return "\n".join(out)


def idx_wrap(inner, placeholder, reading=False):
    """索引の本体を、ページ内絞り込み欄つきの箱に入れる（絞り込みは app.js）。

    reading=True は「スラッグが名前の読み（ヘボン式）である」カテゴリ。
    人名やメーカー名はローマ字からかなを起こして照合できるが、
    タグや属性のスラッグは英訳なので、起こしても雑音にしかならない。"""
    return ('<div class="idx" data-idx%s>\n'
            '<div class="idx-tools">'
            '<input type="search" class="idx-filter" autocomplete="off" '
            'placeholder="%s" aria-label="絞り込み">'
            '<span class="idx-count" aria-live="polite"></span></div>\n'
            '%s\n</div>' % (" data-reading" if reading else "", e(placeholder), inner))



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

    ch_by_cid = defaultdict(list)
    for r in con.execute("""SELECT * FROM characters WHERE vid IN (%s)
                            AND role IN ('主人公','攻略対象')""" % ph, vids):
        ch_by_cid[r["cid"]].append(r)

    con.execute("""CREATE TABLE IF NOT EXISTS shop_images
                   (vid TEXT PRIMARY KEY, url TEXT, source TEXT)""")
    shop_img = dict(con.execute("SELECT vid, url FROM shop_images"))

    def cover_of(g):
        """公開モードではアフィリエイト由来の画像だけを使う"""
        if IMAGE_MODE == "vndb":
            return g["image_url"]
        return shop_img.get(g["vid"])

    series = build_series(con, set(games))
    series_of = {}
    for key, v in series.items():
        for m in v["members"]:
            series_of[m] = key

    alias_of = defaultdict(list)
    for a, c in con.execute("SELECT alias,canonical FROM slug_aliases"):
        alias_of[c].append(a)

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    urls = []
    idx = defaultdict(list)   # kind -> 索引ページに載せる項目

    def cat_crumbs(kind, lab):
        """パンくずに「声優」「メーカー」などの層を挟み、索引ページへ戻れるようにする"""
        c = CAT_OF.get(kind)
        return [(SITE_NAME, "/")] + ([(c[2], c[1])] if c else []) + [(lab, None)]

    def rep_game(gs):
        """代表作。カバー画像があるものを優先し、その中で票数の多いもの。
        評価より票数を見るのは「知られている作品」を出したいから"""
        gs = [g for g in gs if g]
        if not gs:
            return None
        return sorted(gs, key=lambda g: (cover_of(g) is not None, g["votecount"] or 0),
                      reverse=True)[0]

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
        cover = cover_of(g)
        if cover:
            b.append('<img class="cover" src="%s" alt="%sのパッケージ" width="220">'
                     % (e(cover), e(g["title"])))
        b.append("<div>")
        b.append("<h1>%s</h1>" % e(g["title"]))
        if g["title_latin"]:
            b.append('<p class="latin">%s</p>' % e(g["title_latin"]))
        rows = [("機種", " / ".join('<a href="%s">%s</a>' % (e(slug[("platform", p)]), e(p))
                                    for p in gplats[vid] if ("platform", p) in slug)),
                ("発売日", e(ja_date(g["released"]) or "—")),
                ("シリーズ", ('<a href="%s">%s</a>' % (
                    e(slug[("series", series_of[vid])]),
                    e(series[series_of[vid]]["name"]))
                    if vid in series_of else "—")),
                ("メーカー", " / ".join(
                    '<a href="%s">%s</a>' % (e(slug[("maker", d)]), e(d))
                    if ("maker", d) in slug and npages[("maker", d)][1] else e(d)
                    for d in (g["developers"] or "").split(" / ") if d)),
                ("発売元", " / ".join(
                    '<a href="%s">%s</a>' % (e(slug[("publisher", d)]), e(d))
                    if ("publisher", d) in slug and npages[("publisher", d)][1] else e(d)
                    for d in (g["publishers"] or "").split(" / ")[:6] if d) or "—"),
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
                cu = slug.get(("character", c["cid"]))
                nm = ('<a href="%s">%s</a>' % (e(cu), e(c["name"]))
                      if cu and c["name"] else e(c["name"] or ""))
                b.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
                         % (e(c["role"] or ""), nm, cv))
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

        tg = [t for t in ja_only(gtags[vid])
              if ("tag", t) in slug and npages[("tag", t)][1]]
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

        offs = [r["url"] for r in con.execute(
            "SELECT DISTINCT url FROM vndb_links WHERE vid=? AND site='website' AND url IS NOT NULL",
            (vid,))]
        live = [u for u in offs if "web.archive.org" not in u]
        arch = [u for u in offs if "web.archive.org" in u]
        if live or arch:
            b.append('<section><h2>公式サイト</h2><ul class="links">')
            for u in live[:4]:
                b.append('<li><a href="%s" rel="noopener" target="_blank">%s</a></li>'
                         % (e(u), e(re.sub(r"^https?://(www\.)?", "", u).rstrip("/")[:60])))
            for u in arch[:2]:
                b.append('<li><a href="%s" rel="noopener" target="_blank">'
                         '公式サイト（保存版・Web Archive）</a></li>' % e(u))
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
        if cover:
            ld["image"] = cover
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
        write(url, layout(title, desc, BASE_URL + url, "\n".join(b), [ld], crumbs, cover))
        urls.append((url, g["released"]))

    # ---------------- 一覧系ページ ----------------
    def listing(kind, heading_fmt, desc_fmt, member_sql, extra_col=None, tools=False):
        made = 0
        for (k, key), (n, is_page) in npages.items():
            if k != kind or not is_page:
                continue
            if kind == "tag" and is_en(label[(k, key)]):
                continue          # 訳が無い語はページを作らない
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
            items, gs = [], []
            for r in rows:
                g = games.get(r["vid"])
                if not g:
                    continue
                items.append((slug[("game", g["vid"])], g["title"], g["released"],
                              (g["platforms"] or "").split(" / ")[0], cover_of(g),
                              r[extra_col] if extra_col and extra_col in r.keys() else None,
                              card_attrs(g)))
                gs.append(g)
            if not items:
                continue
            items.sort(key=lambda x: (x[2] or "0000"), reverse=True)
            body = ["<h1>%s</h1>" % e(heading_fmt % lab),
                    '<p class="lead">%s</p>' % e(desc_fmt % (lab, len(items)))]
            if kind == "cv" and alias_of.get(key):
                body.append('<p class="alias">別名義: %s</p>' % e("、".join(alias_of[key])))
            if tools:
                body.append('<div data-list>')
                body.append(list_tools(gs))
            body.append(card_list(items))
            if tools:
                body.append('</div>')
            ld = {"@context": "https://schema.org", "@type": "ItemList",
                  "name": heading_fmt % lab,
                  "numberOfItems": len(items),
                  "itemListElement": [
                      {"@type": "ListItem", "position": i + 1,
                       "url": BASE_URL + it[0], "name": it[1]}
                      for i, it in enumerate(items[:50])]}
            write(url, layout("%s｜%s" % (heading_fmt % lab, SITE_NAME),
                              desc_fmt % (lab, len(items)), BASE_URL + url,
                              "\n".join(body), [ld], cat_crumbs(kind, lab)))
            urls.append((url, None))
            rg = rep_game([games.get(r["vid"]) for r in rows])
            idx[kind].append(dict(url=url, label=lab, n=len(items), k=slug_key(url),
                                  img=cover_of(rg) if rg else None,
                                  rep=("代表作: %s" % rg["title"]) if rg else None))
            made += 1
        return made

    def person_page(kind, key, url, lab, n):
        """1人物1ページ。出演（声優）とスタッフ参加の両方を載せる"""
        sid = key if kind == "staff" else sid_of_cv.get(key)
        body = []
        cast_rows, staff_rows = [], []      # (作品, 補足) の組。並べ替えは発売日(ISO)で行う

        if kind == "cv":
            keys = [key] + alias_of.get(key, [])
            for r in con.execute(
                    "SELECT DISTINCT vid, name FROM characters WHERE cv IN (%s)"
                    % ",".join("?" * len(keys)), keys):
                g = games.get(r["vid"])
                if g:
                    cast_rows.append((g, r["name"]))
        if sid:
            seen = {}
            for c in sc_by_sid.get(sid, []):
                g = games.get(c["vid"])
                if not g:
                    continue
                seen.setdefault(c["vid"], []).append(c["role"])
            for v2, roles in seen.items():
                staff_rows.append((games[v2], "・".join(sorted(set(roles)))))

        # 表示は「2011年8月18日」形式だが、並べ替えはISO日付で行う。
        # 和暦表記の文字列で並べると 8月 が 12月 より後になってしまう
        cast_rows.sort(key=lambda x: (x[0]["released"] or ""), reverse=True)
        staff_rows.sort(key=lambda x: (x[0]["released"] or ""), reverse=True)

        def item_of(g, extra):
            return (slug[("game", g["vid"])], g["title"], ja_date(g["released"]),
                    (g["platforms"] or "").split(" / ")[0], cover_of(g), extra,
                    card_attrs(g))

        cast_items = [item_of(g, x) for g, x in cast_rows]
        staff_items = [item_of(g, x) for g, x in staff_rows]
        # cast_items は「作品×キャラ」の行。同じ作品で2役演じていれば2行になるので、
        # 作品数として数えるときは作品URLで重複を落とす
        cast_works = {i[0] for i in cast_items}
        total = len(cast_works | {i[0] for i in staff_items})
        if not total:
            return False

        roles_txt = "・".join(sorted({c["role"] for c in sc_by_sid.get(sid, [])})) if sid else ""
        if kind == "cv":
            head = "%s が出演する乙女ゲーム" % lab
            # 2役以上を演じている作品があるときだけ「（%d役）」を添える
            n_role = ("（%d役）" % len(cast_items)) if len(cast_items) > len(cast_works) else ""
            desc = ("%s が声を担当した乙女ゲーム %d作品%sの一覧。"
                    "キャラクター名・発売日・機種と、買えるお店へのリンクをまとめています。"
                    % (lab, len(cast_works), n_role))
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
        body.append('<div data-list>')
        body.append(list_tools([g for g, _ in cast_rows] + [g for g, _ in staff_rows]))
        if cast_items:
            if staff_items:
                body.append("<h2>出演</h2>")
            body.append(card_list(cast_items))
        if staff_items:
            body.append("<h2>スタッフとしての参加</h2>")
            body.append(card_list(staff_items))
        body.append('</div>')

        ld = {"@context": "https://schema.org", "@type": "ItemList", "name": head,
              "numberOfItems": len(cast_items) + len(staff_items),
              "itemListElement": [{"@type": "ListItem", "position": i + 1,
                                   "url": BASE_URL + it[0], "name": it[1]}
                                  for i, it in enumerate((cast_items + staff_items)[:50])]}
        write(url, layout("%s｜%s" % (head, SITE_NAME), desc, BASE_URL + url,
                          "\n".join(body), [ld], cat_crumbs(kind, lab)))
        urls.append((url, None))
        # 索引に出す数は、声優なら出演作品数（スタッフ参加は混ぜない）。
        # 「声優から探す」の一覧に主題歌などの参加数まで足すと意味がずれる
        ent = dict(url=url, label=lab, k=slug_key(url),
                   n=(len(cast_works) or total) if kind == "cv" else total)
        if kind == "staff":
            # 役割ごとの参加作品数。索引ではこちらを件数として出す
            by_role = defaultdict(set)
            for c in (sc_by_sid.get(sid, []) if sid else []):
                if c["role"] in STAFF_ROLES:
                    by_role[c["role"]].add(c["vid"])
            if not by_role:
                return True      # QA・翻訳・編集だけの人はページは作るが索引には出さない
            ent["roles"] = {r: len(v) for r, v in by_role.items()}
        idx[kind].append(ent)
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
                    "SELECT DISTINCT vid FROM vn_tags WHERE tag = ?", tools=True)
    n_plat = listing("platform", "%s の乙女ゲーム",
                     "%s で遊べる乙女ゲーム %d作品の一覧。",
                     "SELECT DISTINCT vid FROM platforms WHERE platform = ?")
    n_pub = listing("publisher", "%s が発売した乙女ゲーム",
                    "%s が発売した乙女ゲーム %d作品の一覧。",
                    "SELECT vid FROM games WHERE publishers LIKE '%' || ? || '%'")
    n_maker = listing("maker", "%s の乙女ゲーム",
                      "%s が開発した乙女ゲーム %d作品の一覧。",
                      "SELECT vid FROM games WHERE developers LIKE '%' || ? || '%'",
                      tools=True)

    # 属性ページ（キーが category:trait）
    n_tr = 0
    for (k, key), (n, is_page) in npages.items():
        if k != "trait" or not is_page:
            continue
        cat, tr = key.split(":", 1)
        if is_en(tr):
            continue              # 訳が無い属性はページを作らない
        rows = con.execute(
            "SELECT DISTINCT vid, name FROM traits WHERE category=? AND trait=?",
            (cat, tr)).fetchall()
        items, gs = [], []
        for r in rows:
            g = games.get(r["vid"])
            if g:
                items.append((slug[("game", g["vid"])], g["title"], g["released"],
                              (g["platforms"] or "").split(" / ")[0], cover_of(g),
                              r["name"], card_attrs(g)))
                gs.append(g)
        if not items:
            continue
        items.sort(key=lambda x: (x[2] or "0000"), reverse=True)
        url = slug[(k, key)]
        # items はキャラ単位（1作品に該当キャラが複数いれば複数行）なので、
        # 作品数として数えるときは作品URLで重複を落とす
        nw = len({i[0] for i in items})
        d = "「%s」のキャラクターが登場する乙女ゲーム %d作品の一覧。" % (tr, nw)
        body = ['<h1>「%s」のキャラクターがいる乙女ゲーム</h1>' % e(tr),
                '<p class="lead">%s（%s）</p>' % (e(d), e(cat)),
                '<div data-list>', list_tools(gs), card_list(items), '</div>']
        write(url, layout("「%s」のキャラクターがいる乙女ゲーム｜%s" % (tr, SITE_NAME),
                          d, BASE_URL + url, "\n".join(body), None, cat_crumbs("trait", tr)))
        urls.append((url, None))
        idx["trait"].append(dict(url=url, label=tr, n=nw, k=slug_key(url), cat=cat))
        n_tr += 1

    # ---------------- キャラクターページ ----------------
    n_char = 0
    for (k, key), (n, is_page) in npages.items():
        if k != "character" or not is_page:
            continue
        rows = ch_by_cid.get(key)
        if not rows:
            continue
        c0 = rows[0]
        name = c0["name"]
        url = slug[(k, key)]

        # 出演作品（同じキャラが複数作品に出ることがある）
        apps = []
        for r in sorted(rows, key=lambda x: (games[x["vid"]]["released"] or "9999")):
            g = games[r["vid"]]
            apps.append((slug[("game", g["vid"])], g["title"], ja_date(g["released"]),
                         (g["platforms"] or "").split(" / ")[0], cover_of(g), r["role"]))

        cvs = sorted({r["cv"] for r in rows if r["cv"]})
        cv_links = []
        for nm in cvs:
            u = slug.get(("cv", nm))
            if not u:
                for canon, alist in alias_of.items():
                    if nm in alist:
                        u = slug.get(("cv", canon)); break
            cv_links.append('<a href="%s">%s</a>' % (e(u), e(nm)) if u else e(nm))

        facts = [("声優", " / ".join(cv_links) or "—"),
                 ("区分", e("・".join(sorted({r["role"] for r in rows})))),
                 ("誕生日", e(c0["birthday"] or "—")),
                 ("年齢", e("%d歳" % c0["age"] if c0["age"] else "—")),
                 ("身長", e("%dcm" % c0["height"] if c0["height"] else "—")),
                 ("血液型", e(c0["blood"] or "—"))]

        # 属性は traits テーブルから (分類, 属性) の対で引く。
        # characters.appearance などの平文カラムは分類が混ざっているため使わない
        bycat = defaultdict(list)
        for cat, tr in con.execute(
                "SELECT DISTINCT category, trait FROM traits WHERE cid=?", (key,)):
            if not is_en(tr):
                bycat[cat].append(tr)

        def chips(cats):
            out = []
            for cat in cats:
                for v in bycat.get(cat, []):
                    u = slug.get(("trait", "%s:%s" % (cat, v)))
                    out.append('<a href="%s">%s</a>' % (e(u), e(v)) if u else
                               '<span>%s</span>' % e(v))
            return " ".join(out)

        pers = chips(["性格"])
        appe = chips(["髪", "瞳", "外見"])
        role_t = chips(["役柄", "行動", "服装", "持ち物", "境遇"])

        b = ['<article class="chara">', '<div class="hero">']
        show_img = IMAGE_MODE == "vndb" and c0["image_url"]
        if show_img:
            b.append('<img class="cover" src="%s" alt="%s" width="180">'
                     % (e(c0["image_url"]), e(name)))
        b.append("<div>")
        b.append("<h1>%s</h1>" % e(name))
        if c0["name_latin"]:
            b.append('<p class="latin">%s</p>' % e(c0["name_latin"]))
        b.append('<table class="facts">' + "".join(
            "<tr><th>%s</th><td>%s</td></tr>" % (kk, vv) for kk, vv in facts) + "</table>")
        b.append("</div></div>")
        for ttl, html_ in (("性格", pers), ("外見", appe), ("役柄・特徴", role_t)):
            if html_:
                b.append('<section><h2>%s</h2><p class="tags">%s</p></section>' % (ttl, html_))
        if not show_img:
            b.append('<p class="ext"><a href="https://vndb.org/%s" rel="noopener" '
                     'target="_blank">VNDBでこのキャラクターを見る（立ち絵あり）</a></p>' % e(key))
        b.append('<section><h2>登場作品</h2>%s</section>' % card_list(apps))
        b.append("</article>")

        cvtxt = "、".join(cvs)
        d = "%s（%s）が登場する乙女ゲームと、プロフィール・性格・外見のまとめ。" % (
            name, cvtxt or "声優未確定")
        if len(apps) > 1:
            d += "%d作品に登場します。" % len(apps)
        ld = {"@context": "https://schema.org", "@type": "Person", "name": name,
              "url": BASE_URL + url}
        if show_img:
            ld["image"] = c0["image_url"]
        crumbs = [(SITE_NAME, "/"), (games[c0["vid"]]["title"],
                                     slug[("game", c0["vid"])]), (name, None)]
        write(url, layout("%s（CV: %s）｜%s" % (name, cvtxt or "—", SITE_NAME),
                          d, BASE_URL + url, "\n".join(b), [ld], crumbs,
                          c0["image_url"] if show_img else None))
        urls.append((url, None))
        n_char += 1

    # ---------------- シリーズページ（発売順＝プレイ順） ----------------
    n_ser = 0
    for key, v in series.items():
        if not npages.get(("series", key), (0, 0))[1]:
            continue
        url = slug[("series", key)]
        items = []
        for m in v["members"]:
            g = games[m]
            items.append((slug[("game", m)], g["title"], ja_date(g["released"]),
                          (g["platforms"] or "").split(" / ")[0], cover_of(g),
                          ("★ %.2f" % g["rating"]) if g["rating"] else None))
        d = ("「%s」シリーズの乙女ゲーム %d作品を発売順に並べた一覧。"
             "どれから遊べばよいか分かるように、続編・前日譚・ファンディスクをまとめています。"
             % (v["name"], len(items)))
        body = ["<h1>「%s」シリーズの乙女ゲーム</h1>" % e(v["name"]),
                '<p class="lead">%s</p>' % e(d),
                "<h2>発売順</h2>", card_list(items)]
        ld = {"@context": "https://schema.org", "@type": "ItemList",
              "name": "「%s」シリーズ" % v["name"], "numberOfItems": len(items),
              "itemListOrder": "https://schema.org/ItemListOrderAscending",
              "itemListElement": [{"@type": "ListItem", "position": i + 1,
                                   "url": BASE_URL + it[0], "name": it[1]}
                                  for i, it in enumerate(items)]}
        write(url, layout("「%s」シリーズの乙女ゲーム 発売順一覧｜%s" % (v["name"], SITE_NAME),
                          d, BASE_URL + url, "\n".join(body), [ld],
                          cat_crumbs("series", v["name"])))
        urls.append((url, None))
        first = next((games[m] for m in v["members"] if cover_of(games[m])),
                     games[v["members"][0]])
        idx["series"].append(dict(
            url=url, label=v["name"], n=len(items), k=slug_key(url), img=cover_of(first),
            # シリーズ名と1作目が同じ名前のことが多いので、違うときだけ添える
            rep=("1作目: %s" % first["title"]) if first["title"] != v["name"] else None))
        n_ser += 1

    # ---------------- カテゴリ索引ページ（/cv/ /maker/ …） ----------------
    # 個別ページは山ほどあるのに、そこへ辿り着く一覧が無かった。
    # ドロップダウンで727人から選ばせる代わりに、五十音・カード・チップで見せる。
    def index_page(kind):
        _, path, short, style, heading, desc_fmt, _blurb = CAT_OF[kind]
        ents = idx.get(kind, [])
        if not ents:
            return 0
        desc = desc_fmt % len(ents)
        # スラッグが名前の読みになっているカテゴリだけローマ字入力を効かせる
        is_reading = kind in ("cv", "staff", "maker", "publisher", "series")
        by_n = sorted(ents, key=lambda x: (-x["n"], x["k"]))
        inner = []
        if style == "kana":
            # 目的の無い人向けに、まず作品数の多い順を出してから五十音に落とす
            inner.append('<section class="idx-sec idx-top" data-top>'
                         '<h2>作品数の多い%s</h2>%s</section>'
                         % (e(short), idx_names(by_n[:30])))
            inner.append(idx_kana(ents))
        elif style == "cards":
            inner.append(idx_cards(by_n))
        elif style == "chips":
            inner.append(idx_chips(by_n))
        elif style == "byrole":
            inner.append(idx_byrole(ents))
        else:
            inner.append(idx_bycat(ents))
        body = ["<h1>%s</h1>" % e(heading),
                '<p class="lead">%s</p>' % e(desc),
                idx_wrap("\n".join(inner),
                         "%sの名前で絞り込む（かな%sも可）"
                         % (short, "・ローマ字" if is_reading else ""), is_reading)]
        ld = {"@context": "https://schema.org", "@type": "ItemList", "name": heading,
              "numberOfItems": len(ents),
              "itemListElement": [{"@type": "ListItem", "position": i + 1,
                                   "url": BASE_URL + it["url"], "name": it["label"]}
                                  for i, it in enumerate(by_n[:50])]}
        write(path, layout("%s｜%s" % (heading, SITE_NAME), desc, BASE_URL + path,
                           "\n".join(body), [ld],
                           [(SITE_NAME, "/"), (short, None)]))
        urls.append((path, None))
        return len(ents)

    n_idx = sum(1 for c in CATS if index_page(c[0]))

    # カテゴリの入口カード。トップに置く（検索中は app.js が section ごと隠す）
    bl = []
    for kind, path, short, style, heading, desc_fmt, blurb in CATS:
        n = len(idx.get(kind, []))
        if n:
            bl.append('<li><a href="%s"><b>%s</b><span class="idx-n">%d件</span>'
                      '<small>%s</small></a></li>' % (e(path), e(heading), n, e(blurb)))
    browse = ('<section id="browse"><h2>カテゴリから探す</h2>'
              '<ul class="browse">%s</ul></section>' % "".join(bl))

    # ---------------- トップ（検索） ----------------
    today = datetime.date.today().isoformat()
    released = [g for g in games.values() if g["released"] and g["released"] <= today]
    upcoming = [g for g in games.values() if g["released"] and g["released"] > today]
    recent = sorted(released, key=lambda g: g["released"], reverse=True)[:24]
    upcoming.sort(key=lambda g: g["released"])
    body = ['<h1>%s</h1>' % e(SITE_NAME),
            '<p class="lead">%s 家庭用ゲーム%d作品・声優%d人を収録。</p>' % (e(SITE_DESC), len(games), n_cv),
            '<div id="app"><noscript><p>検索にはJavaScriptが必要です。'
            '下のカテゴリ一覧と新着はそのままご覧いただけます。</p></noscript></div>',
            browse,
            ]
    if upcoming:
        body += ["<h2>発売予定</h2>",
                 card_list([(slug[("game", g["vid"])], g["title"], ja_date(g["released"]),
                             (g["platforms"] or "").split(" / ")[0], cover_of(g), None)
                            for g in upcoming])]
    body += ["<h2>最近発売された作品</h2>",
             card_list([(slug[("game", g["vid"])], g["title"], ja_date(g["released"]),
                         (g["platforms"] or "").split(" / ")[0], cover_of(g),
                         ("★ %.2f" % g["rating"]) if g["rating"] else None)
                        for g in recent])]
    write("/", layout("%s｜乙女ゲームを声優・キャラ属性から探す" % SITE_NAME, SITE_DESC,
                      BASE_URL + "/", "\n".join(body), None, None, home=True))
    urls.append(("/", None))

    # ---------------- assets / sitemap / robots ----------------
    assets_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    shutil.copytree(assets_src, os.path.join(OUT, "assets"))
    # traits.json はキャラ属性の絞り込みをトップから外したので配らない
    # （属性から辿るのは /trait/ の索引ページの役目）
    for name in ("index.json", "suggest.json"):
        shutil.copy(os.path.join(DATA, "site", name),
                    os.path.join(OUT, "assets", name))

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
    if IMAGE_MODE != "vndb":
        miss = sum(1 for g in games.values() if not cover_of(g))
        print("  画像モード: %s（アフィリエイト画像なし %d件 / キャラ画像は非表示）"
              % (IMAGE_MODE, miss))
    print("  作品      %5d" % len(games))
    print("  キャラクター %4d" % n_char)
    print("  声優      %5d" % n_cv)
    print("  スタッフ   %5d" % n_staff)
    print("  キャラ属性 %5d" % n_tr)
    print("  タグ      %5d" % n_tag)
    print("  シリーズ   %5d" % n_ser)
    print("  メーカー   %5d" % n_maker)
    print("  発売元    %5d" % n_pub)
    print("  機種      %5d" % n_plat)
    print("  索引ページ %5d" % n_idx)
    print("  トップ        1")
    print("  ------------------")
    print("  合計      %5d ページ" % len(urls))
    con.close()


if __name__ == "__main__":
    main()
