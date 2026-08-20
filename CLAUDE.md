# otomegamedb

乙女ゲームのデータベースサイト。VNDB のダンプを加工して `docs/` に静的サイトを生成し、
GitHub Pages で配信する。

## 最重要ルール：大きな変更の前に必ずバックアップを取る

「大きな変更」＝ 以下のいずれかに当てはまるもの。

- `data/` の DB・jsonl・xlsx を作り直す／スキーマを変える
- `docs/` を丸ごと再生成する（`site_build.py` の全件実行など）
- サイトの構造を変える（URL・ディレクトリ構成・テンプレートの作り直し）
- 複数ファイルにまたがるリファクタリング

手順は 2 つ。**両方**やること。

```bash
# 1. データのバックアップ（data/ と scripts/ を tar.gz で backups/ へ）
./scripts/backup.sh "何をしようとしているか"
```

```bash
# 2. 作業用ブランチを切る（main を壊さない）
git switch -c feat/やること
```

`backup.sh` は同時に `backup/<日時>` の git タグも打つので、
「バックアップ時点のサイトの状態」も commit ハッシュで特定できる。

戻したくなったら:

```bash
./scripts/restore.sh
```

引数なしで一覧、`./scripts/restore.sh 20260820_144006` で復元。
復元前に現状も自動退避されるので、やり直しは効く。

## バージョン管理の方針（GitHub）

- `main` は常に「動くサイト」。直接大きな変更を積まない。
- 変更は作業用ブランチ → 動作確認 → `main` にマージ。
- コミットメッセージは日本語、何を変えたかを一行で。
- 節目では `git tag` を打ち、`git push origin --tags` で GitHub にも残す。
- push は明示的に頼まれたときだけ行う。

## リポジトリの構成

| パス | 内容 | git 管理 |
|---|---|---|
| `scripts/` | 取得・整形・DB構築・サイト生成のスクリプト | する |
| `docs/` | 生成された静的サイト（GitHub Pages の配信元） | する |
| `data/` | DB・jsonl・xlsx・サイト用 JSON | **しない**（要バックアップ） |
| `vndb/`, `raw/` | VNDB ダンプと中間データ（再取得可能） | しない |
| `backups/` | `backup.sh` の出力（最新 10 件を保持） | しない |

`data/` は git に入っていないので、消えたら再生成しか手がない。
だから「大きな変更の前のバックアップ」が効いてくる。

## よく使うコマンド

```bash
python3 scripts/vndb_build.py
```

```bash
python3 scripts/site_build.py
```
