"""otomex スクレイパ共通処理（標準ライブラリのみ）"""
import os, time, urllib.request, urllib.error

BASE = "http://otomex.net"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_ITEM = os.path.join(ROOT, "raw", "item")
DATA = os.path.join(ROOT, "data")

DELAY = 1.5      # リクエスト間隔（秒）
TIMEOUT = 30

_last = [0.0]


def get(url, retries=3):
    """レート制限つきGET。本文をstrで返す。失敗時は例外。"""
    for attempt in range(retries):
        wait = DELAY - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError) as e:
            if attempt == retries - 1:
                raise
            time.sleep(3 * (attempt + 1))


def is_real_item(html):
    """ソフト404の判定。存在しないIDでも200が返るため中身で見る。"""
    return '<h1 id="incommon">' in html


def raw_path(item_id):
    return os.path.join(RAW_ITEM, "%d.html" % item_id)
