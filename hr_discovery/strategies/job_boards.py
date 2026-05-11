from __future__ import annotations

from typing import List

from hr_discovery.search_engine import SearchEngine
from hr_discovery.strategies.base import DiscoveryRecord
from hr_discovery.utils.text_parser import extract_name_candidates


JOB_BOARD_SITES = [
    ("en-gage.net", "engage"),
    ("mynavi.jp", "mynavi"),
    ("rikunabi.com", "rikunabi"),
    ("doda.jp", "doda"),
]


def discover_from_job_boards(search_engine: SearchEngine, company_name: str, homepage: str = "") -> List[DiscoveryRecord]:
    records: List[DiscoveryRecord] = []

    for domain, label in JOB_BOARD_SITES:
        results = search_engine.search_site(domain, company_name, "採用 OR 人事 OR 募集")
        for result in results:
            page_text = search_engine.fetch_page_text(result.url)
            source_text = " ".join(filter(None, [result.title, result.snippet, page_text[:2500]]))
            for candidate in extract_name_candidates(source_text, max_results=2):
                records.append(
                    DiscoveryRecord(
                        company_name=company_name,
                        homepage=homepage,
                        person_name=candidate.name,
                        title=candidate.title,
                        source_url=result.url,
                        source_label=f"JobBoard:{label}",
                        candidate_tier=candidate.tier,
                        confidence_score=candidate.score,
                    )
                )

    return records
