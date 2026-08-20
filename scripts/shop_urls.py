"""【フェーズ1】5チャネルの購入URLを生成して shop_urls テーブルに入れる。

  python3 scripts/buy_links.py     # 先にJANを抽出しておく
  python3 scripts/shop_urls.py

アフィリエイトIDは scripts/affiliate_config.py。未設定なら素のURLになる。
URLの形式はすべて実地で疎通確認済み（2026-08-20）。
"""
import os, sqlite3, urllib.parse
import affiliate_config as AF
from common import DATA

SCHEMA = """
DROP TABLE IF EXISTS shop_urls;
CREATE TABLE shop_urls (
    vid TEXT REFERENCES games(vid),
    channel TEXT,        -- 楽天 / Amazon / アニメイト / メルカリ / 駿河屋
    condition TEXT,      -- 新品 / 中古
    key_type TEXT,       -- jan / title
    key_value TEXT,
    url TEXT,
    priority INTEGER     -- 小さいほど上に表示
);
CREATE INDEX idx_su_vid ON shop_urls(vid);
CREATE INDEX idx_su_ch ON shop_urls(channel);
"""

q = lambda s: urllib.parse.quote(str(s), safe="")


def rakuten(kw):
    url = "https://search.rakuten.co.jp/search/mall/%s/" % q(kw)
    if AF.RAKUTEN_AFFILIATE_ID:
        return "https://hb.afl.rakuten.co.jp/hgc/%s/?pc=%s&m=%s" % (
            AF.RAKUTEN_AFFILIATE_ID, q(url), q(url))
    return url


def rakuten_shop(kw, sid):
    """楽天市場の店舗内検索（駿河屋楽天市場店の中古を引く）
    パス形式のキーワード＋数値sid でないと店舗で絞れない（実地確認済み）"""
    url = "https://search.rakuten.co.jp/search/mall/%s/?sid=%s" % (q(kw), sid)
    if AF.RAKUTEN_AFFILIATE_ID:
        return "https://hb.afl.rakuten.co.jp/hgc/%s/?pc=%s&m=%s" % (
            AF.RAKUTEN_AFFILIATE_ID, q(url), q(url))
    return url


def amazon(kw):
    url = "https://www.amazon.co.jp/s?k=%s" % q(kw)
    if AF.AMAZON_ASSOCIATE_TAG:
        url += "&tag=" + AF.AMAZON_ASSOCIATE_TAG
    return url


def animate(kw):
    url = "https://www.animate-onlineshop.jp/products/list.php?smt=%s" % q(kw)
    if AF.ANIMATE_A8_BASE:
        return "%s&a8ejpredirect=%s" % (AF.ANIMATE_A8_BASE, q(url))
    return url


def mercari(kw):
    return "https://jp.mercari.com/search?keyword=%s" % q(kw)


def surugaya(kw):
    """駿河屋本体のキーワード検索。JANでは引けないのでタイトルを渡すこと"""
    url = "https://www.suruga-ya.jp/search?category=&search_word=%s&searchbox=1" % q(kw)
    if AF.SURUGAYA_AFFILIATE_ID:
        url += "&aff=" + AF.SURUGAYA_AFFILIATE_ID
    return url


def main():
    con = sqlite3.connect(os.path.join(DATA, "vndb_otome.db"))
    con.executescript(SCHEMA)

    games = con.execute("SELECT vid, title, released, platforms FROM games").fetchall()
    jan = {}
    for vid, g, official in con.execute(
            "SELECT vid, gtin, official FROM gtins ORDER BY official DESC, released"):
        jan.setdefault(vid, g)      # 公式・古い順に最初の1件を代表とする

    rows = []
    for vid, title, released, platforms in games:
        if not title:
            continue
        year = int(released[:4]) if released and released[:4].isdigit() else None
        used_first = year is None or year <= 2015     # 中古を上に出すか
        g = jan.get(vid)

        # 新品系
        new = []
        if g:
            new.append(("楽天", "新品", "jan", g, rakuten(g)))
            new.append(("Amazon", "新品", "jan", g, amazon(g)))
        new.append(("楽天", "新品", "title", title, rakuten(title)))
        new.append(("Amazon", "新品", "title", title, amazon(title)))
        new.append(("アニメイト", "新品", "title", title, animate(title)))

        # 中古系
        used = []
        used.append(("駿河屋", "中古", "title", title, rakuten_shop(title, AF.SURUGAYA_RAKUTEN_SID)))
        # 駿河屋本体はJAN検索がGETリンクでは動かない（フォームトークンが必要）ため
        # タイトル検索のみ。実地確認済み 2026-08-20
        used.append(("駿河屋", "中古", "title", title, surugaya(title)))
        used.append(("メルカリ", "中古", "title", title, mercari(title)))

        ordered = (used + new) if used_first else (new + used)
        for i, (ch, cond, kt, kv, url) in enumerate(ordered):
            rows.append((vid, ch, cond, kt, kv, url, i))

    con.executemany("INSERT INTO shop_urls VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()

    n = lambda s: con.execute(s).fetchone()[0]
    print("shop_urls %d行 / %d作品" % (len(rows), n("SELECT COUNT(DISTINCT vid) FROM shop_urls")))
    print()
    print("=== チャネル別 ===")
    for ch, c, v in con.execute(
            "SELECT channel, COUNT(*), COUNT(DISTINCT vid) FROM shop_urls GROUP BY channel ORDER BY 3 DESC"):
        print("  %-10s %5d行 / %4d作品" % (ch, c, v))
    home = ("Switch", "PS", "ニンテンドー", "Xbox")
    cond = " OR ".join("platforms LIKE '%%%s%%'" % k for k in home)
    tot = n("SELECT COUNT(*) FROM games WHERE %s" % cond)
    cov = n("""SELECT COUNT(DISTINCT g.vid) FROM games g JOIN shop_urls s ON s.vid=g.vid
               WHERE %s""" % cond)
    print()
    print("家庭用機 %d件 のうち購入導線あり %d件 (%.0f%%)" % (tot, cov, 100.0 * cov / tot))
    unset = [k for k in ("RAKUTEN_AFFILIATE_ID", "AMAZON_ASSOCIATE_TAG",
                         "SURUGAYA_AFFILIATE_ID", "ANIMATE_A8_BASE") if not getattr(AF, k)]
    if unset:
        print()
        print("※ 未設定のアフィリエイトID: %s" % ", ".join(unset))
        print("  素のURLで生成済み。IDを埋めて再実行すれば置き換わる。")
    con.close()


if __name__ == "__main__":
    main()
