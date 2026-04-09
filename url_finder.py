"""
DOCdemo 自動化フロー — URL検索モジュール

企業名からホームページURLをGoogle検索で自動特定する。
"""

import asyncio
import logging
import re
from urllib.parse import urlparse, unquote
from typing import Optional

from playwright.async_api import async_playwright, Page

from config import EXCLUDED_DOMAINS, PAGE_LOAD_TIMEOUT, RETRY_COUNT, RETRY_DELAY

logger = logging.getLogger(__name__)


class URLFinder:
    """Google検索を使い、企業のホームページURLを自動で特定するクラス"""

    def __init__(self, page: Optional[Page] = None):
        """
        Args:
            page: Playwrightのページインスタンス。
                  Noneの場合は内部で新規ブラウザを起動。
        """
        self._page = page
        self._owns_browser = page is None

    async def find_homepage_url(self, company_name: str) -> str:
        """
        Google検索で企業のホームページURLを自動特定する。

        Args:
            company_name: 企業名（例: "one-hat株式会社"）

        Returns:
            ホームページURL。見つからない場合は空文字列。

        戦略:
        1. Google検索「{企業名} 公式サイト」を実行
        2. オーガニック検索結果からリンクを取得
        3. 除外ドメイン（求人サイト・SNS等）をフィルタリング
        4. 最初の有効な結果を返す
        """
        search_query = f"{company_name} 公式サイト"
        logger.info(f"URL検索開始: {search_query}")

        for attempt in range(RETRY_COUNT):
            try:
                return await self._search_google(search_query, company_name)
            except Exception as e:
                logger.warning(
                    f"URL検索エラー (試行 {attempt + 1}/{RETRY_COUNT}): {e}"
                )
                if attempt < RETRY_COUNT - 1:
                    await asyncio.sleep(RETRY_DELAY)

        logger.error(f"URL検索失敗: {company_name}")
        return ""

    async def _search_google(self, query: str, company_name: str) -> str:
        """
        Google検索を実行し、最適なURLを返す。

        Args:
            query: 検索クエリ
            company_name: 企業名（フィルタリング用）

        Returns:
            見つかったURL
        """
        page = self._page
        search_url = f"https://www.google.com/search?q={query}&hl=ja"

        await page.goto(search_url, wait_until="domcontentloaded",
                        timeout=PAGE_LOAD_TIMEOUT)

        # Cookie同意ダイアログが出る場合は承認
        try:
            consent_btn = page.locator("button:has-text('すべて同意')")
            if await consent_btn.count() > 0:
                await consent_btn.first.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        # Google検索結果のリンクを取得
        # 主要な検索結果のセレクター
        result_links = await page.eval_on_selector_all(
            "#search a[href]",
            """elements => elements.map(el => ({
                href: el.href,
                text: el.textContent || ''
            }))"""
        )

        if not result_links:
            # 代替セレクター
            result_links = await page.eval_on_selector_all(
                "a[href^='http']",
                """elements => elements.map(el => ({
                    href: el.href,
                    text: el.textContent || ''
                }))"""
            )

        # フィルタリングして最適なURLを選択
        for link_info in result_links:
            href = link_info.get("href", "")
            if not href or not href.startswith("http"):
                continue

            # Google内部リンクをスキップ
            parsed = urlparse(href)
            if "google" in parsed.netloc:
                continue

            # 除外ドメインをスキップ
            if self._is_excluded_domain(parsed.netloc):
                logger.debug(f"除外ドメイン: {href}")
                continue

            # 有効なURLとして返す
            # ルートドメインを返す（深いパスの場合）
            clean_url = f"{parsed.scheme}://{parsed.netloc}"
            if parsed.path and parsed.path != "/":
                # トップページに近いURLを優先
                path_depth = len([p for p in parsed.path.split("/") if p])
                if path_depth <= 1:
                    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

            logger.info(f"URL特定成功: {company_name} → {clean_url}")
            return clean_url

        logger.warning(f"URL特定失敗: {company_name} — 検索結果に適切なリンクなし")
        return ""

    def _is_excluded_domain(self, netloc: str) -> bool:
        """除外対象ドメインかどうかを判定"""
        netloc_lower = netloc.lower()
        return any(
            excluded in netloc_lower
            for excluded in EXCLUDED_DOMAINS
        )

    async def validate_url(self, url: str) -> bool:
        """
        URLが有効（HTTPステータス200番台）かどうかを検証する。

        Args:
            url: 検証対象のURL

        Returns:
            有効ならTrue
        """
        if not url:
            return False

        page = self._page
        try:
            response = await page.goto(url, wait_until="domcontentloaded",
                                       timeout=PAGE_LOAD_TIMEOUT)
            return response is not None and response.ok
        except Exception as e:
            logger.warning(f"URL検証エラー: {url} — {e}")
            return False
