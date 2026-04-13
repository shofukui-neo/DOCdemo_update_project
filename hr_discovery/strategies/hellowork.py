from __future__ import annotations

from typing import List

from hr_discovery.search_engine import SearchEngine
from hr_discovery.strategies.base import DiscoveryRecord
from hr_discovery.utils.text_parser import extract_name_candidates


def discover_from_hellowork(search_engine: SearchEngine, company_name: str, homepage: str = "") -> List[DiscoveryRecord]:
    results = search_engine.search_site("hellowork.mhlw.go.jp", company_name, "求人 OR 採用")
    records: List[DiscoveryRecord] = []

    for result in results:
        page_text = search_engine.fetch_page_text(result.url)
        source_text = " ".join(filter(None, [result.title, result.snippet, page_text[:1200]]))
        for candidate in extract_name_candidates(source_text, max_results=2):
            records.append(
                DiscoveryRecord(
                    company_name=company_name,
                    homepage=homepage,
                    person_name=candidate.name,
                    title=candidate.title,
                    source_url=result.url,
                    source_label="HelloWork",
                )
            )

    return records
