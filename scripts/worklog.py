# -*- coding: utf-8 -*-
"""作業ログ WORKLOG.md への追記（標準ライブラリのみ）。

    python3 scripts/worklog.py "かな検索に対応した"
    python3 scripts/worklog.py "かな検索に対応した" --why "英語タイトルが引けなかった" \
                                                    --next "カナ表記ゆれの吸収"

区切りのいい作業をしたら必ず 1 行残す。後から「なぜこうなっているのか」を
git log だけで辿るのは辛いので、意図と次の一手をここに書く。

新しい日付が上に来るように追記する。日時・ブランチ・直近のコミットは自動で入る。
"""
import os, sys, subprocess, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "WORKLOG.md")

HEADER = """# 作業ログ

新しい順。区切りのいい作業をしたら 1 件残す。

    python3 scripts/worklog.py "やったこと" --why "なぜ" --next "次にやること"

コミットメッセージが「何を変えたか」なら、こちらは「なぜそうしたか」と
「次に何をするつもりだったか」を残す場所。
"""


def git(*args):
    try:
        r = subprocess.run(("git",) + args, cwd=ROOT, capture_output=True, text=True)
        return r.stdout.strip()
    except OSError:
        return ""


def parse_args(argv):
    """位置引数 1 つと --why / --next を拾う。"""
    what, why, nxt = None, None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--why", "--next") and i + 1 < len(argv):
            if a == "--why":
                why = argv[i + 1]
            else:
                nxt = argv[i + 1]
            i += 2
            continue
        if what is None:
            what = a
        i += 1
    return what, why, nxt


def main():
    what, why, nxt = parse_args(sys.argv[1:])
    if not what:
        sys.exit('使い方: python3 scripts/worklog.py "やったこと" '
                 '[--why "なぜ"] [--next "次にやること"]')

    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    branch = git("rev-parse", "--abbrev-ref", "HEAD") or "-"
    commit = (git("rev-parse", "--short", "HEAD") or "-")

    lines = ["### %s %s" % (now.strftime("%H:%M"), what)]
    if why:
        lines.append("- なぜ: %s" % why)
    if nxt:
        lines.append("- 次: %s" % nxt)
    # 記録はコミットの前に書くので、ここの HEAD は「この作業の 1 つ前」になる。
    # この作業自体のコミットは、WORKLOG.md を一緒にコミットすれば
    # git log -p WORKLOG.md で辿れる。
    lines.append("- ブランチ: %s（記録時 HEAD: %s）" % (branch, commit))
    entry = "\n".join(lines) + "\n"

    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8") as f:
            body = f.read()
    else:
        body = HEADER

    marker = "## %s\n" % today
    if marker in body:
        # 同じ日の見出しの直後に差し込む（その日の中でも新しいものが上）
        head, rest = body.split(marker, 1)
        body = head + marker + "\n" + entry + "\n" + rest.lstrip("\n")
    else:
        # 新しい日付は最初の日付見出しの前、なければ末尾に
        idx = body.find("\n## ")
        block = marker + "\n" + entry
        if idx == -1:
            body = body.rstrip("\n") + "\n\n" + block
        else:
            body = body[:idx + 1] + block + "\n" + body[idx + 1:]

    with open(LOG, "w", encoding="utf-8") as f:
        f.write(body)
    print("WORKLOG.md に追記しました:")
    for l in lines:
        print("  " + l)


if __name__ == "__main__":
    main()
