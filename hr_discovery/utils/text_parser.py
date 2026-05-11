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
    "広報",
    "総務",
    "総務人事",
    "人事総務",
    "管理部",
    "HR",
    "recruit",
    "採用担当",
]

TITLE_PATTERN = re.compile(
    r"(人事(?:部長|責任者|担当|課長|主任)?|"
    r"採用(?:責任者|担当|部長|課長)?|"
    r"広報(?:責任者|担当|部長)?|"
    r"総務(?:人事)?(?:部長|責任者|担当|課長)?|"
    r"人事総務(?:部長|責任者|担当|課長)?|"
    r"管理部(?:長|責任者|担当)?|"
    r"HR(?:責任者|担当|Manager)?|"
    r"Talent Acquisition(?: Manager)?)"
)

STOP_WORDS = {
    "高校",
    "年生",
    "大学",
    "採用",
    "人事",
    "担当",
    "責任者",
    "広報",
    "総務",
    "総務人事",
    "人事総務",
    "会社概要",
    "ニュース",
    "採用情報",
    "株式会社",
    "有限会社",
}

NON_PERSON_TOKENS = {
    "採用",
    "情報",
    "内容",
    "企業",
    "学生",
    "社会人",
    "主催",
    "制度",
    "登録",
    "以下",
    "新卒",
    "中途",
}

PARSER_MODE = "strict"


@dataclass
class NameCandidate:
    name: str
    score: int
    title: str
    tier: str


def set_parser_mode(mode: str) -> None:
    global PARSER_MODE
    PARSER_MODE = mode if mode in {"strict", "discovery"} else "strict"


def extract_name_candidates(text: str, max_results: int = 5) -> List[NameCandidate]:
    if not text:
        return []

    normalized = " ".join(text.split())
    candidates: List[NameCandidate] = []

    for match in NAME_PATTERN.finditer(normalized):
        name = match.group(0).strip()
        if len(name) < 2:
            continue
        if name in STOP_WORDS:
            continue
        if any(token in name for token in NON_PERSON_TOKENS):
            continue
        if not _is_plausible_person_name(name):
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
        if title_match:
            score += 12
        if "様" in name or "さん" in name:
            score += 3
        if any(token in window for token in ("お問い合わせ", "インタビュー", "コメント")):
            score += 2

        normalized_name = name.replace("様", "").replace("さん", "")
        tier = _decide_tier(
            score=score,
            keyword_hits=keyword_hits,
            has_title=bool(title_match),
            mode=PARSER_MODE,
        )
        if not tier:
            continue

        candidates.append(NameCandidate(name=normalized_name, score=score, title=title, tier=tier))

    return _deduplicate_and_sort(candidates)[:max_results]


def _deduplicate_and_sort(candidates: Iterable[NameCandidate]) -> List[NameCandidate]:
    best_by_name = {}
    for candidate in candidates:
        existing = best_by_name.get(candidate.name)
        if existing is None or _ranking_key(candidate) > _ranking_key(existing):
            best_by_name[candidate.name] = candidate

    return sorted(best_by_name.values(), key=_ranking_key, reverse=True)


def _decide_tier(score: int, keyword_hits: int, has_title: bool, mode: str) -> str:
    if mode == "discovery":
        if has_title and keyword_hits >= 1 and score >= 16:
            return "確定候補"
        if keyword_hits >= 1 and score >= 10:
            return "要確認候補"
        return ""

    if has_title and keyword_hits >= 2 and score >= 30:
        return "確定候補"
    if has_title and score >= 20:
        return "要確認候補"
    if keyword_hits >= 3 and score >= 24:
        return "要確認候補"
    return ""


def _ranking_key(candidate: NameCandidate) -> tuple[int, int]:
    tier_rank = 1 if candidate.tier == "確定候補" else 0
    return (tier_rank, candidate.score)


def _is_plausible_person_name(name: str) -> bool:
    compact = name.replace(" ", "")
    if len(compact) < 2 or len(compact) > 8:
        return False

    # Typical Japanese names are 2-4 kanji for family and given name each.
    if all("一" <= char <= "龥" or char == "々" for char in compact):
        return True

    if " " in name:
        parts = [part for part in name.split(" ") if part]
        if len(parts) == 2 and all(part[:1].isupper() for part in parts):
            return True

    return False
