# -*- coding: utf-8 -*-
"""【価格取得】Yahoo!ショッピング商品検索APIで、JANごとの実売価格と商品直リンクを取る。

  export YAHOO_CLIENT_ID=... YAHOO_VC_SID=... YAHOO_VC_PID=...
  python3 scripts/yahoo_prices.py --dry-run      # 何件叩くかだけ見る
  python3 scripts/yahoo_prices.py --limit 20     # 先に20件で試す
  python3 scripts/yahoo_prices.py                # 全件（1秒1リクエスト）
  python3 scripts/yahoo_prices.py --apply        # APIを叩かず offers に入れ直すだけ

なぜ楽天と別に要るのか
----------------------
楽天だけだと中古の相場が薄い。Yahoo!ショッピングは買取王子・メディアワールド・
ネットオフ・ブックオフ・らしんばん・駿河屋Yahoo!店といった中古専門店が厚く、
楽天では見えない価格帯が拾える（実測で Code:Realize が ¥484、
Collar×Malice が ¥599 など）。

**駿河屋の価格をここから取れるのが大きい。** 駿河屋本体には公式APIが無く、
規約上スクレイピングもできない。楽天市場店に続く2本目の正規の経路になる。
なおアニメイトはYahooに出店していないので、こちらは検索リンクのまま。

rakuten_prices.py と同じ作りにしてある。
  yahoo_items      … JANごとにヒットしたYahoo!ショッピングの商品
  yahoo_fetch_log  … いつどのJANを引いたか（叩き直しを避けるため）
取り込んだあと apply_to_offers() が offers に Yahoo の行を入れ直す。

アフィリエイトについて
----------------------
Yahoo!ショッピングAPIのアフィリエイトはバリューコマース経由のみ
（affiliate_type は "vc" 固定）。sid と pid から affiliate_id を組み立てて
渡すと、レスポンスの url がアフィリエイトリンクになる。
未設定なら素のURLが返るだけで、動作としては問題ない。

利用にあたって
--------------
Yahoo!デベロッパーネットワークのガイドラインでクレジット表示が必須。
サイトのフッターに「Webサービス by Yahoo! JAPAN」を出している
（site_build.py / site2_build.py）。改変してはいけないので触らないこと。
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA

DB = os.path.join(DATA, "vndb_otome.db")
ENDPOINT = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"

DELAY = 1.0                  # 1秒1リクエスト。楽天に合わせる
TIMEOUT = 20
RESULTS = 30                 # 1JANあたりの取得件数
MISS_TTL = 7 * 24 * 3600     # 0件だったJANは1週間置く
HIT_TTL = 24 * 3600          # 価格があるものは24時間で取り直す

CHANNEL = "Yahoo!ショッピング"

SCHEMA = """
CREATE TABLE IF NOT EXISTS yahoo_items (
  jan TEXT, code TEXT, name TEXT, price INTEGER,
  condition TEXT,            -- new / used
  in_stock INTEGER,
  seller_id TEXT, seller_name TEXT,
  url TEXT, image_url TEXT,
  fetched_at TEXT,
  PRIMARY KEY (jan, code)
);
CREATE INDEX IF NOT EXISTS idx_yi_jan  ON yahoo_items(jan);
CREATE INDEX IF NOT EXISTS idx_yi_cond ON yahoo_items(condition);

CREATE TABLE IF NOT EXISTS yahoo_fetch_log (
  jan TEXT PRIMARY KEY, fetched_at TEXT, n_hits INTEGER
);
"""


def env(name):
    v = os.environ.get(name, "").strip()
    if v:
        return v
    path = os.path.expanduser("~/.config/otomegamedb/env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8", errors="replace"):
            if line.strip().startswith("#"):
                continue
            k, _, val = line.replace("export ", "", 1).partition("=")
            if k.strip() == name:
                return val.strip().strip("\"'")
    return ""


def affiliate_params():
    """バリューコマースの sid / pid から affiliate_id を組み立てる。

    仕様は「MyLinkのURLに &vc_url= を付けたものをURLエンコードして渡す」。
    片方でも欠けていたらアフィリエイトなしで叩く（素のURLが返る）。
    """
    sid, pid = env("YAHOO_VC_SID"), env("YAHOO_VC_PID")
    if not (sid and pid):
        return {}
    base = "https://ck.jp.ap.valuecommerce.com/servlet/referral?sid=%s&pid=%s&vc_url=" % (sid, pid)
    return {"affiliate_type": "vc", "affiliate_id": base}


def fetch(jan, aff):
    """1JAN分を引く。戻り値は hits のリスト。404/0件なら空。"""
    q = dict(aff)
    q.update({"appid": env("YAHOO_CLIENT_ID"), "jan_code": jan, "results": RESULTS})
    url = ENDPOINT + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "otomegamedb/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return (json.loads(r.read().decode("utf-8")).get("hits")) or []
        except urllib.error.HTTPError as e:
            if e.code == 403:
                # 存在しない/許可されていないIDか、利用上限。叩き続けても無駄
                raise SystemExit(
                    "403 Forbidden。YAHOO_CLIENT_ID が正しいか、"
                    "1日の上限(50,000)を超えていないか確認してください")
            if attempt == 2:
                return []
            time.sleep(2 * (attempt + 1))
        except Exception:
            if attempt == 2:
                return []
            time.sleep(2 * (attempt + 1))
    return []


def targets(con, limit=None):
    """引く対象のJAN。価格を持っているものは24時間、0件だったものは1週間置く。"""
    rows = con.execute("""
        SELECT DISTINCT e.gtin AS jan
        FROM editions e JOIN games g ON g.vid = e.vid
        WHERE e.gtin IS NOT NULL AND e.gtin <> ''
        ORDER BY g.votecount DESC""").fetchall()
    log = {r[0]: (r[1], r[2]) for r in
           con.execute("SELECT jan, fetched_at, n_hits FROM yahoo_fetch_log")}
    now = time.time()
    todo = []
    for (jan,) in rows:
        prev = log.get(jan)
        if prev:
            try:
                age = now - time.mktime(time.strptime(prev[0], "%Y-%m-%d %H:%M:%S"))
            except (ValueError, TypeError):
                age = 1e9
            if age < (MISS_TTL if not prev[1] else HIT_TTL):
                continue
        todo.append(jan)
    return todo[:limit] if limit else todo


def apply_to_offers(con):
    """yahoo_items から offers の Yahoo 行を入れ直す。

    新品と中古で1行ずつ。同じJANの中でいちばん安い在庫ありを採る。
    表示順(priority)は Amazon や駿河屋と同じ1にする。楽天(0)の次。
    """
    con.execute("DELETE FROM offers WHERE channel = ?", (CHANNEL,))
    eds = con.execute("""
        SELECT eid, vid, gtin FROM editions
        WHERE gtin IS NOT NULL AND gtin <> ''""").fetchall()
    n = 0
    for eid, vid, jan in eds:
        for cond, label in (("new", "新品"), ("used", "中古")):
            row = con.execute("""
                SELECT code, name, price, url, image_url, seller_name, fetched_at
                FROM yahoo_items
                WHERE jan = ? AND condition = ? AND in_stock = 1 AND price > 0
                ORDER BY price LIMIT 1""", (jan, cond)).fetchone()
            if not row:
                continue
            con.execute("""
                INSERT INTO offers (eid, vid, channel, via, condition, link_type,
                                    key_type, key_value, url, item_code, item_name,
                                    image_url, price, availability, fetched_at, priority)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (eid, vid, CHANNEL, "", label, "item", "jan", jan,
                         row[3], row[0], row[1], row[4], row[2], "在庫あり", row[6], 1))
            n += 1
    con.commit()
    print("  offers に Yahoo の行を %d件 入れました" % n)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true", dest="dry")
    ap.add_argument("--apply", action="store_true",
                    help="APIを叩かず offers に入れ直すだけ")
    args = ap.parse_args()

    if not env("YAHOO_CLIENT_ID"):
        sys.exit("YAHOO_CLIENT_ID が未設定です")
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)

    if args.apply:
        return apply_to_offers(con)

    todo = targets(con, args.limit)
    aff = affiliate_params()
    print("対象JAN: %d件 / アフィリエイト: %s"
          % (len(todo), "あり(vc)" if aff else "なし（sid/pid未設定）"))
    if args.dry:
        return
    if not todo:
        print("  取り直しが必要なJANはありません")
        return apply_to_offers(con)

    now = lambda: time.strftime("%Y-%m-%d %H:%M:%S")
    hit = 0
    for i, jan in enumerate(todo, 1):
        time.sleep(DELAY)
        hits = fetch(jan, aff)
        for h in hits:
            s = h.get("seller") or {}
            con.execute("""INSERT OR REPLACE INTO yahoo_items
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (jan, h.get("code"), h.get("name"), h.get("price"),
                         h.get("condition"), 1 if h.get("inStock") else 0,
                         s.get("sellerId"), s.get("name"), h.get("url"),
                         (h.get("image") or {}).get("medium"), now()))
        con.execute("INSERT OR REPLACE INTO yahoo_fetch_log VALUES (?,?,?)",
                    (jan, now(), len(hits)))
        hit += bool(hits)
        if i % 100 == 0:
            con.commit()
            print("  ... %d/%d （価格が取れたJAN %d）" % (i, len(todo), hit), flush=True)
    con.commit()
    print("  取得完了: %d/%d のJANでヒット" % (hit, len(todo)))
    apply_to_offers(con)


if __name__ == "__main__":
    main()
