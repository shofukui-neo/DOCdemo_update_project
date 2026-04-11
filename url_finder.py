"""
DOCdemo 自動化フロー — URL検索モジュール

企業名からホームページURLをYahoo! Japan検索で自動特定する。
Google/Bing/DuckDuckGoはCAPTCHA/JSレンダリング制約があるため、
Yahoo! Japan検索をメインに使用する。
"""

import asyncio
import logging
import re
from urllib.parse import urlparse, quote
from typing import Optional

from playwright.async_api import Page

from config import PAGE_LOAD_TIMEOUT, RETRY_COUNT, RETRY_DELAY, EXCLUDED_DOMAINS

logger = logging.getLogger(__name__)


class URLFinder:
    """Yahoo! Japan検索を使い、企業のホームページURLを自動で特定するクラス"""

    def __init__(self, page: Optional[Page] = None):
        """
        Args:
            page: Playwrightのページインスタンス。
        """
        self._page = page

    async def find_homepage_url(self, company_name: str) -> str:
        """
        Yahoo! Japan検索で企業のホームページURLを自動特定する。

        Args:
            company_name: 企業名（例: "one-hat株式会社"）

        Returns:
            ホームページURL。見つからない場合は空文字列。

        戦略:
        1. Yahoo! Japan検索「{企業名} 公式サイト」を実行
        2. 検索結果からリンクを取得
        3. 除外ドメイン（求人・SNS等）をフィルタリング
        4. 最初の有効な結果を返す
        """
        search_query = f"{company_name} 公式サイト"
        logger.info(f"URL検索開始: {search_query}")

        for attempt in range(RETRY_COUNT):
            try:
                result = await self._search_yahoo(search_query, company_name)
                if result:
                    return result
                # 結果が空の場合はクエリを変えてリトライ
                if attempt == 0:
                    search_query = f"{company_name}"
                elif attempt == 1:
                    search_query = f"{company_name} ホームページ"
                logger.debug(f"クエリ変更してリトライ: {search_query}")
            except Exception as e:
                logger.warning(
                    f"URL検索エラー (試行 {attempt + 1}/{RETRY_COUNT}): {e}"
                )
                if attempt < RETRY_COUNT - 1:
                    await asyncio.sleep(RETRY_DELAY)

        logger.error(f"URL検索失敗: {company_name}")
        return ""

    async def _search_yahoo(self, query: str, company_name: str) -> str:
        """
        Yahoo! Japan検索を実行し、最適なURLを返す。

        Args:
            query: 検索クエリ
            company_name: 企業名（フィルタリング用）

        Returns:
            見つかったURL（見つからない場合は空文字列）
        """
        page = self._page
        search_url = f"https://search.yahoo.co.jp/search?p={quote(query)}"

        await page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=PAGE_LOAD_TIMEOUT,
        )
        await page.wait_for_timeout(2000)

        # Yahoo!検索結果のリンクを取得
        result_links = await page.eval_on_selector_all(
            ".sw-Card__title a, #contents .sw-Card a[href^='http']",
            """elements => elements.map(el => ({
                href: el.href,
                text: el.textContent || ''
            }))"""
        )

        if not result_links:
            # 代替セレクター
            result_links = await page.eval_on_selector_all(
                "a[href^='http']:not([href*='yahoo']):not([href*='yimg'])",
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

            parsed = urlparse(href)

            # Yahoo内部リンクをスキップ
            if "yahoo" in parsed.netloc or "yimg" in parsed.netloc:
                continue

            # 除外ドメインをスキップ
            if self._is_excluded_domain(parsed.netloc):
                logger.debug(f"除外ドメイン: {href}")
                continue

            # ルートドメインを返す（深いパスの場合はトップに近いURLを優先）
            clean_url = f"{parsed.scheme}://{parsed.netloc}"
            if parsed.path and parsed.path != "/":
                path_depth = len([p for p in parsed.path.split("/") if p])
                if path_depth <= 1:
                    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

            logger.info(f"URL特定成功: {company_name} -> {clean_url}")
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
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT,
            )
            return response is not None and response.ok
        except Exception as e:
            logger.warning(f"URL検証エラー: {url} — {e}")
            return False
