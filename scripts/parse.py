"""raw/item/*.html を解析して data/games.jsonl を作る。ネットワークアクセスなし。
項目を足したくなったら、取得はやり直さずこれだけ再実行すればよい。

  python3 scripts/parse.py
"""
import os, re, json, glob, html as htmlmod
from common import DATA, RAW_ITEM, BASE

def text(s):
    """タグを除去して素のテキストにする"""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<!--.*?-->", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = htmlmod.unescape(s)
    s = s.replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()

def first(pat, s, flags=0):
    m = re.search(pat, s, flags)
    return m.group(1) if m else None

def parse_datatable(s):
    """<table class="item-datatable"> の th/td を {見出し: (テキスト, [href...])} で返す"""
    tbl = first(r'(?s)<table class="item-datatable">(.*?)</table>', s)
    rows = {}
    if not tbl:
        return rows
    for th, td in re.findall(r"(?s)<th>(.*?)</th>\s*<td>(.*?)</td>", tbl):
        key = text(th)
        val = text(td)
        if val in ("--", "-", ""):
            val = None
        hrefs = re.findall(r'href="([^"]+)"', td)
        rows[key] = (val, hrefs, td)
    return rows

def parse_characters(s):
    """出演声優欄から (区分, キャラ名, 声優名) を取り出す"""
    block = first(r'(?s)<div class="item-databox" id="incast">(.*?)</div>\s*</div>', s)
    if not block:
        return []
    block = first(r'(?s)<div class="text">(.*)', block) or block
    out, section = [], None
    for line in re.split(r"(?i)<br\s*/?>", block):
        if "alignRight" in line:
            break
        m = re.search(r'(?s)^(.*?)[－–—\-]\s*<a[^>]*>(.*?)</a>', line)
        if m:
            name, cv = text(m.group(1)), text(m.group(2))
            if name or cv:
                out.append({"section": section, "character": name or None, "cv": cv or None})
            continue
        t = text(line)
        if t and len(t) < 30 and "※" not in t:
            section = t
    return out

def to_iso(jp):
    """「2005年11月09日」→「2005-11-09」。日付が欠けていれば取れる範囲まで、解釈不能ならNone"""
    if not jp:
        return None
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", jp)
    if m:
        return "%04d-%02d-%02d" % tuple(int(x) for x in m.groups())
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", jp)
    if m:
        return "%04d-%02d" % tuple(int(x) for x in m.groups())
    m = re.search(r"(\d{4})\s*年", jp)
    return m.group(1) if m else None


def parse_keywords(s):
    """キーワード欄のタグを取り出す"""
    block = first(r'(?s)<div class="text item-keyword">(.*?)</div>', s)
    if not block:
        return []
    return [text(a) for a in re.findall(r'<a[^>]*href="[^"]*/game/key/[^"]*"[^>]*>(.*?)</a>', block)]


def parse_one(path):
    s = open(path, encoding="utf-8", errors="replace").read()
    item_id = int(os.path.splitext(os.path.basename(path))[0])
    d = parse_datatable(s)

    def f(key):
        return d.get(key, (None, [], ""))[0]

    def anchor(key, pat):
        """指定パターンのリンクのアンカーテキストを返す"""
        raw = d.get(key, (None, [], ""))[2]
        m = re.search(r'<a[^>]*href="[^"]*%s[^"]*"[^>]*>(.*?)</a>' % pat, raw, re.S)
        return text(m.group(1)) if m else None

    def link(key, pat=None):
        _, hrefs = d.get(key, (None, [], ""))[:2]
        for h in hrefs:
            if pat is None or re.search(pat, h):
                return h
        return None

    subcat = first(r'(?s)<div class="subcategory">(.*?)</div>', s) or ""
    game_type = text(first(r'<span class="type-[a-z]+">(.*?)</span>', subcat) or "") or None

    rec = {
        "id": item_id,
        "url": "%s/item/%d" % (BASE, item_id),
        "title": text(first(r'(?s)<h1 id="incommon">(.*?)</h1>', s) or "") or None,
        "game_type": game_type,                       # 乙女ゲーム / BLゲーム など
        "platform": f("プラットフォーム"),
        "series": f("シリーズ"),
        "series_id": (lambda h: int(h.rsplit("/", 1)[1]) if h else None)(link("シリーズ", r"/game/series/\d+")),
        "release_date": f("発売日"),
        "release_date_iso": to_iso(f("発売日")),
        "genre": ((f("ジャンル") or "").split("/") + [""])[0].strip() or None,
        "genre_sub": ((f("ジャンル") or "").split("/") + [""])[1].strip() or None,
        "price": f("販売価格"),
        "official_site": link("ゲーム公式サイト"),
        "official_blog": link("ゲーム公式ブログ"),
        "maker": anchor("メーカー", r"/game/maker/") or f("メーカー"),
        "maker_id": (lambda h: int(h.rsplit("/", 1)[1]) if h else None)(link("メーカー", r"/game/maker/\d+")),
        "maker_site": link("メーカー", r"^(?!.*otomex\.net)"),
        "character_design": f("キャラクターデザイン"),
        "scenario": f("シナリオ"),
        "image_url": (lambda u: None if not u or "no-image" in u else u)(
            first(r'<img src="([^"]+)"[^>]*class="item-image"', s)),
        "asin": first(r"/ASIN/([A-Z0-9]{10})/", s),
        "keywords": parse_keywords(s),
        "characters": parse_characters(s),
        "last_updated": (first(r"最終更新日時：\s*([0-9\-: ]+)", text(s)) or "").strip() or None,
    }
    rec["bonus"] = None
    tok = first(r'(?s)<div class="item-databox"\s*id="intokuten">(.*?)</div>\s*</div>', s)
    if tok:
        t = text(tok)
        rec["bonus"] = None if "特典情報はありません" in t else t[:2000] or None
    return rec

def main():
    paths = sorted(glob.glob(os.path.join(RAW_ITEM, "*.html")),
                   key=lambda p: int(os.path.splitext(os.path.basename(p))[0]))
    os.makedirs(DATA, exist_ok=True)
    out = os.path.join(DATA, "games.jsonl")
    n = 0
    with open(out, "w", encoding="utf-8") as f:
        for p in paths:
            rec = parse_one(p)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    print("%d件を解析 -> %s" % (n, out))

if __name__ == "__main__":
    main()
