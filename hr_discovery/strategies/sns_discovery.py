from __future__ import annotations

from typing import List

from hr_discovery.search_engine import SearchEngine
from hr_discovery.strategies.base import DiscoveryRecord
from hr_discovery.utils.text_parser import extract_name_candidates


SNS_SITES = ["linkedin.com", "facebook.com", "x.com"]


def discover_from_sns(search_engine: SearchEngine, company_name: str, homepage: str = "") -> List[DiscoveryRecord]:
    records: List[DiscoveryRecord] = []

    for site in SNS_SITES:
        results = search_engine.search_site(site, company_name, "採用 OR 人事 OR HR")
        for result in results:
            source_text = " ".join(filter(None, [result.title, result.snippet]))
            for candidate in extract_name_candidates(source_text, max_results=1):
                records.append(
                    DiscoveryRecord(
                        company_name=company_name,
                        homepage=homepage,
                        person_name=candidate.name,
                        title=candidate.title,
                        source_url=result.url,
                        source_label=f"SNS:{site}",
                    )
                )

    return records
