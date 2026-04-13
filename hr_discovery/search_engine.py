from __future__ import annotations

from dataclasses import dataclass
from typing import List
from urllib.parse import quote_plus

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
                    results.extend(found)
                    break
            except requests.RequestException:
                continue

        return results

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
