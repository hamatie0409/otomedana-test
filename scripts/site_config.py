# -*- coding: utf-8 -*-
"""サイトの基本設定。公開先が決まったら SITE_URL を差し替えて再ビルドする。

SITE_URL は canonical・OGP・sitemap.xml・robots.txt に反映される。
公開後に変えると検索エンジンの評価がリセットされるので、公開前に確定させること。
"""

import os

SITE_NAME = "オトメ棚"
SITE_DESC = "乙女ゲームを声優・キャラクター属性・機種から探して、買えるお店へ。"

# ------------------------------------------------------------------
# 公開モード
#   False … テスト環境。noindex を付け、robots.txt で全面拒否し、
#            ページ上部に「テスト環境」と明示する。誤って公開されても
#            検索エンジンに拾われない。
#   True  … 一般公開。SITE_URL と REPO_URL を必ず実際の値にすること。
# ------------------------------------------------------------------
PUBLISH = False

# 末尾スラッシュなし。例: "https://otomedana.pages.dev"
SITE_URL = "https://example.pages.dev" if PUBLISH else "https://hamatie0409.github.io"

# サブディレクトリ配信のときだけ設定する（GitHub Pages のプロジェクトサイトなど）。
# ルート配信なら空文字。末尾スラッシュなし。
BASE_PATH = "" if PUBLISH else "/otomedana-test"

# ------------------------------------------------------------------
# 画像の出し方
#   "vndb"      … VNDBの画像サーバを直接参照する。確認しやすいが、
#                 VNDBは「個々の画像はフェアユース」という立場であって
#                 第三者への利用許諾ではない。日本にフェアユース規定は
#                 なく、VNDBの帯域も使うため公開には向かない。
#   "affiliate" … パッケージ画像はアフィリエイトAPIが返す画像URLのみ
#                 （楽天は「URLのまま表示」が規約上OK）。
#                 キャラクター画像は出さず、VNDBへのリンクに置き換える。
# ------------------------------------------------------------------
#
# 既定は "affiliate"。VNDB画像は公開できないので、既定にしておくと
# 「テストでは出ていたのに公開したら消えた」が起きる。
# 手元で確認したいときだけ環境変数で戻す:
#       IMAGE_MODE=vndb python3 scripts/site_build.py
# なお affiliate 画像は shop_images.py を実行しないと1枚も入らない。
IMAGE_MODE = os.environ.get("IMAGE_MODE") or "affiliate"

# ODbL 4.6（改変方法の提供義務）を満たすための公開リポジトリ
REPO_URL = "https://github.com/"

# 収録範囲の説明（トップとフッターに出す）
SCOPE_NOTE = "家庭用ゲーム機で遊べる日本語の乙女ゲームを収録しています。"


# ------------------------------------------------------------------
# 対象年齢の区分
#   VNDB の minage（0,3,6,10,12,13,15,16,17,18）は刻みが細かいので、
#   CERO（A/B/C/D/Z）の区切りに寄せてまとめる。
#   注意: minage は VNDB 独自の項目で、CERO の公式レーティングではない。
#   13 や 16 のように CERO に無い値も入っているため、表示は目安。
#   (値, 表示名, 下限, 上限)
# ------------------------------------------------------------------
AGE_TIERS = [
    ("0",  "全年齢（CERO A）",    0, 11),
    ("12", "12歳以上（CERO B）", 12, 14),
    ("15", "15歳以上（CERO C）", 15, 16),
    ("17", "17歳以上（CERO D）", 17, 17),
    ("18", "18歳以上（CERO Z）", 18, 99),
]

# 発売年の区分。この年以降は1年ずつ、それより前は10年ずつ（年代）でまとめる。
# 古い作品は年あたり数本しかなく、1年ずつ出すと空に近い選択肢が並ぶため。
YEAR_SINGLE_FROM = 2020


def age_tier(minage):
    """minage → 区分の値。未設定は空文字を返す"""
    if minage is None:
        return ""
    for key, _label, lo, hi in AGE_TIERS:
        if lo <= minage <= hi:
            return key
    return ""


def year_bucket(released):
    """発売日(ISO) → 区分の値（"2015" や "2001-2005"）。年が取れなければ空文字"""
    try:
        y = int((released or "")[:4])
    except ValueError:
        return ""
    if not y:
        return ""
    if y >= YEAR_SINGLE_FROM:
        return str(y)
    d = (y // 10) * 10
    return "%d-%d" % (d, d + 9)


def year_label(bucket):
    """区分の値 → 表示名。"2010-2019" は「2010年代」"""
    return ("%s年代" % bucket[:4]) if "-" in bucket else ("%s年" % bucket)


def year_sort(bucket):
    """新しい順に並べるための数値。開始年で見る"""
    head = bucket[:4]
    return int(head) if head.isdigit() else -1
