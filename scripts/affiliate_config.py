# -*- coding: utf-8 -*-
"""アフィリエイトIDの設定。取得できたものから埋めていく。
空のままなら素のURLが生成される（リンクとしては正しく動く）。
"""

# 楽天アフィリエイトID（例: "1a2b3c4d.5e6f7g8h.9i0j1k2l.3m4n5o6p"）
RAKUTEN_AFFILIATE_ID = ""

# 楽天ウェブサービスのアプリID（商品検索APIに必要）
RAKUTEN_APPLICATION_ID = ""

# AmazonアソシエイトのトラッキングID（例: "otomedana-22"）
AMAZON_ASSOCIATE_TAG = ""

# 駿河屋アフィリエイトのパートナーID
SURUGAYA_AFFILIATE_ID = ""

# A8.net のアニメイト用リンク（発行された a8.net のリダイレクトURLをそのまま入れる）
# 例: "https://px.a8.net/svt/ejp?a8mat=XXXXXX"
ANIMATE_A8_BASE = ""

# メルカリアンバサダーのリンク（発行形式が決まったら反映）
MERCARI_AMBASSADOR_ID = ""

# 駿河屋楽天市場店
#   shopCode … 楽天商品検索APIの shopCode パラメータ用（文字列）
#   sid      … 楽天市場の店舗内検索URL用（数値）。実地確認済み: 全件 surugaya-a-too の商品が返る
SURUGAYA_RAKUTEN_SHOPCODE = "surugaya-a-too"
SURUGAYA_RAKUTEN_SID = "239310"
