# -*- coding: utf-8 -*-
"""サイトの基本設定。公開先が決まったら SITE_URL を差し替えて再ビルドする。

SITE_URL は canonical・OGP・sitemap.xml・robots.txt に反映される。
公開後に変えると検索エンジンの評価がリセットされるので、公開前に確定させること。
"""

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

# ODbL 4.6（改変方法の提供義務）を満たすための公開リポジトリ
REPO_URL = "https://github.com/"

# 収録範囲の説明（トップとフッターに出す）
SCOPE_NOTE = "家庭用ゲーム機で遊べる日本語の乙女ゲームを収録しています。"
