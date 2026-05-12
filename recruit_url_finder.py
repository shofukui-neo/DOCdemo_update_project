"""
DOCdemo 自動化フロー — 求人サイトURL検索モジュール

各種求人サイト（マイナビ・リクナビ・DODA等）から、
指定企業の掲載ページURLをYahoo!検索で取得する。

採用情報を厚く学習させるため、コンテンツ生成入力 (Step 3) に
内部リンクと一緒に追加するURLを供給する。

設計上の注意:
- Step 1 の URLFinder と異なり、求人サイトを「除外」ではなく「対象」として扱う
- 各サイト最大 RECRUIT_URL_MAX_PER_SITE 件、合計 RECRUIT_URL_MAX_TOTAL 件まで
- スニペット内に企業名が含まれるかを軽く検証してノイズ排除
"""

import logging
import re
from urllib.parse import quote, urlparse
from typing import List, Optional

from playwright.async_api import Page

from config import (
    PAGE_LOAD_TIMEOUT,
    RECRUIT_SITES,
    RECRUIT_URL_MAX_TOTAL,
    RECRUIT_URL_MAX_PER_SITE,
)

logger = logging.getLogger(__name__)

# 法人格パターン (検証時に企業名から除外して柔軟マッチング)
_LEGAL_FORMS = re.compile(
    r"(株式会社|有限会社|合同会社|合資会社|合名会社|"
    r"一般財団法人|公益財団法人|一般社団法人|公益社団法人|"
    r"医療法人社団|医療法人|社会福祉法人|学校法人|"
    r"特定非営利活動法人|NPO法人|"
    r"税理士法人|司法書士法人|弁護士法人)"
)


def _normalize_name(name: str) -> str:
    """企業名から法人格・空白を除去した検索用の正規化キーを返す"""
    cleaned = _LEGAL_FORMS.sub("", name)
    return cleaned.strip().replace(" ", "").replace("　", "")


async def find_recruit_site_urls(
    company_name: str,
    page: Page,
) -> List[str]:
    """
    各求人サイト内で企業名検索を行い、該当企業のページURLを返す。

    Args:
        company_name: 企業名 (法人格込みでOK)
        page: Playwrightのページインスタンス (Yahoo!検索用)

    Returns:
        求人サイトの企業掲載ページURLリスト (重複排除済、最大 RECRUIT_URL_MAX_TOTAL 件)
    """
    norm_name = _normalize_name(company_name)
    logger.info(
        f"求人サイトURL収集開始: {company_name} (正規化: '{norm_name}')"
    )

    all_urls: list = []
    seen_urls: set = set()

    for site in RECRUIT_SITES:
        if len(all_urls) >= RECRUIT_URL_MAX_TOTAL:
            logger.debug(f"  合計上限 {RECRUIT_URL_MAX_TOTAL} 件に到達 → 残りスキップ")
            break

        domain = site["domain"]
        name = site["name"]

        try:
            urls_for_site = await _search_site(page, domain, company_name, norm_name)
        except Exception as e:
            logger.debug(f"  [{name}] 検索エラー: {e}")
            continue

        added = 0
        for url in urls_for_site:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            all_urls.append(url)
            added += 1
            if added >= RECRUIT_URL_MAX_PER_SITE:
                break
            if len(all_urls) >= RECRUIT_URL_MAX_TOTAL:
                break

        logger.info(f"  [{name}] {added}件取り込み (累計 {len(all_urls)}件)")

    logger.info(
        f"求人サイトURL収集完了: {company_name} → 合計 {len(all_urls)} 件"
    )
    return all_urls


async def _search_site(
    page: Page,
    site_domain: str,
    company_name: str,
    norm_name: str,
) -> List[str]:
    """
    Yahoo!検索で `site:<domain> <企業名>` を実行し、結果URLを返す。

    Returns:
        該当サイトの候補URLリスト (順序: 検索結果順)
    """
    query = f"site:{site_domain} {company_name}"
    search_url = f"https://search.yahoo.co.jp/search?p={quote(query)}"

    await page.goto(
        search_url,
        wait_until="domcontentloaded",
        timeout=PAGE_LOAD_TIMEOUT,
    )
    await page.wait_for_timeout(1500)

    # Yahoo!検索結果のリンクとスニペットを取得
    results = await page.eval_on_selector_all(
        ".sw-Card",
        """elements => elements.map(el => {
            const titleEl = el.querySelector(".sw-Card__title a");
            const snippetEl = el.querySelector(".sw-Card__description, .sw-Card__summary");
            return {
                url: titleEl ? titleEl.href : "",
                title: titleEl ? titleEl.textContent : "",
                snippet: snippetEl ? snippetEl.textContent : "",
            };
        })"""
    )

    candidates: list = []
    for r in results:
        url = r.get("url", "")
        if not url or not url.startswith("http"):
            continue

        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        # Yahoo経由のリダイレクト/internal はスキップ
        if "yahoo" in netloc or "yimg" in netloc:
            continue
        # 該当ドメイン内のURLか確認
        if site_domain not in netloc:
            continue

        # スニペット+タイトルに企業名(正規化済)が含まれることを軽く確認
        haystack = (r.get("title", "") + " " + r.get("snippet", "")).replace(
            " ", ""
        ).replace("　", "")
        if norm_name and norm_name not in haystack:
            # 法人格除いた名前で見つからない場合は元の名前でも試す
            if company_name.replace(" ", "") not in haystack:
                continue

        candidates.append(url)

    return candidates
