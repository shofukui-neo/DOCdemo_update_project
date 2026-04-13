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
    discover_from_hellowork,
    discover_from_pr_times,
    discover_from_sns,
    discover_from_wantedly,
)
from hr_discovery.utils.script_generator import generate_talk_script


RESULT_COLUMNS = [
    "企業名",
    "ホームページ",
    "判明した担当者名",
    "肩書き",
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
        "--output-dir",
        default=str(Path(__file__).parent / "data" / "results"),
        help="調査結果の保存先ディレクトリ",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="外部通信なしのデモレコードで動作確認する",
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
    records: List[DiscoveryRecord] = []

    records.extend(discover_from_wantedly(search_engine, company.company_name, company.homepage))
    records.extend(discover_from_pr_times(search_engine, company.company_name, company.homepage))
    records.extend(discover_from_hellowork(search_engine, company.company_name, company.homepage))
    records.extend(discover_from_sns(search_engine, company.company_name, company.homepage))

    return deduplicate_records(records)


def deduplicate_records(records: Iterable[DiscoveryRecord]) -> List[DiscoveryRecord]:
    best = {}
    for record in records:
        key = (record.company_name, record.person_name, record.source_url)
        existing = best.get(key)
        if existing is None or len(record.title) > len(existing.title):
            best[key] = record
    return list(best.values())


def render_rows(records: Iterable[DiscoveryRecord]) -> List[dict]:
    rows = []
    for record in records:
        row = {
            "企業名": record.company_name,
            "ホームページ": record.homepage,
            "判明した担当者名": record.person_name,
            "肩書き": record.title,
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


def save_report(rows: List[dict], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"hr_report_{timestamp}.csv"

    with open(output_path, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    return output_path


def demo_rows(company: CompanyInput) -> List[dict]:
    record = DiscoveryRecord(
        company_name=company.company_name,
        homepage=company.homepage,
        person_name="山田 太郎",
        title="人事責任者",
        source_url="https://prtimes.jp/example",
        source_label="PR TIMES",
    )
    return render_rows([record])


def main() -> int:
    args = parse_args()
    companies = load_companies(args)

    all_rows: List[dict] = []
    search_engine = SearchEngine()

    for company in companies:
        if args.demo:
            rows = demo_rows(company)
        else:
            records = discover_for_company(search_engine, company)
            rows = render_rows(records)

        all_rows.extend(rows)

    output_path = save_report(all_rows, Path(args.output_dir))
    print(f"調査結果を保存しました: {output_path}")
    print(f"出力件数: {len(all_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
