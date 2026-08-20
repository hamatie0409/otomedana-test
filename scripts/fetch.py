"""item_ids.txt のIDを1件ずつ取得して raw/item/ に保存する。

  python3 scripts/fetch.py --limit 10   # 先頭10件だけ（テスト用）
  python3 scripts/fetch.py              # 全件
取得済みのIDはスキップするので、中断しても再実行で続きから進む。
"""
import os, sys
from common import BASE, DATA, RAW_ITEM, get, is_real_item, raw_path

def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    ids = [int(l) for l in open(os.path.join(DATA, "item_ids.txt")) if l.strip()]
    todo = [i for i in ids if not os.path.exists(raw_path(i))]
    if limit:
        todo = todo[:limit]
    print("対象 %d件 / 全 %d件（取得済み %d件）" % (len(todo), len(ids), len(ids) - len([i for i in ids if not os.path.exists(raw_path(i))])))

    os.makedirs(RAW_ITEM, exist_ok=True)
    failed = []
    for n, i in enumerate(todo, 1):
        try:
            html = get("%s/item/%d" % (BASE, i))
        except Exception as e:
            print("  [%d/%d] id=%d 失敗: %s" % (n, len(todo), i, e))
            failed.append(i)
            continue
        if not is_real_item(html):
            print("  [%d/%d] id=%d 中身なし(ソフト404) — スキップ" % (n, len(todo), i))
            continue
        with open(raw_path(i), "w", encoding="utf-8") as f:
            f.write(html)
        if n % 25 == 0 or n == len(todo):
            print("  [%d/%d] id=%d 保存" % (n, len(todo), i))

    if failed:
        p = os.path.join(DATA, "failed.txt")
        with open(p, "w") as f:
            f.write("\n".join(map(str, failed)) + "\n")
        print("失敗 %d件 -> %s" % (len(failed), p))
    print("完了")

if __name__ == "__main__":
    main()
