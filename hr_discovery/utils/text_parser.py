from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List


NAME_PATTERN = re.compile(
    r"(?:[一-龥々]{1,4}\s?[一-龥々]{1,4}|[A-Z][a-z]+\s[A-Z][a-z]+)(?:\s?(?:様|さん))?"
)

KEYWORDS = [
    "採用",
    "人事",
    "担当",
    "責任者",
    "HR",
    "recruit",
    "採用担当",
]

TITLE_PATTERN = re.compile(
    r"(人事(?:部長|責任者|担当)?|採用(?:責任者|担当)?|HR(?:責任者|担当)?|Talent Acquisition(?: Manager)?)"
)


@dataclass
class NameCandidate:
    name: str
    score: int
    title: str


def extract_name_candidates(text: str, max_results: int = 5) -> List[NameCandidate]:
    if not text:
        return []

    normalized = " ".join(text.split())
    candidates: List[NameCandidate] = []

    for match in NAME_PATTERN.finditer(normalized):
        name = match.group(0).strip()
        if len(name) < 2:
            continue

        window_start = max(0, match.start() - 50)
        window_end = min(len(normalized), match.end() + 50)
        window = normalized[window_start:window_end]

        keyword_hits = sum(1 for keyword in KEYWORDS if keyword.lower() in window.lower())
        if keyword_hits == 0:
            continue

        title_match = TITLE_PATTERN.search(window)
        title = title_match.group(1) if title_match else "採用関連担当"

        score = keyword_hits * 10
        if "様" in name or "さん" in name:
            score += 3
        if any(token in window for token in ("お問い合わせ", "インタビュー", "コメント")):
            score += 2

        candidates.append(NameCandidate(name=name.replace("様", "").replace("さん", ""), score=score, title=title))

    return _deduplicate_and_sort(candidates)[:max_results]


def _deduplicate_and_sort(candidates: Iterable[NameCandidate]) -> List[NameCandidate]:
    best_by_name = {}
    for candidate in candidates:
        existing = best_by_name.get(candidate.name)
        if existing is None or candidate.score > existing.score:
            best_by_name[candidate.name] = candidate

    return sorted(best_by_name.values(), key=lambda item: item.score, reverse=True)
