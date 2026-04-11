"""
DOCdemo 自動化フロー — 設定管理モジュール

全モジュール共通の定数・設定値を一元管理する。
"""

import os
from pathlib import Path

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
    "enterprise_id": "企業ID",
    "frontend_url": "フロントエンドURL",
    "status": "ステータス",
    "error_message": "エラー詳細",
    "screenshot_path": "スクリーンショットパス",
}

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
    "indeed.com", "linkedin.com", "facebook.com", "twitter.com",
    "instagram.com", "youtube.com", "tiktok.com", "x.com",
    "en-japan.com", "doda.jp", "mynavi.jp",
    "rikunabi.com", "wantedly.com", "green-japan.com",
    "wikipedia.org",
    "yahoo.co.jp", "search.yahoo.co.jp",
]

# リンク抽出の同時接続数
LINK_CHECK_CONCURRENCY = 10

# =============================================================================
# ログ設定
# =============================================================================
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_LEVEL = os.getenv("DOCDEMO_LOG_LEVEL", "INFO")
LOG_FILE = LOGS_DIR / "automation.log"
