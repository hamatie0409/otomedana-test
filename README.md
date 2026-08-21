# オトメ棚 — 乙女ゲーム検索・購入導線サイト

乙女ゲームを**声優・キャラクター属性・機種**から探し、買えるお店へ案内する静的サイトです。
家庭用ゲーム機で遊べる日本語の乙女ゲーム 637作品を収録しています。

## データの出典とライセンス

作品・キャラクター・声優のデータは **[VNDB](https://vndb.org/)** の公開ダンプ（<https://vndb.org/d14>）から
生成しています。VNDBのデータベースは **[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/)**、
その内容物は **[Database Contents License (DbCL)](https://opendatacommons.org/licenses/dbcl/1-0/)** のもとで提供されています。

本リポジトリが生成する派生データベースも、同じく **ODbL 1.0** のもとで提供します。

ODbL 4.6（派生データベースへのアクセス提供）については、条文が認める
**(b) 改変方法（アルゴリズム）の提供**を選択しています。
`scripts/` 以下のスクリプトが、VNDBの元データから本サイトの派生データベースを
生成する方法そのものです。誰でも同じ手順で再現できます。

価格・在庫の情報は各ストア（楽天・Amazon・アニメイト・メルカリ・駿河屋）から取得したもので、
VNDB由来ではなく、ODbLの対象外です。各ストアの利用規約に従います。

## 再現手順

```bash
# 1. VNDBダンプの取得（178MB / 毎日 8:00 UTC 更新）
mkdir -p vndb && cd vndb
curl -L -o vndb-db-latest.tar.zst https://dl.vndb.org/dump/vndb-db-latest.tar.zst
zstd -dc vndb-db-latest.tar.zst | tar -xf -
cd ..

# 2. 派生データベースの生成
python3 scripts/vndb_build.py     # ダンプ → JSONL
python3 scripts/vndb_export.py    # JSONL → SQLite + Excel
python3 scripts/buy_links.py      # JAN・外部リンクの抽出
python3 scripts/editions.py       # 版（機種×通常版/限定版/DL版）に組み直す
python3 scripts/offers.py         # 版ごとの購入先URLを作る
python3 scripts/slugs.py          # URLスラッグの確定

# 価格とパッケージ画像（楽天ウェブサービスの認証情報が要る。価格は規約上24時間で失効）
python3 scripts/rakuten_prices.py --only-scope   # JANで引く（1601件・約30分）
python3 scripts/rakuten_prices.py --by-title     # JANの無い版をタイトルで引く
python3 scripts/shop_images.py                   # 商品画像から作品の顔を選ぶ

python3 scripts/site_data.py      # 検索インデックス（shop_images の後に実行すること）

# 3. 静的サイトの生成（6,729ページ）
python3 scripts/site_build.py
```

必要なもの: Python 3.9 以上、`zstd`、`openpyxl`（Excel出力を使う場合のみ）。
全体で約1分です。

> **実行順に注意** — `vndb_export.py` はテーブルを作り直すので、
> 必ず `vndb_export.py` → `buy_links.py` → `editions.py` → `offers.py` → `slugs.py` の順で実行してください。

## 自動更新（GitHub Actions）

ワークフローは2本。**役割をはっきり分けてある。**

| ファイル | いつ動く | やること |
|---|---|---|
| `daily.yml` | 毎日 22:00 JST | 楽天から価格を取り直してサイトを作り直し、公開する |
| `rebuild-db.yml` | **手動のときだけ** | VNDBのダンプからカタログ（作品一覧・版・URL）を作り直す |

> **カタログは自動では作り直さない。**
> 乙女ゲームの判定はVNDBのタグ投票の平均でしていて日々動くので、勝手に作り直すと
> 収録作品が出入りし、公開済みのURLが変わって404が出る。作り直すのは明示的に
> `rebuild-db.yml` を実行したときだけ。実行すると「消えるURL / 増えるURL」を
> 一覧で出すので、確認してから公開できる。

日次ビルドが使うDBは、リリース `db-latest` に置いた `vndb_otome.db`。
`rebuild-db.yml` が更新し、`daily.yml` は取得するだけ。

**動かす前に3つ設定が要る。**

1. **Secret を1つ登録する**
   Settings > Secrets and variables > Actions で `RAKUTEN_ACCESS_KEY` を追加。
   秘密なのはこれだけで、ほかのアフィリエイトIDは生成されるリンクに必ず現れる
   公開値なので `scripts/affiliate_config.py` に既定値として入っている。

2. **Pages の配信元を変える**
   Settings > Pages > Source を「GitHub Actions」にする。

3. **カタログDBを置く**（初回のみ）
   手元のDBをそのまま上げるのがいちばん早い。

   ```bash
   gh release create db-latest --title "カタログDB" --notes "日次ビルドが使うDB。手動でのみ更新する。"
   gh release upload db-latest data/vndb_otome.db --clobber
   ```

   `rebuild-db.yml` を手動実行しても同じものが作られる。

配信元を変えると、公開されるのは **CIが生成したサイト**になる。
リポジトリの `docs/` は手元でビルドした結果のままなので、両者は日々ずれる。
手元で確認したいときは `python3 scripts/site_build.py` で作り直すこと。

## 設定

| ファイル | 内容 |
|---|---|
| `scripts/site_config.py` | サイト名・公開URL・リポジトリURL |
| `scripts/affiliate_config.py` | 各社のアフィリエイトID（未設定なら素のURLを生成） |
| `scripts/ja_labels.py` | タグ・キャラ属性の日本語訳（未収録の語は英語のまま） |

## 広告について

本サイトはアフィリエイト広告を利用しています。購入リンクを経由して商品が購入された場合、
運営者が紹介料を受け取ることがあります。この旨は全ページに常時表示しています。

## 収録範囲と既知の制約

- 対象は**家庭用ゲーム機で遊べる日本語の乙女ゲーム 637作品**
- 声優情報があるのは全4,103作品中982作品。ただし**家庭用機に限れば91%**が揃っています
- 残りの多くは「そもそも音声のないゲーム」で、データ欠落ではありません
- タグ・キャラ属性はVNDB由来のため英語ベースです。頻出350語に訳語を当てていますが、未訳の語は英語のまま表示されます
- 価格を機械的に取得できるのは楽天ウェブサービス経由（新品／駿河屋楽天市場店の中古）のみです
