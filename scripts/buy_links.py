"""【フェーズ0】VNDBダンプから購入導線の材料を抽出し、vndb_otome.db に追加する。

  python3 scripts/vndb_export.py   # 先にこちらを実行（テーブルを作り直すため）
  python3 scripts/buy_links.py

作るテーブル:
  gtins       … JANコード（楽天・Amazon・駿河屋の検索キー）
  vndb_links  … VNDBが持っている外部リンク（公式サイト等）
"""
import os, sqlite3
from collections import defaultdict
from common import DATA, ROOT

DB_DUMP = os.path.join(ROOT, "vndb", "db")

# VNDBの外部リンクをURLに戻すためのテンプレート
URL_TEMPLATE = {
    "website": "%s",
    "animateg": "https://www.animategames.jp/home/detail/%s",   # ブランドの作品ページ（ショップではない）
    "steam": "https://store.steampowered.com/app/%s/",
    "dlsite": "https://www.dlsite.com/home/work/=/product_id/%s.html",
    "getchu": "https://www.getchu.com/soft.phtml?id=%s",
    "playasia": "https://www.play-asia.com/%s",
    "nintendo_jp": "https://store-jp.nintendo.com/list/software/%s.html",
    "booth": "https://booth.pm/ja/items/%s",
    "digiket": "https://www.digiket.com/work/show/_data/ID=ITM%s/",
    "melonjp": "https://www.melonbooks.co.jp/detail/detail.php?product_id=%s",
    "toranoana": "https://ec.toranoana.jp/tora_r/ec/item/%s/",
}

SCHEMA = """
DROP TABLE IF EXISTS gtins;
DROP TABLE IF EXISTS vndb_links;
CREATE TABLE gtins (
    vid TEXT REFERENCES games(vid), rid TEXT, gtin TEXT,
    platforms TEXT, released TEXT, official INTEGER
);
CREATE TABLE vndb_links (
    vid TEXT REFERENCES games(vid), rid TEXT, site TEXT, value TEXT, url TEXT,
    platforms TEXT
);
CREATE INDEX idx_gtin_vid ON gtins(vid);
CREATE INDEX idx_gtin_code ON gtins(gtin);
CREATE INDEX idx_vl_vid ON vndb_links(vid);
CREATE INDEX idx_vl_site ON vndb_links(site);
"""


def unesc(v):
    if v == "\\N":
        return None
    if "\\" in v:
        v = (v.replace("\\t", "\t").replace("\\n", "\n")
              .replace("\\r", "\r").replace("\\\\", "\\"))
    return v


def read(table):
    path = os.path.join(DB_DUMP, table)
    cols = open(path + ".header", encoding="utf-8").read().rstrip("\n").split("\t")
    with open(path, encoding="utf-8") as f:
        for line in f:
            yield dict(zip(cols, (unesc(v) for v in line.rstrip("\n").split("\t"))))


def main():
    db = os.path.join(DATA, "vndb_otome.db")
    con = sqlite3.connect(db)
    vids = {r[0] for r in con.execute("SELECT vid FROM games")}
    print("対象作品: %d件" % len(vids))

    # リリース → 作品
    rel2vn = defaultdict(list)
    for r in read("releases_vn"):
        if r["vid"] in vids:
            rel2vn[r["id"]].append(r["vid"])
    print("対象リリース: %d件" % len(rel2vn))

    # リリースの機種
    relplat = defaultdict(list)
    for r in read("releases_platforms"):
        if r["id"] in rel2vn:
            relplat[r["id"]].append(r["platform"])

    con.executescript(SCHEMA)

    # --- JANコード ---
    n_gtin = 0
    for r in read("releases"):
        if r["id"] not in rel2vn:
            continue
        g = r["gtin"]
        if not g or g == "0":
            continue
        plat = " / ".join(sorted(relplat.get(r["id"], [])))
        for vid in rel2vn[r["id"]]:
            con.execute("INSERT INTO gtins VALUES (?,?,?,?,?,?)",
                        (vid, r["id"], g, plat, r["released"],
                         1 if r["official"] == "t" else 0))
            n_gtin += 1

    # --- 外部リンク ---
    ext = {}
    for r in read("extlinks"):
        ext[r["id"]] = (r["site"], r["value"])

    n_link = 0
    for r in read("releases_extlinks"):
        if r["id"] not in rel2vn:
            continue
        site, value = ext.get(r["link"], (None, None))
        if not site:
            continue
        tpl = URL_TEMPLATE.get(site)
        url = (tpl % value) if tpl and value else None
        if url and not url.startswith("http"):
            url = "https://" + url
        plat = " / ".join(sorted(relplat.get(r["id"], [])))
        for vid in rel2vn[r["id"]]:
            con.execute("INSERT INTO vndb_links VALUES (?,?,?,?,?,?)",
                        (vid, r["id"], site, value, url, plat))
            n_link += 1

    con.commit()

    # --- 結果 ---
    q = lambda s: con.execute(s).fetchone()[0]
    print()
    print("gtins      %6d行 / %d作品" % (n_gtin, q("SELECT COUNT(DISTINCT vid) FROM gtins")))
    print("vndb_links %6d行 / %d作品" % (n_link, q("SELECT COUNT(DISTINCT vid) FROM vndb_links")))
    print()
    home = ("Switch", "PS", "ニンテンドー", "Xbox")
    cond = " OR ".join("g.platforms LIKE '%%%s%%'" % k for k in home)
    n_home = q("SELECT COUNT(*) FROM games g WHERE %s" % cond)
    n_home_gtin = q("""SELECT COUNT(DISTINCT g.vid) FROM games g JOIN gtins t ON t.vid=g.vid
                       WHERE %s""" % cond)
    print("家庭用機 %d件 のうち JANあり %d件 (%.0f%%)"
          % (n_home, n_home_gtin, 100.0 * n_home_gtin / n_home))
    con.close()
    print("-> %s" % db)


if __name__ == "__main__":
    main()
