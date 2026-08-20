"""sitemap.xml から /item/ のIDを集め、必要なら最大ID以降を上方向に走査する。

  python3 scripts/collect_ids.py           # sitemapのみ
  python3 scripts/collect_ids.py --probe   # sitemap以降のIDも探索
"""
import os, re, sys
from common import BASE, DATA, get, is_real_item

MISS_LIMIT = 30   # 連続空振りがこの数に達したら打ち切り

def main():
    probe = "--probe" in sys.argv
    xml = get(BASE + "/sitemap.xml")
    ids = sorted({int(m) for m in re.findall(r"<loc>%s/item/(\d+)</loc>" % re.escape(BASE), xml)})
    print("sitemap: %d件 (min=%d, max=%d)" % (len(ids), ids[0], ids[-1]))

    if probe:
        miss = 0
        n = ids[-1] + 1
        while miss < MISS_LIMIT:
            html = get("%s/item/%d" % (BASE, n))
            if is_real_item(html):
                ids.append(n)
                miss = 0
                print("  +%d 実在" % n)
            else:
                miss += 1
            n += 1
        print("走査終了: %d まで確認、合計 %d件" % (n - 1, len(ids)))

    os.makedirs(DATA, exist_ok=True)
    out = os.path.join(DATA, "item_ids.txt")
    with open(out, "w") as f:
        f.write("\n".join(str(i) for i in sorted(set(ids))) + "\n")
    print("-> %s" % out)

if __name__ == "__main__":
    main()
