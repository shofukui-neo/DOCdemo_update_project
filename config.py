"""
DOCdemo 自動化フロー — 設定管理モジュール

全モジュール共通の定数・設定値を一元管理する。
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # python-dotenv 未インストール環境では .env を読まない

# =============================================================================
# プロジェクトパス
# =============================================================================
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"
LOGS_DIR = PROJECT_ROOT / "logs"

# =============================================================================
# Webアプリ設定
# =============================================================================
WEB_APP_BASE_URL = "https://casual-interview-api-dev.brainverse-ai.com"
LOGIN_EMAIL = os.getenv("DOCDEMO_LOGIN_EMAIL", "neocareer.dev@admin")
LOGIN_PASSWORD = os.getenv("DOCDEMO_LOGIN_PASSWORD", "neocareer.dev@admin-pw")

# ページパス
PAGES = {
    "login": "/",
    "company_setup": "/company_setup",
    "content_generator": "/content_generator",
    "settings": "/settings",
    "candidate_interview": "/candidate_interview",
}

# =============================================================================
# スプレッドシート設定
# =============================================================================
# 企業リストCSVファイル名
COMPANY_LIST_CSV = DATA_DIR / "company_list.csv"

# CSVカラム名
CSV_COLUMNS = {
    "company_name": "企業名",
    "homepage_url": "ホームページURL",
    "url_candidates": "URL候補",  # URL企業ID不一致検出時の候補URL（パイプ区切り）
    "enterprise_id": "企業ID",
    "frontend_url": "納品URL",  # フロントエンド公開URL = エンドユーザに渡す納品URL
    "status": "ステータス",
    "error_message": "エラー詳細",
    "quality_check": "品質チェック",          # Stage 4: OK / NG / 部分OK
    "quality_detail": "品質チェック詳細",      # Stage 4: 項目別 OK/NG 内訳
    "screenshot_path": "スクリーンショットパス",
}

# 旧カラム名（後方互換のため読込時に許容）
LEGACY_COLUMN_ALIASES = {
    "frontend_url": ["フロントエンドURL"],
}

# =============================================================================
# URL企業ID不一致検出設定
# =============================================================================
# URL検索結果から取得する候補数の最大値
URL_CANDIDATE_MAX = 5

# =============================================================================
# タイムアウト設定 (ミリ秒)
# =============================================================================
PAGE_LOAD_TIMEOUT = 60000            # ページ読み込み: 60秒
NAVIGATION_TIMEOUT = 30000           # ナビゲーション: 30秒
CONTENT_GENERATION_TIMEOUT = 300000  # コンテンツ生成待機: 5分
ELEMENT_WAIT_TIMEOUT = 10000         # 要素出現待機: 10秒
UPLOAD_TIMEOUT = 30000               # ファイルアップロード: 30秒

# =============================================================================
# リトライ設定
# =============================================================================
RETRY_COUNT = 3        # 最大リトライ回数
RETRY_DELAY = 5        # リトライ間隔 (秒)

# FAQ/企業情報 保存検証失敗時、Step 4 (コンテンツ生成) から再試行する最大回数
# (合計試行回数 = 1 + FAQ_SAVE_MAX_RETRIES)
FAQ_SAVE_MAX_RETRIES = 2

# 生成完了後の FAQ 実体検証 (DOM へ反映されるまで) の最大待機秒数
# 旧 60s → 120s に延長。生成完了スピナー消失後でも企業によっては
# FAQ レンダリングまでに 60〜90 秒程度かかるケースがあるため。
FAQ_VERIFY_TIMEOUT_SECONDS = 120

# =============================================================================
# Stage 4 (verify_quality.py) 設定
# =============================================================================
# AIチャットに送信するテスト質問 (返信に企業名が含まれるか検証する)
QUALITY_CHECK_CHAT_QUESTION = "御社の事業内容や強みについて教えてください。"

# AIチャットの返信を待つタイムアウト (ミリ秒)
QUALITY_CHECK_CHAT_TIMEOUT = 60000

# 1社あたりの全体タイムアウト (ミリ秒) — 静的5項目 + AIチャット
QUALITY_CHECK_TIMEOUT_PER_COMPANY = 120000

# 品質チェック結果のスクリーンショット保存ディレクトリ
QUALITY_SCREENSHOTS_DIR = SCREENSHOTS_DIR / "quality"

# =============================================================================
# ブラウザ設定
# =============================================================================
BROWSER_HEADLESS = os.getenv("DOCDEMO_HEADLESS", "true").lower() == "true"
BROWSER_VIEWPORT = {"width": 1280, "height": 800}
BROWSER_SLOW_MO = 500  # 操作間の遅延 (ms) — Streamlit UIの安定性のため

# =============================================================================
# URL検索設定
# =============================================================================
# 検索結果から除外するドメイン（求人・SNS系のみ。企業公式サイトを除外しないよう限定的に設定）
EXCLUDED_DOMAINS = [
    # SNS/ジョブボード
    "indeed.com", "linkedin.com", "facebook.com", "twitter.com",
    "instagram.com", "youtube.com", "tiktok.com", "x.com",
    "en-japan.com", "doda.jp", "mynavi.jp",
    "rikunabi.com", "wantedly.com", "green-japan.com",
    # 百科事典・検索エンジン
    "wikipedia.org",
    "yahoo.co.jp", "search.yahoo.co.jp",
    # プレスリリース配信サイト (公式サイトと誤認しがち)
    "prtimes.jp", "atpress.ne.jp", "value-press.com",
    "kyodonewsprwire.jp", "newscast.jp", "dreamnews.jp",
    # 企業情報DB (公式サイトではない)
    "houjin.jp", "alarmbox.jp", "baseconnect.in",
    "kaisha-search.com", "buffett-code.com", "salesnow.jp",
    "tdb-di.jp", "tsr-net.co.jp",
]

# リンク抽出の同時接続数
LINK_CHECK_CONCURRENCY = 10

# =============================================================================
# 求人サイト検索設定 (Step 3 でコンテンツ生成入力に追加するURL収集用)
# =============================================================================
# 検索対象の求人サイト (Yahoo!検索で `site:<domain> <企業名>` 形式で検索)
# Step 1 の EXCLUDED_DOMAINS とは独立。公式サイト特定では除外、採用情報収集では積極使用。
RECRUIT_SITES = [
    {"name": "マイナビ転職",   "domain": "tenshoku.mynavi.jp"},
    {"name": "マイナビ新卒",   "domain": "job.mynavi.jp"},
    {"name": "リクナビ",       "domain": "rikunabi.com"},
    {"name": "DODA",          "domain": "doda.jp"},
    {"name": "エン転職",       "domain": "employment.en-japan.com"},
    {"name": "Wantedly",      "domain": "wantedly.com"},
    {"name": "Green",         "domain": "green-japan.com"},
    {"name": "Indeed",        "domain": "indeed.com"},
]

# 求人サイトから取り込むURLの合計上限 (コンテンツ生成のノイズ防止)
RECRUIT_URL_MAX_TOTAL = 10
# 1つの求人サイトから取り込むURLの上限
RECRUIT_URL_MAX_PER_SITE = 2

# =============================================================================
# ログ設定
# =============================================================================
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_LEVEL = os.getenv("DOCDEMO_LOG_LEVEL", "INFO")
LOG_FILE = LOGS_DIR / "automation.log"
