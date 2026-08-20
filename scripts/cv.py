"""声優名から出演作品を引く。キャラクター名からの逆引きもできる。

  python3 scripts/cv.py 鳥海浩輔          # 声優で引く
  python3 scripts/cv.py 鳥海浩輔 --csv    # CSVで出す（Excelに貼れる）
  python3 scripts/cv.py --char 風間千景    # キャラクター名から引く
名前は部分一致。姓名の間のスペースは無視される。
"""
import os, sys, sqlite3, unicodedata
from common import DATA

DB = os.path.join(DATA, "vndb_otome.db")


def norm(s):
    return unicodedata.normalize("NFKC", (s or "")).replace(" ", "").replace("　", "").lower()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_csv = "--csv" in sys.argv
    by_char = "--char" in sys.argv
    if not args:
        print(__doc__)
        return
    key = norm(args[0])
    col = "name" if by_char else "cv"
    con = sqlite3.connect(DB)

    # 候補の絞り込み（表記ゆれ対策でPython側で照合する）
    names = {}
    for (n,) in con.execute("SELECT DISTINCT %s FROM characters WHERE %s IS NOT NULL" % (col, col)):
        if key in norm(n):
            names[n] = True
    if not names:
        print("該当なし: %s" % args[0])
        return
    if len(names) > 1:
        print("候補が%d件あります: %s" % (len(names), " / ".join(sorted(names))))
        print()

    rows = con.execute("""
        SELECT g.released, g.title, c.name, c.role, g.platforms, g.vid
        FROM characters c JOIN games g ON g.vid = c.vid
        WHERE %s IN (%s)
        ORDER BY g.released DESC NULLS LAST
    """ % (col, ",".join("?" * len(names))), list(names)).fetchall()

    if as_csv:
        import csv
        w = csv.writer(sys.stdout)
        w.writerow(["発売日", "タイトル", "キャラクター", "区分", "機種", "VNDB"])
        for r in rows:
            w.writerow([r[0], r[1], r[2], r[3], r[4], "https://vndb.org/" + r[5]])
        return

    label = "キャラクター「%s」" % args[0] if by_char else "声優「%s」" % " / ".join(sorted(names))
    print("%s の出演作品: %d件" % (label, len(rows)))
    print("-" * 92)
    for rel, title, ch, role, plat, _ in rows:
        print("%-11s %-36s %-14s %-8s %s" % (
            rel or "-", (title or "")[:36], (ch or "")[:14], role or "", (plat or "")[:24]))


if __name__ == "__main__":
    main()
