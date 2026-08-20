#!/usr/bin/env bash
# 大きな変更の前に必ず実行するバックアップスクリプト
#
#   ./scripts/backup.sh "かな検索の作り直し"
#
# 保存するもの:
#   - data/            … DB・jsonl・xlsx・site用JSON（git管理外なので消えたら復旧不能）
#   - scripts/         … 生成ロジック
#   - docs/ の設定系   … _headers / robots.txt / sitemap.xml など手書きファイル
#   - MANIFEST.txt     … その時点の git コミットハッシュとブランチ
# docs/ のHTML本体は git 管理下＆再生成可能なので tar には含めない
# （MANIFEST のコミットハッシュから git 側で復元する）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LABEL="${1:-manual}"
SLUG="$(printf '%s' "$LABEL" | tr ' /' '__' | tr -cd '[:alnum:]_ぁ-んァ-ヶ一-龠ー')"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="backups/${STAMP}_${SLUG}.tar.gz"
KEEP="${BACKUP_KEEP:-10}"

mkdir -p backups

# --- 1. マニフェスト（何を戻せばいいかの手掛かり） ---
MANIFEST="$(mktemp)"
trap 'rm -f "$MANIFEST"' EXIT
{
  echo "label:   $LABEL"
  echo "date:    $(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "branch:  $(git rev-parse --abbrev-ref HEAD)"
  echo "commit:  $(git rev-parse HEAD)"
  echo "未コミット: $(git status --porcelain --untracked-files=no | wc -l | tr -d ' ') 件（追跡中）/ $(git ls-files --others --exclude-standard | wc -l | tr -d ' ') 件（未追跡）"
  echo ""
  echo "--- git status ---"
  git status --porcelain
} > "$MANIFEST"

# --- 2. アーカイブ作成 ---
cp "$MANIFEST" ./MANIFEST.txt
tar -czf "$OUT" \
  --exclude='.DS_Store' \
  --exclude='__pycache__' \
  MANIFEST.txt \
  data \
  scripts \
  $(ls docs/_headers docs/robots.txt docs/sitemap.xml 2>/dev/null || true)
rm -f ./MANIFEST.txt

# --- 3. git 側にも戻れる目印を残す（GitHubに push される） ---
TAG="backup/${STAMP}"
if [ -z "$(git status --porcelain --untracked-files=no)" ]; then
  git tag -a "$TAG" -m "backup: $LABEL" >/dev/null
  echo "git tag:  $TAG （push は  git push origin $TAG ）"
else
  echo "git tag:  未作成（未コミットの変更あり。先に commit してから再実行するとタグも打てます）"
fi

# --- 4. 古いバックアップの整理（最新 $KEEP 件を残す） ---
ls -1t backups/*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  echo "削除:     $old （保持数 $KEEP 超過）"
  rm -f "$old"
done

echo "バックアップ完了: $OUT  ($(du -h "$OUT" | cut -f1))"
