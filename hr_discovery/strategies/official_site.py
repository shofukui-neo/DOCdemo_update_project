from __future__ import annotations

from typing import List
from urllib.parse import urljoin
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from hr_discovery.search_engine import SearchEngine
from hr_discovery.strategies.base import DiscoveryRecord
from hr_discovery.utils.text_parser import extract_name_candidates


TARGET_PATH_KEYWORDS = [
    "officer",
    "executive",
    "director",
    "organization",
    "orgchart",
    "press",
    "release",
    "recruit",
    "career",
    "about",
    "company",
    "news",
    "information",
    "役員",
    "役員紹介",
    "組織図",
    "プレスリリース",
    "採用",
    "会社概要",
    "企業情報",
    "ニュース",
]

PREFERRED_PATHS = [
    "officer",
    "executive",
    "organization",
    "orgchart",
    "press",
    "news",
    "company",
    "about",
    "recruit",
]


def discover_from_official_site(search_engine: SearchEngine, company_name: str, homepage: str) -> List[DiscoveryRecord]:
    if not homepage:
        return []

    urls_to_visit = _build_target_urls(search_engine, homepage)
    records: List[DiscoveryRecord] = []

    for url in urls_to_visit:
        page_text = search_engine.fetch_page_text(url)
        if not page_text:
            continue

        for candidate in extract_name_candidates(page_text[:5000], max_results=3):
            records.append(
                DiscoveryRecord(
                    company_name=company_name,
                    homepage=homepage,
                    person_name=candidate.name,
                    title=candidate.title,
                    source_url=url,
                    source_label="OfficialSite",
                    candidate_tier=candidate.tier,
                    confidence_score=candidate.score,
                )
            )

    return records


def _build_target_urls(search_engine: SearchEngine, homepage: str) -> List[str]:
    candidate_urls = {homepage}
    try:
        response = search_engine.session.get(homepage, timeout=search_engine.timeout)
        response.raise_for_status()
    except Exception:
        return [homepage]

    soup = BeautifulSoup(response.text, "html.parser")
    base_host = (urlparse(homepage).netloc or "").lower()

    for anchor in soup.select("a[href]"):
        href = (anchor.get("href") or "").strip()
        if not href:
            continue

        absolute = urljoin(homepage, href)
        parsed = urlparse(absolute)
        host = (parsed.netloc or "").lower()
        if not host or host != base_host:
            continue

        lowered = f"{absolute} {anchor.get_text(' ', strip=True)}".lower()
        if any(keyword.lower() in lowered for keyword in TARGET_PATH_KEYWORDS):
            candidate_urls.add(_strip_fragment_query(absolute))

        if len(candidate_urls) >= 30:
            break

    # Add common corporate paths even if not present in top navigation.
    for path in PREFERRED_PATHS:
        candidate_urls.add(_strip_fragment_query(urljoin(homepage, path)))

    ranked = sorted(candidate_urls, key=_url_priority)
    return ranked[:12]


def _url_priority(url: str) -> tuple[int, int]:
    lowered = url.lower()
    if any(token in lowered for token in ("officer", "executive", "役員")):
        return (0, len(url))
    if any(token in lowered for token in ("organization", "orgchart", "組織")):
        return (1, len(url))
    if any(token in lowered for token in ("press", "release", "プレス")):
        return (2, len(url))
    if any(token in lowered for token in ("news", "お知らせ")):
        return (3, len(url))
    if any(token in lowered for token in ("company", "about", "会社概要", "企業情報")):
        return (4, len(url))
    if any(token in lowered for token in ("recruit", "career", "採用")):
        return (5, len(url))
    return (9, len(url))


def _strip_fragment_query(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="").geturl()
