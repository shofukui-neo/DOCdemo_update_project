from __future__ import annotations

from dataclasses import dataclass
from typing import List
from urllib.parse import quote_plus
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}


@dataclass
class SearchResult:
    source: str
    title: str
    url: str
    snippet: str


class SearchEngine:
    """Searches web pages through Yahoo/Google site queries."""

    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def search_site(self, site_domain: str, company_name: str, extra_keywords: str = "") -> List[SearchResult]:
        query = f'site:{site_domain} "{company_name}" {extra_keywords}'.strip()
        results: List[SearchResult] = []

        # Prioritize Yahoo Japan, then fallback to Google web search.
        for engine in (self._search_yahoo, self._search_google):
            try:
                found = engine(query)
                if found:
                    filtered = [item for item in found if self._is_matching_domain(item.url, site_domain)]
                    results.extend(filtered)
                    break
            except requests.RequestException:
                continue

        return results

    def search_web(self, query: str, max_results: int = 10) -> List[SearchResult]:
        for engine in (self._search_yahoo, self._search_google):
            try:
                found = engine(query)
                if found:
                    return found[:max_results]
            except requests.RequestException:
                continue
        return []

    def find_company_homepage(self, company_name: str) -> str:
        primary_query = f'"{company_name}" 公式サイト'
        secondary_query = f'"{company_name}" 会社概要'
        excluded_hosts = self._homepage_excluded_hosts()

        for query in (primary_query, secondary_query):
            for result in self.search_web(query, max_results=20):
                if self._is_valid_homepage_candidate(result.url, excluded_hosts):
                    return self._normalize_url(result.url)

        return ""

    def is_likely_official_homepage(self, url: str) -> bool:
        return self._is_valid_homepage_candidate(url, self._homepage_excluded_hosts())

    def _homepage_excluded_hosts(self) -> set[str]:
        return {
            "wantedly.com",
            "prtimes.jp",
            "hellowork.mhlw.go.jp",
            "linkedin.com",
            "facebook.com",
            "x.com",
            "chiebukuro.yahoo.co.jp",
            "ja.wikipedia.org",
            "koyou.pref.shizuoka.jp",
            "openwork.jp",
            "en-hyouban.com",
            "buffett-code.com",
            "wakayama-uiturn.jp",
            "plus-web.co.jp",
            "job.mynavi.jp",
            "rikunabi.com",
            "doda.jp",
            "en-gage.net",
        }

    def _is_valid_homepage_candidate(self, url: str, excluded_hosts: set[str]) -> bool:
        try:
            parsed = urlparse(url)
            host = (parsed.netloc or "").lower()
        except ValueError:
            return False

        if not host:
            return False

        if host.endswith(".go.jp") or host.endswith(".lg.jp"):
            return False
        if "pref." in host:
            return False

        if any(host == blocked or host.endswith(f".{blocked}") for blocked in excluded_hosts):
            return False

        path = (parsed.path or "").strip("/")
        if any(token in path for token in ("employment", "detail", "search")):
            return False

        return True

    def _normalize_url(self, url: str) -> str:
        try:
            parsed = urlparse(url)
        except ValueError:
            return url
        return parsed._replace(query="", fragment="").geturl()

    def _is_matching_domain(self, url: str, expected_domain: str) -> bool:
        try:
            parsed = urlparse(url)
            host = (parsed.netloc or "").lower()
        except ValueError:
            return False

        expected = expected_domain.lower()
        return host == expected or host.endswith(f".{expected}")

    def fetch_page_text(self, url: str) -> str:
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException:
            return ""

        soup = BeautifulSoup(response.text, "html.parser")
        return " ".join(soup.get_text(separator=" ", strip=True).split())

    def _search_yahoo(self, query: str) -> List[SearchResult]:
        url = f"https://search.yahoo.co.jp/search?p={quote_plus(query)}"
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.select("section li")
        results: List[SearchResult] = []

        for card in cards[:10]:
            link = card.select_one("a")
            if not link:
                continue

            href = link.get("href")
            title = " ".join(link.get_text(" ", strip=True).split())
            snippet_node = card.select_one("p")
            snippet = ""
            if snippet_node:
                snippet = " ".join(snippet_node.get_text(" ", strip=True).split())

            if href and title:
                results.append(SearchResult(source="yahoo", title=title, url=href, snippet=snippet))

        return results

    def _search_google(self, query: str) -> List[SearchResult]:
        url = f"https://www.google.com/search?q={quote_plus(query)}&hl=ja"
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        results: List[SearchResult] = []

        for card in soup.select("div.g")[:10]:
            link = card.select_one("a")
            title_node = card.select_one("h3")
            snippet_node = card.select_one("div.VwiC3b, span.aCOpRe")

            if not link or not title_node:
                continue

            href = link.get("href")
            title = " ".join(title_node.get_text(" ", strip=True).split())
            snippet = ""
            if snippet_node:
                snippet = " ".join(snippet_node.get_text(" ", strip=True).split())

            if href and title:
                results.append(SearchResult(source="google", title=title, url=href, snippet=snippet))

        return results
