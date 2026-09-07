"""otomex スクレイパ共通処理（標準ライブラリのみ）"""
import os, time, urllib.request, urllib.error

BASE = "http://otomex.net"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_ITEM = os.path.join(ROOT, "raw", "item")


def _data_dir():
    """data/ の場所。git worktree から動かしたときは本体側を辿る。

    data/ は .gitignore されているので worktree には現れない。ここを
    ROOT/data で決め打ちすると、worktree ではDBが見つからずビルドできない。
    .git がファイル（worktree の目印）なら、そこに書かれた gitdir から
    本体の作業ツリーを割り出す。
    """
    here = os.path.join(ROOT, "data")
    if os.path.isdir(here):
        return here
    dotgit = os.path.join(ROOT, ".git")
    if os.path.isfile(dotgit):
        try:
            with open(dotgit, encoding="utf-8") as f:
                gitdir = os.path.abspath(f.read().split(":", 1)[1].strip())
        except (OSError, IndexError):
            return here
        # gitdir は <本体>/.git/worktrees/<名前>。".git" まで遡って親を取る
        while gitdir != os.path.dirname(gitdir):
            if os.path.basename(gitdir) == ".git":
                cand = os.path.join(os.path.dirname(gitdir), "data")
                return cand if os.path.isdir(cand) else here
            gitdir = os.path.dirname(gitdir)
    return here


DATA = _data_dir()

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
