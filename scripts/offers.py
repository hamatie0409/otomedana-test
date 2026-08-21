# -*- coding: utf-8 -*-
"""【フェーズ2】版ごとの購入先（offers）を作る。

  python3 scripts/editions.py    # 先に版を作っておく
  python3 scripts/offers.py

1行 = 「この版を、この店で買う導線」。
価格・在庫・商品直リンクの列は用意だけして、この段階では空にしておく。
  ・楽天／駿河屋楽天市場店 … 認証情報が入れば rakuten_prices.py が埋める
  ・Amazon                … PA-API が通れば asin と価格が埋まる
どちらも「行を後から UPDATE するだけ」で済むように、URLと識別子は先に確定させる。

アフィリエイトIDは scripts/affiliate_config.py。未設定なら素のURLになる。
URLの形式はすべて実地で疎通確認済み（2026-08-20）。
"""
import os, sqlite3, urllib.parse
import affiliate_config as AF
from common import DATA
from editions import PLATFORM_KW, STORE_JA

SCHEMA = """
DROP TABLE IF EXISTS offers;
CREATE TABLE offers (
    eid TEXT REFERENCES editions(eid),
    vid TEXT REFERENCES games(vid),
    channel TEXT,        -- 楽天 / Amazon / アニメイト / 駿河屋 / メルカリ / 配信ストア名
    via TEXT,            -- 経路。'' か 'rakuten_shop'（駿河屋楽天市場店）
    condition TEXT,      -- 新品 / 中古 / ダウンロード
    link_type TEXT,      -- search=検索結果 / item=商品直リンク / store=公式ストア
    key_type TEXT,       -- jan / title / url
    key_value TEXT,
    url TEXT,            -- 表示に使うURL（アフィリエイトIDが設定されていれば適用済み）
    -- ここから下は【フェーズ2/3】で UPDATE する。今は NULL
    item_code TEXT, item_name TEXT, image_url TEXT,
    price INTEGER, availability TEXT, fetched_at TEXT,
    priority INTEGER     -- 小さいほど上に表示
);
CREATE INDEX idx_of_eid ON offers(eid);
CREATE INDEX idx_of_vid ON offers(vid);
CREATE INDEX idx_of_ch ON offers(channel);
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
    """駿河屋本体のキーワード検索。JANでは引けないのでタイトルを渡すこと
    （JAN検索はフォームトークンが必要でGETリンクでは動かない。実地確認済み 2026-08-20）"""
    url = "https://www.suruga-ya.jp/search?category=&search_word=%s&searchbox=1" % q(kw)
    if AF.SURUGAYA_AFFILIATE_ID:
        url += "&aff=" + AF.SURUGAYA_AFFILIATE_ID
    return url


def used_keyword(e):
    """中古の検索語。

    駿河屋・メルカリはJANで引けずキーワード検索しかない。
    機種を足さないと別機種の版が混ざるので必ず付ける。
    版名は「限定版」「ツインパック」だけ足す。中古の出品は通常版をわざわざ
    「通常版」と書かないことが多く、付けると空振りになるため。
    """
    parts = [e["search_kw"], PLATFORM_KW.get(e["platform"], "")]
    if e["edition_kind"] in ("限定", "セット"):
        parts.append(e["edition"])
    return " ".join(p for p in parts if p)


def new_keyword(e):
    """新品の検索語（JANが無い版のフォールバック）"""
    parts = [e["search_kw"], PLATFORM_KW.get(e["platform"], "")]
    if e["edition_kind"] in ("限定", "セット"):
        parts.append(e["edition"])
    return " ".join(p for p in parts if p)


def main():
    con = sqlite3.connect(os.path.join(DATA, "vndb_otome.db"))
    con.row_factory = sqlite3.Row
    eds = con.execute("SELECT * FROM editions").fetchall()
    con.executescript(SCHEMA)

    rows = []
    n_dl_nostore = 0
    for e in eds:
        gtin = e["gtin"]
        year = e["released"][:4]
        # 2015年以前は新品の流通がほぼ無いので、中古を上に出す
        used_first = not year.isdigit() or int(year) <= 2015

        new, used, store = [], [], []

        if e["is_dl"]:
            # ダウンロード版。中古は存在せず、アフィリエイトの提携先も無いので
            # 公式の配信ストアに送るだけにする
            if e["store_url"]:
                name = STORE_JA.get(e["store_site"], e["store_site"])
                store.append((name, "", "ダウンロード", "store", "url",
                              e["store_url"], e["store_url"]))
            else:
                n_dl_nostore += 1
        else:
            if gtin:
                # JANで引ければ通常版と限定版を撃ち分けられる。これが本筋
                new.append(("楽天", "", "新品", "search", "jan", gtin, rakuten(gtin)))
                new.append(("Amazon", "", "新品", "search", "jan", gtin, amazon(gtin)))
            else:
                kw = new_keyword(e)
                new.append(("楽天", "", "新品", "search", "title", kw, rakuten(kw)))
                new.append(("Amazon", "", "新品", "search", "title", kw, amazon(kw)))
            kw = new_keyword(e)
            new.append(("アニメイト", "", "新品", "search", "title", kw, animate(kw)))

            ukw = used_keyword(e)
            used.append(("駿河屋", "", "中古", "search", "title", ukw, surugaya(ukw)))
            used.append(("メルカリ", "", "中古", "search", "title", ukw, mercari(ukw)))
            # 駿河屋楽天市場店。表示はしないが、フェーズ2でここから中古価格を取る。
            # 駿河屋本体には公式APIが無いので、規約上安全に価格を取れる唯一の経路
            used.append(("駿河屋", "rakuten_shop", "中古", "search", "title", ukw,
                         rakuten_shop(ukw, AF.SURUGAYA_RAKUTEN_SID)))

        ordered = store + ((used + new) if used_first else (new + used))
        for i, (ch, via, cond, lt, kt, kv, url) in enumerate(ordered):
            rows.append((e["eid"], e["vid"], ch, via, cond, lt, kt, kv, url,
                         None, None, None, None, None, None, i))

    con.executemany("INSERT INTO offers VALUES (%s)" % ",".join("?" * 16), rows)
    con.commit()

    n = lambda s: con.execute(s).fetchone()[0]
    print("offers %d行 / %d版 / %d作品"
          % (len(rows), n("SELECT COUNT(DISTINCT eid) FROM offers"),
             n("SELECT COUNT(DISTINCT vid) FROM offers")))
    print()
    print("=== チャネル別（掲載対象の作品のみ）===")
    HOME = ("Switch", "PS", "ニンテンドー", "Xbox")
    cond = " OR ".join("platforms LIKE '%%%s%%'" % k for k in HOME)
    sub = "SELECT vid FROM games WHERE %s" % cond
    for ch, via, c, v in con.execute(
            """SELECT channel, via, COUNT(*), COUNT(DISTINCT vid) FROM offers
               WHERE vid IN (%s) GROUP BY channel, via ORDER BY 3 DESC""" % sub):
        print("  %-14s %5d行 / %4d作品%s"
              % (ch, c, v, "  ※表示せず価格取得用" if via else ""))
    print()
    print("配信ストアのリンクが無いDL版: %d行（購入導線を出せないので非表示）" % n_dl_nostore)
    tot = n("SELECT COUNT(*) FROM games WHERE %s" % cond)
    cov = n("SELECT COUNT(DISTINCT vid) FROM offers WHERE vid IN (%s)" % sub)
    print("掲載対象 %d件 のうち購入導線あり %d件 (%.0f%%)" % (tot, cov, 100.0 * cov / tot))

    unset = [k for k in ("RAKUTEN_AFFILIATE_ID", "AMAZON_ASSOCIATE_TAG",
                         "SURUGAYA_AFFILIATE_ID", "ANIMATE_A8_BASE") if not getattr(AF, k)]
    if unset:
        print()
        print("※ 未設定のアフィリエイトID: %s" % ", ".join(unset))
        print("  素のURLで生成済み。IDを埋めて再実行すれば置き換わる。")
    con.close()


if __name__ == "__main__":
    main()
