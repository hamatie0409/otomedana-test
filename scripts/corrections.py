# -*- coding: utf-8 -*-
"""作品情報の訂正・追記を DB に適用する（標準ライブラリのみ）。

    python3 scripts/corrections.py            # 検証だけ（DBは触らない）
    python3 scripts/corrections.py --apply    # DBに適用する
    python3 scripts/corrections.py --diff     # いまのDBの値と訂正値を並べて見る

なぜファイルに書くのか
----------------------
`data/` は git 管理外で、`rebuild-db.yml` を回すとカタログは VNDB のダンプから
作り直される。**DBに直接書いた訂正はそこで消える。**
訂正は `corrections/works.csv`（git 管理下）に書き、ビルドのたびにここで流し込む。

出典を必須にする理由
--------------------
「なぜこの値なのか」を後から辿れないと、次に見た人が正しいものを間違いだと
思って戻してしまう。値・出典URL・確認日の3点が揃わない行は受け付けない。
ODbL 4.4（改変の開示）も、この形なら自然に満たせる。

適用したものは `corrections_log` に残す。サイト側で「VNDB由来 / 手で訂正」を
出し分けたり、`audit.py` から出典URLの死活を見たりするのに使う。
"""
import argparse
import csv
import datetime
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "vndb_otome.db")
CSV_PATH = os.path.join(ROOT, "corrections", "works.csv")
CHAR_CSV = os.path.join(ROOT, "corrections", "characters.csv")

# 触ってよい列だけを列挙する。games に無い列は ALTER で足す
FIELDS = {
    "released":       "発売日",
    "title":          "作品名",
    "publishers":     "発売元",
    "developers":     "開発元",
    "staff":          "スタッフ",
    "description_ja": "日本語のあらすじ",
    "jawiki_url":     "日本語Wikipedia",
    "url":            "公式サイト",
}
# games に元から無く、訂正のために足す列
ADDED = {"description_ja": "TEXT"}

# キャラクター側で触ってよい列。sex は表示で「攻略対象」と「主要キャラ」を
# 出し分けるのに使うので、値は DB と同じ m / f だけを受け付ける
CHAR_FIELDS = {"cv": "声優", "name": "キャラクター名", "role": "役割",
               "sex": "性別"}
SEX_VALUES = ("m", "f")

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
URL = re.compile(r"^https?://", re.I)


def read_rows():
    """CSVを読む。# で始まる行と空行は飛ばす"""
    if not os.path.exists(CSV_PATH):
        sys.exit("訂正ファイルがありません: %s" % CSV_PATH)
    with open(CSV_PATH, encoding="utf-8") as f:
        lines = [l for l in f if l.strip() and not l.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def validate(rows, con):
    """1行ずつ検証する。1件でも落ちたら適用しない（中途半端に入るほうが困る）"""
    vids = {r[0] for r in con.execute("SELECT vid FROM games")}
    errs, ok = [], []
    seen = set()
    for i, r in enumerate(rows, 2):
        vid = (r.get("vid") or "").strip()
        field = (r.get("field") or "").strip()
        value = (r.get("value") or "").strip()
        src = (r.get("source_url") or "").strip()
        when = (r.get("checked_at") or "").strip()

        def bad(msg):
            errs.append("  %s行目 %s/%s … %s" % (i, vid or "?", field or "?", msg))

        if not vid:
            bad("vid が空")
        elif vid not in vids:
            bad("その vid はDBに無い")
        if field not in FIELDS:
            bad("使えない field。使えるのは %s" % " ".join(FIELDS))
        if not value:
            bad("value が空（消したいなら別の手段を用意すること）")
        if not URL.match(src):
            bad("source_url が無いか http(s) で始まらない")
        if not DATE.match(when):
            bad("checked_at が YYYY-MM-DD でない")
        if field == "released" and value and not DATE.match(value):
            bad("released は YYYY-MM-DD で書く")
        key = (vid, field)
        if key in seen:
            bad("同じ vid と field が2回出てくる")
        seen.add(key)

        if not [e for e in errs if e.startswith("  %s行目" % i)]:
            ok.append({"vid": vid, "field": field, "value": value,
                       "source_url": src, "checked_at": when,
                       "note": (r.get("note") or "").strip()})
    return ok, errs


def read_char_rows():
    if not os.path.exists(CHAR_CSV):
        return []
    with open(CHAR_CSV, encoding="utf-8") as f:
        lines = [l for l in f if l.strip() and not l.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def validate_chars(rows, con):
    """キャラクター側の検証。作品と同じく、1件でも落ちたら何も適用しない"""
    errs, ok, seen = [], [], set()
    for i, r in enumerate(rows, 2):
        vid = (r.get("vid") or "").strip()
        who = (r.get("character") or "").strip()
        field = (r.get("field") or "").strip()
        value = (r.get("value") or "").strip()
        src = (r.get("source_url") or "").strip()
        when = (r.get("checked_at") or "").strip()

        def bad(msg):
            errs.append("  %s行目 %s/%s … %s" % (i, vid or "?", who or "?", msg))

        cid = (r.get("cid") or "").strip()
        # 同じ名前のキャラが作品内に複数いることがある（VNDB側の重複や同姓同名）。
        # そのときは cid 列で1人に絞る
        if cid:
            n = con.execute("SELECT COUNT(*) FROM characters WHERE vid=? AND cid=?",
                            (vid, cid)).fetchone()[0] if vid else 0
            if vid and n == 0:
                bad("その作品にその cid のキャラクターがいない")
        else:
            n = con.execute("SELECT COUNT(*) FROM characters WHERE vid=? AND name=?",
                            (vid, who)).fetchone()[0] if vid and who else 0
        if not vid:
            bad("vid が空")
        elif n == 0 and not cid:
            bad("その作品にそのキャラクターがいない")
        elif n > 1:
            bad("同じ名前のキャラクターが%d人いる。cid 列で1人に絞ること" % n)
        if field not in CHAR_FIELDS:
            bad("使えない field。使えるのは %s" % " ".join(CHAR_FIELDS))
        if not value:
            bad("value が空")
        elif field == "sex" and value not in SEX_VALUES:
            bad("sex は %s のどちらか（DBに入っている値そのまま）" % " / ".join(SEX_VALUES))
        if not URL.match(src):
            bad("source_url が無いか http(s) で始まらない")
        if not DATE.match(when):
            bad("checked_at が YYYY-MM-DD でない")
        key = (vid, who, field)
        if key in seen:
            bad("同じ vid・キャラ・field が2回出てくる")
        seen.add(key)
        if not [e for e in errs if e.startswith("  %s行目" % i)]:
            ok.append({"vid": vid, "character": who, "cid": cid, "field": field,
                       "value": value, "source_url": src, "checked_at": when,
                       "note": (r.get("note") or "").strip()})
    return ok, errs


def ensure_columns(con):
    have = {d[1] for d in con.execute("PRAGMA table_info(games)")}
    for col, typ in ADDED.items():
        if col not in have:
            con.execute("ALTER TABLE games ADD COLUMN %s %s" % (col, typ))
    con.execute("""CREATE TABLE IF NOT EXISTS char_corrections_log (
        vid TEXT, character TEXT, field TEXT, old_value TEXT, new_value TEXT,
        source_url TEXT, checked_at TEXT, note TEXT, applied_at TEXT,
        PRIMARY KEY (vid, character, field))""")
    con.execute("""CREATE TABLE IF NOT EXISTS corrections_log (
        vid TEXT, field TEXT, old_value TEXT, new_value TEXT,
        source_url TEXT, checked_at TEXT, note TEXT, applied_at TEXT,
        PRIMARY KEY (vid, field))""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--diff", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(DB):
        sys.exit("DBがありません: %s" % DB)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    rows = read_rows()
    ok, errs = validate(rows, con)
    crows = read_char_rows()
    cok, cerrs = validate_chars(crows, con)
    errs = errs + cerrs

    print("訂正ファイル: %s" % os.path.relpath(CSV_PATH, ROOT))
    print("  作品      読めた行 %d / 通った行 %d" % (len(rows), len(ok)))
    print("  キャラクター 読めた行 %d / 通った行 %d" % (len(crows), len(cok)))
    print("  弾かれた行 %d" % len(errs))
    if errs:
        print()
        print("=== 直すまで適用しません ===")
        for e in errs:
            print(e)
        sys.exit(1)
    if not ok and not cok:
        print("  適用するものはありません")
        return

    ensure_columns(con)
    n_same = n_change = 0
    for r in ok:
        cur = con.execute("SELECT %s v FROM games WHERE vid=?" % r["field"],
                          (r["vid"],)).fetchone()
        old = cur["v"] if cur else None
        if (old or "") == r["value"]:
            n_same += 1
            continue
        n_change += 1
        if a.diff or not a.apply:
            t = con.execute("SELECT title FROM games WHERE vid=?", (r["vid"],)).fetchone()
            print()
            print("  %s %s（%s）" % (r["vid"], t["title"] if t else "?", FIELDS[r["field"]]))
            print("    いま: %s" % ((old or "(空)")[:70]))
            print("    訂正: %s" % r["value"][:70])
            print("    出典: %s（%s 確認）" % (r["source_url"], r["checked_at"]))
        if a.apply:
            con.execute("UPDATE games SET %s=? WHERE vid=?" % r["field"],
                        (r["value"], r["vid"]))
            con.execute("""INSERT OR REPLACE INTO corrections_log
                VALUES (?,?,?,?,?,?,?,?)""",
                        (r["vid"], r["field"], old, r["value"], r["source_url"],
                         r["checked_at"], r["note"],
                         datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    # ---- キャラクター側 ----
    c_change = c_same = 0
    for r in cok:
        where, arg = ("cid=?", r["cid"]) if r["cid"] else ("name=?", r["character"])
        cur = con.execute("SELECT %s v FROM characters WHERE vid=? AND %s"
                          % (r["field"], where), (r["vid"], arg)).fetchone()
        old = cur["v"] if cur else None
        if (old or "") == r["value"]:
            c_same += 1
            continue
        c_change += 1
        if a.diff or not a.apply:
            t = con.execute("SELECT title FROM games WHERE vid=?", (r["vid"],)).fetchone()
            print()
            print("  %s %s / %s（%s）"
                  % (r["vid"], t["title"] if t else "?", r["character"],
                     CHAR_FIELDS[r["field"]]))
            print("    いま: %s" % ((old or "(空)")[:70]))
            print("    訂正: %s" % r["value"][:70])
            print("    出典: %s（%s 確認）" % (r["source_url"], r["checked_at"]))
        if a.apply:
            con.execute("UPDATE characters SET %s=? WHERE vid=? AND %s"
                        % (r["field"], where), (r["value"], r["vid"], arg))
            con.execute("INSERT OR REPLACE INTO char_corrections_log VALUES (?,?,?,?,?,?,?,?,?)",
                        (r["vid"], r["character"], r["field"], old, r["value"],
                         r["source_url"], r["checked_at"], r["note"],
                         datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    if a.apply:
        con.commit()
    print()
    print("  作品      値が変わる %d件 / 既に同じ %d件" % (n_change, n_same))
    print("  キャラクター 値が変わる %d件 / 既に同じ %d件" % (c_change, c_same))
    print("  %s" % ("DBに適用しました" if a.apply else "適用するには --apply"))


if __name__ == "__main__":
    main()
