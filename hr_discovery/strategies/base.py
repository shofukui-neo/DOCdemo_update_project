from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DiscoveryRecord:
    company_name: str
    homepage: str
    person_name: str
    title: str
    source_url: str
    source_label: str
    candidate_tier: str
    confidence_score: int
