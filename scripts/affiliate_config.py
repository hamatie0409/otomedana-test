# -*- coding: utf-8 -*-
"""アフィリエイトIDとAPI認証情報の設定。

値は「環境変数 → ~/.config/otomegamedb/env → このファイルの既定値」の順で解決する。

**秘密なのは楽天の accessKey だけ**。ほかのIDは生成されるリンクに必ず現れる公開値
（アフィリエイトIDは hb.afl.rakuten.co.jp のURLに、アプリIDは rafcid= に出る）なので、
ここに既定値として書いてある。こうしておくと GitHub Actions で必要な Secret が
RAKUTEN_ACCESS_KEY の1つで済む。

    # ~/.config/otomegamedb/env （権限600・git管理外）
    export RAKUTEN_APPLICATION_ID=...
    export RAKUTEN_ACCESS_KEY=pk_...
    export RAKUTEN_AFFILIATE_ID=1a2b3c4d.5e6f7g8h.9i0j1k2l.3m4n5o6p

このファイルは対話シェルからは ~/.zshrc 経由で読まれるが、
スクリプトを非対話で走らせたときは読まれないので、こちらでも直接読む。
GitHub Actions のように環境変数で渡せる場所では、そちらが優先される。

空のままなら素のURLが生成される（リンクとしては正しく動く）。
"""
import os

CONFIG_FILE = os.path.expanduser("~/.config/otomegamedb/env")


def _from_file():
    """`export KEY=VALUE` の並んだ設定ファイルを読む。無ければ空"""
    out = {}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return out


_FILE = _from_file()


def _env(name, default=""):
    return (os.environ.get(name) or _FILE.get(name) or default).strip()


# --- 楽天 ---------------------------------------------------------------
# 楽天アフィリエイトID（例: "1a2b3c4d.5e6f7g8h.9i0j1k2l.3m4n5o6p"）
RAKUTEN_AFFILIATE_ID = _env("RAKUTEN_AFFILIATE_ID", "1f97fe7f.938d5c13.1f97fe80.1628d44a")

# 楽天ウェブサービスのアプリID（商品検索APIに必要）
RAKUTEN_APPLICATION_ID = _env("RAKUTEN_APPLICATION_ID", "8d2fe18b-88d8-4575-a194-71a7f7254c59")

# 楽天ウェブサービスに登録したアプリのURL。
# 2026年のインフラ刷新以降、リクエストに Referer と Origin が無いと
# 403 REQUEST_CONTEXT_BODY_HTTP_REFERRER_MISSING で弾かれる（実地確認済み 2026-08-21）。
# 公開先が決まったら site_config.SITE_URL と揃えること。
RAKUTEN_APP_URL = _env("RAKUTEN_APP_URL", "https://hamatie0409.github.io")

# 楽天ウェブサービスのアクセスキー（"pk_" で始まる）。**ここだけ秘密**。
# 絶対にこのファイルに書かないこと（scripts/ は git 管理下）。
# 2026年のインフラ刷新で applicationId だけでは通らなくなり、必須になった。
# 既存アプリの編集では発行されないので「新規アプリ登録」から取り直すこと。
RAKUTEN_ACCESS_KEY = _env("RAKUTEN_ACCESS_KEY", "")

# --- そのほかのチャネル -------------------------------------------------
# AmazonアソシエイトのトラッキングID（例: "otomedana-22"）
AMAZON_ASSOCIATE_TAG = _env("AMAZON_ASSOCIATE_TAG", "hamat1e-22")

# 駿河屋アフィリエイトのID（af_jump.php の user_id にあたる）。
# 素のURLにパラメータを足す方式ではなく、リダイレクトURLを組む
SURUGAYA_AFFILIATE_ID = _env("SURUGAYA_AFFILIATE_ID", "4041")

# A8.net のアニメイト用リンク（発行された a8.net のリダイレクトURLをそのまま入れる）
# 例: "https://px.a8.net/svt/ejp?a8mat=XXXXXX"
ANIMATE_A8_BASE = _env("ANIMATE_A8_BASE", "")

# メルカリアンバサダーのリンク（発行形式が決まったら反映）
MERCARI_AMBASSADOR_ID = _env("MERCARI_AMBASSADOR_ID", "2811053099")

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
