"""シリーズの判定。VNDBに「シリーズ」という実体はないため、
関連作品のグラフから連結成分を求めて1つのシリーズとみなす。

  from series import build_series
  series = build_series(con, scope_vids)
"""
import re
from collections import defaultdict

# シリーズとみなす関連の種別。「キャラ共通」は緩すぎて無関係な作品まで
# 繋がるので含めない（実測で最大27作品の塊ができた）
TYPES = {"続編", "前作", "同シリーズ", "本編", "サイドストーリー", "別バージョン", "同一設定"}

TRAIL = r"[\s　\-–—:：〜~・…,，。\(（\[【«\"'’”0-9IVXｰ]+$"
SEP = " 　-–—:：〜~・"


def series_name(names):
    """メンバーのタイトルからシリーズ名を決める。
    共通接頭辞が十分な長さと割合を持つときだけ採用し、そうでなければ最古のタイトル。"""
    if not names:
        return None
    p = names[0]
    for n in names[1:]:
        i = 0
        while i < min(len(p), len(n)) and p[i] == n[i]:
            i += 1
        p = p[:i]
    # 語の途中で切れていたら直前の区切りまで戻す
    if p and any(len(n) > len(p) and n[len(p)] not in SEP for n in names):
        m = re.search(r"^(.*)[%s]" % re.escape(SEP), p)
        if m:
            p = m.group(1)
    p = re.sub(TRAIL, "", p).strip()
    q = re.sub(r"[\s　]*[~〜][^\s　~〜]*$", "", p).strip()   # 末尾の「~副題」を落とす
    if len(q) >= 4:
        p = q
    short = min(len(n) for n in names)
    if len(p) >= 4 and len(p) / short >= 0.35:
        return p
    return names[0]


def build_series(con, scope):
    """scope（対象作品のvid集合）に2作品以上属するシリーズを返す。

    戻り値: {key: {"name": シリーズ名, "members": [vid...] 発売順, "latin": ローマ字}}
    key は最古メンバーのvid。安定していて衝突しない。
    """
    info = {r[0]: (r[1], r[2], r[3]) for r in
            con.execute("SELECT vid, title, released, title_latin FROM games")}
    adj = defaultdict(set)
    for vid, rv, t in con.execute("SELECT vid, related_vid, type FROM relations"):
        if t in TYPES and vid in info and rv in info:
            adj[vid].add(rv)
            adj[rv].add(vid)

    seen, out = set(), {}
    for v in info:
        if v in seen:
            continue
        stack, comp = [v], set()
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.add(x)
            stack.extend(adj[x] - seen)
        members = sorted(comp & scope, key=lambda x: (info[x][1] or "9999", x))
        if len(members) < 2:
            continue
        names = [info[m][0] for m in members if info[m][0]]
        latins = [info[m][2] or info[m][0] for m in members]
        out[members[0]] = {
            "name": series_name(names),
            "latin": series_name([l for l in latins if l]) if latins else None,
            "members": members,
        }
    return out
