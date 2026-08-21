# -*- coding: utf-8 -*-
"""アフィリエイトIDとAPI認証情報の設定。

値は「環境変数 → このファイルの既定値」の順で解決する。
`scripts/` は git 管理下なので、**秘密情報は環境変数で渡すこと**。
特に accessKey はファイルに直書きしないほうがいい。

    export RAKUTEN_APPLICATION_ID=1234567890123456789
    export RAKUTEN_ACCESS_KEY=pk_xxxxxxxxxxxxxxxxxxxx
    export RAKUTEN_AFFILIATE_ID=1a2b3c4d.5e6f7g8h.9i0j1k2l.3m4n5o6p

空のままなら素のURLが生成される（リンクとしては正しく動く）。
"""
import os


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


# --- 楽天 ---------------------------------------------------------------
# 楽天アフィリエイトID（例: "1a2b3c4d.5e6f7g8h.9i0j1k2l.3m4n5o6p"）
RAKUTEN_AFFILIATE_ID = _env("RAKUTEN_AFFILIATE_ID", "")

# 楽天ウェブサービスのアプリID（商品検索APIに必要）
RAKUTEN_APPLICATION_ID = _env("RAKUTEN_APPLICATION_ID", "")

# 楽天ウェブサービスのアクセスキー（"pk_" で始まる）。
# 2026年のインフラ刷新で applicationId だけでは通らなくなり、必須になった。
# 既存アプリの編集では発行されないので「新規アプリ登録」から取り直すこと。
RAKUTEN_ACCESS_KEY = _env("RAKUTEN_ACCESS_KEY", "")

# --- そのほかのチャネル -------------------------------------------------
# AmazonアソシエイトのトラッキングID（例: "otomedana-22"）
AMAZON_ASSOCIATE_TAG = _env("AMAZON_ASSOCIATE_TAG", "")

# 駿河屋アフィリエイトのパートナーID
SURUGAYA_AFFILIATE_ID = _env("SURUGAYA_AFFILIATE_ID", "")

# A8.net のアニメイト用リンク（発行された a8.net のリダイレクトURLをそのまま入れる）
# 例: "https://px.a8.net/svt/ejp?a8mat=XXXXXX"
ANIMATE_A8_BASE = _env("ANIMATE_A8_BASE", "")

# メルカリアンバサダーのリンク（発行形式が決まったら反映）
MERCARI_AMBASSADOR_ID = _env("MERCARI_AMBASSADOR_ID", "")

# 駿河屋楽天市場店
#   shopCode … 楽天商品検索APIの shopCode パラメータ用（文字列）
#   sid      … 楽天市場の店舗内検索URL用（数値）。実地確認済み: 全件 surugaya-a-too の商品が返る
SURUGAYA_RAKUTEN_SHOPCODE = "surugaya-a-too"
SURUGAYA_RAKUTEN_SID = "239310"

# 中古を扱う楽天市場の店舗コード（rakuten_prices.py の新品/中古判定に使う）
RAKUTEN_USED_SHOPCODES = {
    "surugaya-a-too",   # 駿河屋
    "bookoffonline",    # ブックオフ
    "auc-rally",
    "geoonlinestore",   # ゲオ
}
