from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from hr_discovery.search_engine import SearchEngine
from hr_discovery.strategies import (
    DiscoveryRecord,
    discover_from_job_boards,
    discover_from_official_site,
    discover_from_hellowork,
    discover_from_pr_times,
    discover_from_sns,
    discover_from_wantedly,
)
from hr_discovery.utils.script_generator import generate_talk_script
from hr_discovery.utils.text_parser import set_parser_mode


RESULT_COLUMNS = [
    "企業名",
    "ホームページ",
    "判明した担当者名",
    "肩書き",
    "判定区分",
    "信頼スコア",
    "出典URL",
    "推奨トーク案",
]


@dataclass
class CompanyInput:
    company_name: str
    homepage: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HR採用担当者名 調査自動化ツール")
    parser.add_argument("--company", help="調査対象企業名")
    parser.add_argument("--homepage", default="", help="企業のホームページURL（任意）")
    parser.add_argument("--csv", help="企業リストCSV（company_name,homepage列）")
    parser.add_argument(
        "--enrich-csv",
        action="store_true",
        help="CSVの空ホームページ欄を公式URLで補完してから実行する",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).parent / "data" / "results"),
        help="調査結果の保存先ディレクトリ",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="外部通信なしのデモレコードで動作確認する",
    )
    parser.add_argument(
        "--mode",
        choices=["strict", "discovery"],
        default="strict",
        help="抽出モード。discoveryは取得率重視、strictは精度重視。",
    )
    return parser.parse_args()


def load_companies(args: argparse.Namespace) -> List[CompanyInput]:
    companies: List[CompanyInput] = []

    if args.company:
        companies.append(CompanyInput(company_name=args.company, homepage=args.homepage))

    if args.csv:
        with open(args.csv, "r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                company_name = (row.get("company_name") or row.get("企業名") or "").strip()
                if not company_name:
                    continue
                homepage = (row.get("homepage") or row.get("ホームページ") or "").strip()
                companies.append(CompanyInput(company_name=company_name, homepage=homepage))

    if not companies:
        raise ValueError("--company もしくは --csv のいずれかを指定してください。")

    return companies


def discover_for_company(search_engine: SearchEngine, company: CompanyInput) -> List[DiscoveryRecord]:
    if not company.homepage:
        company.homepage = search_engine.find_company_homepage(company.company_name)

    records: List[DiscoveryRecord] = []

    # Prioritize direct crawl of official pages (recruit/about/news).
    records.extend(discover_from_official_site(search_engine, company.company_name, company.homepage))
    records.extend(discover_from_job_boards(search_engine, company.company_name, company.homepage))
    records.extend(discover_from_wantedly(search_engine, company.company_name, company.homepage))
    records.extend(discover_from_pr_times(search_engine, company.company_name, company.homepage))
    records.extend(discover_from_hellowork(search_engine, company.company_name, company.homepage))
    records.extend(discover_from_sns(search_engine, company.company_name, company.homepage))

    if not records:
        records.append(_build_fallback_review_record(company))

    return deduplicate_records(records)


def _build_fallback_review_record(company: CompanyInput) -> DiscoveryRecord:
    source_url = company.homepage or ""
    return DiscoveryRecord(
        company_name=company.company_name,
        homepage=company.homepage,
        person_name="採用担当者（要確認）",
        title="採用窓口",
        source_url=source_url,
        source_label="Fallback",
        candidate_tier="要確認候補",
        confidence_score=1,
    )


def enrich_csv_homepages(csv_path: str, search_engine: SearchEngine) -> None:
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames or ["company_name", "homepage"]
        rows = list(reader)

    changed = 0
    for row in rows:
        company_name = (row.get("company_name") or row.get("企業名") or "").strip()
        if not company_name:
            continue

        homepage = (row.get("homepage") or row.get("ホームページ") or "").strip()
        needs_update = (not homepage) or (not search_engine.is_likely_official_homepage(homepage))
        if not needs_update:
            continue

        found = search_engine.find_company_homepage(company_name)
        if not found:
            continue

        if "homepage" in fieldnames:
            row["homepage"] = found
        elif "ホームページ" in fieldnames:
            row["ホームページ"] = found
        else:
            row["homepage"] = found
        if found != homepage:
            changed += 1

    if changed == 0:
        return

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"CSVのホームページ欄を補完しました: {changed}件")


def deduplicate_records(records: Iterable[DiscoveryRecord]) -> List[DiscoveryRecord]:
    best = {}
    for record in records:
        key = (record.company_name, record.person_name, record.source_url)
        existing = best.get(key)
        if existing is None or _record_rank(record) > _record_rank(existing):
            best[key] = record
    return list(best.values())


def _record_rank(record: DiscoveryRecord) -> tuple[int, int, int]:
    tier_rank = 1 if record.candidate_tier == "確定候補" else 0
    return (tier_rank, record.confidence_score, len(record.title))


def render_rows(records: Iterable[DiscoveryRecord]) -> List[dict]:
    rows = []
    for record in records:
        row = {
            "企業名": record.company_name,
            "ホームページ": record.homepage,
            "判明した担当者名": record.person_name,
            "肩書き": record.title,
            "判定区分": record.candidate_tier,
            "信頼スコア": record.confidence_score,
            "出典URL": record.source_url,
            "推奨トーク案": generate_talk_script(
                company_name=record.company_name,
                source_label=record.source_label,
                person_name=record.person_name,
                title=record.title,
            ),
        }
        rows.append(row)
    return rows


def save_report(rows: List[dict], output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    all_path = output_dir / f"hr_report_{timestamp}.csv"
    confirmed_path = output_dir / f"hr_report_confirmed_{timestamp}.csv"
    review_path = output_dir / f"hr_report_review_{timestamp}.csv"

    confirmed_rows = [row for row in rows if row.get("判定区分") == "確定候補"]
    review_rows = [row for row in rows if row.get("判定区分") != "確定候補"]

    _write_rows(all_path, rows)
    _write_rows(confirmed_path, confirmed_rows)
    _write_rows(review_path, review_rows)

    return {
        "all": all_path,
        "confirmed": confirmed_path,
        "review": review_path,
    }


def _write_rows(path: Path, rows: List[dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def demo_rows(company: CompanyInput) -> List[dict]:
    record = DiscoveryRecord(
        company_name=company.company_name,
        homepage=company.homepage,
        person_name="山田 太郎",
        title="人事責任者",
        source_url="https://prtimes.jp/example",
        source_label="PR TIMES",
        candidate_tier="確定候補",
        confidence_score=42,
    )
    return render_rows([record])


def main() -> int:
    args = parse_args()
    set_parser_mode(args.mode)
    search_engine = SearchEngine()

    if args.csv and args.enrich_csv:
        enrich_csv_homepages(args.csv, search_engine)

    companies = load_companies(args)
    all_rows: List[dict] = []

    for company in companies:
        if args.demo:
            rows = demo_rows(company)
        else:
            records = discover_for_company(search_engine, company)
            rows = render_rows(records)

        all_rows.extend(rows)

    output_paths = save_report(all_rows, Path(args.output_dir))
    print(f"調査結果(全件)を保存しました: {output_paths['all']}")
    print(f"調査結果(確定候補)を保存しました: {output_paths['confirmed']}")
    print(f"調査結果(要確認候補)を保存しました: {output_paths['review']}")
    print(f"出力件数: {len(all_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
