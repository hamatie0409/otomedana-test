# -*- coding: utf-8 -*-
"""データの検収。作品ページに出る情報が信用できるかを全数で調べる。

    python3 scripts/audit.py                 # 人が読むレポート
    python3 scripts/audit.py --json          # 機械可読（CI用）
    python3 scripts/audit.py --limit 40      # 要確認リストの表示件数
    python3 scripts/audit.py --strict        # 要確認が残っていても落とす

検査は性質の違う2種類に分けてある。混ぜると使えなくなる。

**構造の検査** … 満たされていなければバグ。違反が1件でもあれば異常終了する。
**結合の検査** … 白黒つかない。risk順に並べて人に見せ、判断は audit_ok.py に残す。

なぜ自動で弾かないか
--------------------
「作品名が商品名に含まれない」を不合格にすると、正しいものが大量に落ちる。
実測すると item リンク2,961行のうち584行が不一致だが、その中身は

  合本        華ヤカ哉 キネマモザイク → 幻燈ノスタルジィ（収録作品。買う手段はこれだけ）
  英題と邦題   Real Rode → リアルロデ / ビタミンX → VitaminX
  表記ゆれ     Collar x Malice → Collar×Malice
  廉価版      召しませ浪漫茶房 → SIMPLE2000シリーズVol.98 THE浪漫茶房
  移植版      薄桜鬼 新選組奇譚 → 薄桜鬼 ポータブル

と、ほとんどが正しい結び付きだった。弱い手がかりを足し合わせて順位を付け、
人が上から見るのが結局いちばん速い。
"""
import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from audit_ok import ACK, COMPILATION_OK
except ImportError:
    ACK, COMPILATION_OK = set(), {}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "vndb_otome.db")

PRICE_RATIO = 3.0      # 機種×状態の中央値の何倍から外れ値とみなすか
RISK_SHOW = 4          # これ以上を要確認として出す


def norm(s):
    """比較用に潰す。全半角・大文字小文字・記号・空白の違いを無視する"""
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[^0-9a-z぀-ヿ一-鿿]", "", s)


# ---------------------------------------------------------------- 構造の検査

def structure(con):
    """満たされていなければバグ。1件でもあれば異常終了する"""
    out = []

    def check(name, sql, hint):
        rows = con.execute(sql).fetchall()
        if rows:
            out.append({"name": name, "n": len(rows), "hint": hint,
                        "sample": [list(r) for r in rows[:5]]})

    check("同じ版・同じ販路・同じ状態に商品リンクが2つ以上",
          """SELECT eid, channel, condition, COUNT(*) FROM offers
             WHERE link_type='item' GROUP BY eid, channel, condition HAVING COUNT(*) > 1""",
          "apply_to_offers の UPDATE が複数行に当たっている")

    check("offers と editions で作品IDが食い違う",
          """SELECT o.eid, o.vid, e.vid FROM offers o JOIN editions e ON e.eid=o.eid
             WHERE o.vid <> e.vid""",
          "版の組み直しか offers の生成で作品の対応が壊れている")

    check("表紙が、その作品の版でない版から来ている",
          """SELECT s.vid, s.eid FROM shop_images s
             WHERE s.eid IS NOT NULL AND s.eid NOT IN
                   (SELECT eid FROM editions WHERE vid = s.vid)""",
          "shop_images.eid が別作品の版を指している")

    check("URLスラッグが重複",
          """SELECT kind, slug, COUNT(*) FROM slugs WHERE is_page=1
             GROUP BY kind, slug HAVING COUNT(*) > 1""",
          "別のページが同じURLを取り合う")

    check("価格があるのに在庫表示が無い",
          """SELECT eid, channel, price FROM offers
             WHERE link_type='item' AND price IS NOT NULL
               AND (availability IS NULL OR availability='')""",
          "site_build 側で在庫を見て出し分けているので表示が崩れる")

    check("商品リンクなのに URL が無い",
          """SELECT eid, channel FROM offers
             WHERE link_type='item' AND (url IS NULL OR url='')""",
          "リンク先が空のボタンが出る")

    return out


# ---------------------------------------------------------------- 結合の検査

def joins(con):
    """白黒つかないものを risk 順に並べる"""
    rows = con.execute("""
        SELECT o.eid, o.vid, o.channel, o.condition, o.price, o.item_code, o.item_name,
               g.title, e.rel_title, e.platform_ja, e.plat_group, e.n_vn, e.rid
        FROM offers o
        JOIN editions e ON e.eid = o.eid
        JOIN games g    ON g.vid = o.vid
        WHERE o.link_type='item'
    """).fetchall()

    # 同じ商品が何作品に付いているか
    per_item = {}
    for r in rows:
        per_item.setdefault(r["item_code"], set()).add(r["vid"])

    # 機種×状態ごとの値ごろ感。中央値から大きく離れたものを疑う
    buckets = {}
    for r in rows:
        if r["price"]:
            buckets.setdefault((r["plat_group"], r["condition"]), []).append(r["price"])
    median = {k: statistics.median(v) for k, v in buckets.items() if len(v) >= 5}

    found = []
    for r in rows:
        why, risk = [], 0

        n_vn = r["n_vn"] or 1
        if n_vn >= 2 and str(r["rid"]) not in COMPILATION_OK:
            why.append("合本（1つの商品に%d作品）" % n_vn)
            risk += 3

        shared = per_item.get(r["item_code"]) or set()
        if len(shared) >= 2:
            why.append("同じ商品が複数の作品に付いている（%d作品）" % len(shared))
            risk += 2

        med = median.get((r["plat_group"], r["condition"]))
        if med and r["price"] and r["price"] > med * PRICE_RATIO:
            why.append("価格が%s中央値から外れる（%.1f倍・¥%s / 中央値¥%d）"
                       % (r["condition"], r["price"] / med, r["price"], med))
            risk += 2

        t, rel, item = norm(r["title"]), norm(r["rel_title"]), norm(r["item_name"])
        if t and t not in rel:
            why.append("作品名がリリース名に無い")
            risk += 1
        if rel and rel[:14] not in item:
            why.append("リリース名が商品名に無い")
            risk += 1

        if not why:
            continue
        if "%s:%s" % (r["vid"], r["item_code"]) in ACK:
            continue
        found.append({"vid": r["vid"], "eid": r["eid"], "rid": r["rid"],
                      "title": r["title"], "rel_title": r["rel_title"],
                      "item_name": r["item_name"], "item_code": r["item_code"],
                      "channel": r["channel"], "condition": r["condition"],
                      "price": r["price"], "platform": r["platform_ja"],
                      "risk": risk, "why": why})

    found.sort(key=lambda x: (-x["risk"], -(x["price"] or 0)))
    return found, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--strict", action="store_true",
                    help="要確認が残っていても異常終了する（CIで回帰を止める用）")
    a = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("DBがありません: %s\n"
                 "  gh release download db-latest --pattern vndb_otome.db --dir data" % DB)

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    bugs = structure(con)
    todo, total = joins(con)
    high = [t for t in todo if t["risk"] >= RISK_SHOW]

    if a.json:
        print(json.dumps({"structure": bugs, "join_total": total,
                          "join_todo": len(todo), "join_high": len(high),
                          "items": todo}, ensure_ascii=False, indent=2))
    else:
        print("=== 構造の検査 ===")
        if not bugs:
            print("  違反なし")
        for b in bugs:
            print("  ✗ %s … %d件" % (b["name"], b["n"]))
            print("      %s" % b["hint"])
            for s in b["sample"]:
                print("      %s" % (s,))
        print()
        print("=== 結合の検査 ===")
        print("  商品リンク総数   %d行" % total)
        print("  要確認          %d行 / %d作品（うち risk>=%d が %d行）"
              % (len(todo), len({t['vid'] for t in todo}), RISK_SHOW, len(high)))
        print("  承認済み        %d件" % len(ACK))
        print()
        tally = {}
        for t in todo:
            for w in t["why"]:
                tally[w.split("（")[0]] = tally.get(w.split("（")[0], 0) + 1
        print("  理由別の内訳（1行が複数該当する）:")
        for k, v in sorted(tally.items(), key=lambda x: -x[1]):
            print("    %-28s %5d行" % (k, v))
        print()
        for t in todo[:a.limit]:
            print("  [risk %d] %s" % (t["risk"], t["title"]))
            print("      %s / %s / ¥%s" % (t["platform"], t["condition"], t["price"]))
            print("      商品: %s" % t["item_name"][:70])
            print("      → %s" % " / ".join(t["why"]))
            print("      承認するなら audit_ok.py の ACK に \"%s:%s\"" % (t["vid"], t["item_code"]))
            print()
        if len(todo) > a.limit:
            print("  ほか %d行（--limit で増やす / --json で全件）" % (len(todo) - a.limit))

    if bugs:
        sys.exit(1)
    if a.strict and high:
        sys.exit(1)


if __name__ == "__main__":
    main()
