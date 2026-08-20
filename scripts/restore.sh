#!/usr/bin/env bash
# バックアップの一覧表示と復元
#
#   ./scripts/restore.sh                       … 一覧を見る
#   ./scripts/restore.sh 20260820_144006       … そのバックアップを復元
#
# 復元は data/ と scripts/ を上書きします。実行前に現状を
# backups/ に自動退避してから展開するので、やり直しは効きます。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ $# -eq 0 ]; then
  echo "バックアップ一覧（新しい順）:"
  echo
  for f in $(ls -1t backups/*.tar.gz 2>/dev/null); do
    printf '  %-46s %6s\n' "$(basename "$f")" "$(du -h "$f" | cut -f1)"
    tar -xzOf "$f" MANIFEST.txt 2>/dev/null | sed -n '1,5p' | sed 's/^/      /' \
      || echo "      （MANIFEST なしの旧バックアップ）"
    echo
  done
  echo "復元:  ./scripts/restore.sh <スタンプ>   例) ./scripts/restore.sh 20260820_144006"
  exit 0
fi

STAMP="$1"
ARCHIVE="$(ls -1 backups/*"$STAMP"*.tar.gz 2>/dev/null | head -1 || true)"
[ -n "$ARCHIVE" ] || { echo "見つかりません: $STAMP" >&2; exit 1; }

echo "復元元: $ARCHIVE"
tar -xzOf "$ARCHIVE" MANIFEST.txt | sed -n '1,5p' | sed 's/^/  /'
echo
read -r -p "data/ と scripts/ を上書きします。よろしいですか? [y/N] " ans
[ "$ans" = "y" ] || [ "$ans" = "Y" ] || { echo "中止しました"; exit 1; }

# 念のため今の状態を退避
"$ROOT/scripts/backup.sh" "restore前の自動退避" >/dev/null 2>&1 || true

tar -xzf "$ARCHIVE" -C "$ROOT" data scripts
echo "復元しました。"
echo
echo "サイト側（docs/）を当時に戻す場合は MANIFEST の commit を使ってください:"
tar -xzOf "$ARCHIVE" MANIFEST.txt | grep '^commit:' | sed 's/^commit: */  git checkout /'
