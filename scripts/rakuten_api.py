# -*- coding: utf-8 -*-
"""楽天ウェブサービスAPIの薄いクライアント（標準ライブラリのみ）。

2026年のインフラ刷新後の仕様に合わせてある。ネット上の解説はほぼ旧仕様なので注意。

  - エンドポイントは openapi.rakuten.co.jp
    （旧 app.rakuten.co.jp は 2026-05 に停止済み）
  - applicationId に加えて accessKey が必須
  - 1アプリIDにつき 1秒1リクエスト
  - affiliateId を渡すと、レスポンスに affiliateUrl が入る

取得結果は data/cache/rakuten/ に置く。楽天ウェブサービスの規約上、
価格・在庫の保存は24時間まで（それ以外は3ヶ月）なので既定のTTLは24時間。

疎通確認:
    python3 scripts/rakuten_api.py --selftest
    python3 scripts/rakuten_api.py --jan 4995506002930
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import affiliate_config as AF
from common import DATA

# --- エンドポイント（2026-08-21 に公式ドキュメントで確認） -------------------
ICHIBA_ITEM_SEARCH = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"
BOOKS_GAME_SEARCH = "https://openapi.rakuten.co.jp/services/api/BooksGame/Search/20170404"

CACHE_DIR = os.path.join(DATA, "cache", "rakuten")
PRICE_TTL = 24 * 3600        # 価格・在庫（規約上の上限）
DELAY = 1.1                  # 1秒1リクエスト。取りこぼしを避けて少し余裕を持たせる
TIMEOUT = 30
RETRIES = 3

_last = [0.0]


class RakutenError(RuntimeError):
    pass


class MissingCredentials(RakutenError):
    pass


def credentials():
    """(applicationId, accessKey, affiliateId) を返す。足りなければ例外。"""
    app_id = AF.RAKUTEN_APPLICATION_ID
    access_key = AF.RAKUTEN_ACCESS_KEY
    missing = [n for n, v in (("RAKUTEN_APPLICATION_ID", app_id),
                              ("RAKUTEN_ACCESS_KEY", access_key)) if not v]
    if missing:
        raise MissingCredentials(
            "楽天ウェブサービスの認証情報がありません: %s\n"
            "  https://webservice.rakuten.co.jp/ の「アプリID発行」→ 新規アプリ登録 で取得し、\n"
            "  環境変数に入れてください（既存アプリの編集では accessKey は出ません）:\n"
            "    export RAKUTEN_APPLICATION_ID=...\n"
            "    export RAKUTEN_ACCESS_KEY=pk_..." % "、".join(missing))
    return app_id, access_key, AF.RAKUTEN_AFFILIATE_ID


def _cache_path(endpoint, params):
    """認証情報を除いたパラメータでキャッシュキーを作る。
    アプリIDを変えてもキャッシュが無効にならないようにするため。"""
    key = endpoint + "?" + urllib.parse.urlencode(sorted(params.items()))
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest() + ".json")


def _sleep_for_rate_limit():
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()


def request(endpoint, params, ttl=PRICE_TTL, use_cache=True):
    """APIを1回叩いてJSONをdictで返す。TTL内ならキャッシュを使う。"""
    params = {k: v for k, v in params.items() if v not in (None, "")}
    path = _cache_path(endpoint, params)

    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    app_id, access_key, affiliate_id = credentials()
    q = dict(params)
    q["applicationId"] = app_id
    q["accessKey"] = access_key
    q.setdefault("formatVersion", 2)      # Items が素の配列で返る（入れ子が減る）
    if affiliate_id:
        q["affiliateId"] = affiliate_id

    url = endpoint + "?" + urllib.parse.urlencode(q)
    body = None
    for attempt in range(RETRIES):
        _sleep_for_rate_limit()
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                body = r.read().decode("utf-8")
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            # 400番台は投げ直しても直らない。ただし429（叩きすぎ）だけは待って再試行。
            if e.code == 429 or e.code >= 500:
                if attempt == RETRIES - 1:
                    raise RakutenError("HTTP %d: %s" % (e.code, detail))
                time.sleep(5 * (attempt + 1))
                continue
            raise RakutenError("HTTP %d: %s" % (e.code, detail))
        except (urllib.error.URLError, OSError) as e:
            if attempt == RETRIES - 1:
                raise RakutenError("接続失敗: %s" % e)
            time.sleep(3 * (attempt + 1))

    data = json.loads(body)
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
    return data


def ichiba_search(ttl=PRICE_TTL, use_cache=True, **params):
    """楽天市場商品検索API。keyword に JAN を渡すのが基本。

    よく使うパラメータ:
      keyword, hits(1-30), page(1-100), shopCode,
      sort('+itemPrice' / '-itemPrice' / 'standard'), minPrice, maxPrice

    ttl / use_cache は API に送らず、こちら側のキャッシュ制御にだけ使う。
    """
    params.setdefault("hits", 30)
    params.setdefault("sort", "+itemPrice")
    return request(ICHIBA_ITEM_SEARCH, params, ttl=ttl, use_cache=use_cache)


def books_game_search(ttl=PRICE_TTL, use_cache=True, **params):
    """楽天ブックスゲーム検索API。jan / title / hardware などで引ける。
    楽天ブックスの正規商品だけが返るので、発売日や限定版フラグが正確。
    """
    params.setdefault("hits", 30)
    return request(BOOKS_GAME_SEARCH, params, ttl=ttl, use_cache=use_cache)


def items_of(payload):
    """formatVersion=2 と 1 のどちらで返ってきても item の配列にそろえる。"""
    items = payload.get("Items") or []
    out = []
    for it in items:
        if isinstance(it, dict) and len(it) == 1 and "Item" in it:
            out.append(it["Item"])      # formatVersion=1
        else:
            out.append(it)
    return out


def _selftest():
    try:
        app_id, access_key, affiliate_id = credentials()
    except MissingCredentials as e:
        print(e)
        return 1
    print("applicationId : %s…（%d桁）" % (app_id[:6], len(app_id)))
    print("accessKey     : %s…" % access_key[:6])
    print("affiliateId   : %s" % (affiliate_id or "（未設定 — affiliateUrl は返りません）"))
    print()
    print("楽天市場商品検索APIに1回だけ問い合わせます…")
    data = ichiba_search(keyword="乙女ゲーム", hits=3, use_cache=False)
    items = items_of(data)
    print("  ヒット総数: %s件 / 取得 %d件" % (data.get("count"), len(items)))
    for it in items:
        print("  - %-9s円  %s" % (it.get("itemPrice"), (it.get("itemName") or "")[:48]))
        if it.get("affiliateUrl"):
            print("      affiliateUrl: %s" % it["affiliateUrl"][:80])
    if items and not items[0].get("affiliateUrl"):
        print()
        print("※ affiliateUrl が空です。RAKUTEN_AFFILIATE_ID を設定してください。")
    print()
    print("疎通OK")
    return 0


def main():
    if "--selftest" in sys.argv:
        return _selftest()
    if "--jan" in sys.argv:
        jan = sys.argv[sys.argv.index("--jan") + 1]
        data = ichiba_search(keyword=jan, hits=10)
        items = items_of(data)
        print("JAN %s → %s件" % (jan, data.get("count")))
        for it in items:
            print("  %-9s円  %-22s  %s" % (it.get("itemPrice"), (it.get("shopName") or "")[:22],
                                           (it.get("itemName") or "")[:44]))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
