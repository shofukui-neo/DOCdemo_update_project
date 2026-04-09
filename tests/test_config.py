"""
テスト: config.py — 設定管理

設定値の正当性を検証する。
"""

import pytest

from config import (
    WEB_APP_BASE_URL,
    LOGIN_EMAIL,
    LOGIN_PASSWORD,
    PAGES,
    CSV_COLUMNS,
    PAGE_LOAD_TIMEOUT,
    CONTENT_GENERATION_TIMEOUT,
    RETRY_COUNT,
    RETRY_DELAY,
    EXCLUDED_DOMAINS,
    BROWSER_VIEWPORT,
)


class TestConfig:
    """設定値の正当性テスト"""

    def test_web_app_url_is_https(self):
        """WebアプリURLがHTTPS"""
        assert WEB_APP_BASE_URL.startswith("https://")

    def test_login_credentials_not_empty(self):
        """ログイン情報が空でないこと"""
        assert LOGIN_EMAIL
        assert LOGIN_PASSWORD

    def test_pages_all_defined(self):
        """全ページパスが定義されていること"""
        required = ["login", "company_setup", "content_generator", "settings", "candidate_interview"]
        for page in required:
            assert page in PAGES

    def test_csv_columns_all_defined(self):
        """全CSV列名が定義されていること"""
        required = ["company_name", "homepage_url", "enterprise_id", "frontend_url", "status"]
        for col in required:
            assert col in CSV_COLUMNS

    def test_timeouts_positive(self):
        """タイムアウト値が正の値"""
        assert PAGE_LOAD_TIMEOUT > 0
        assert CONTENT_GENERATION_TIMEOUT > 0

    def test_retry_settings(self):
        """リトライ設定が妥当"""
        assert RETRY_COUNT >= 1
        assert RETRY_DELAY > 0

    def test_excluded_domains_not_empty(self):
        """除外ドメインリストが空でないこと"""
        assert len(EXCLUDED_DOMAINS) > 0

    def test_viewport_dimensions(self):
        """ビューポートサイズが妥当"""
        assert BROWSER_VIEWPORT["width"] >= 800
        assert BROWSER_VIEWPORT["height"] >= 600
