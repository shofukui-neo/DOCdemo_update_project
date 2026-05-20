"""
納品URL（推定）生成スクリプト

`data/company_list_delivery_urls.csv` の「納品URL」列を、
`data/company_list.csv` の「企業ID」列から自動的に埋める。

URLパターン:
    https://casual-interview-dev.brainverse-ai.com/<企業ID>

使い方:
    python generate_predicted_delivery_urls.py                # 標準実行 (素のURL)
    python generate_predicted_delivery_urls.py --predicted    # [推定] プレフィックス付き
    python generate_predicted_delivery_urls.py --dry-run      # 書き戻さず内容だけ表示

注意:
    本URLは「企業ID から組み立てた予想」であり、Brainverse 側で実際に
    生成されていない企業は 404 になる可能性がある。
    完全な納品URLが必要な場合は `python orchestrator.py` で Stage 2 を
    完了させて `python generate_delivery_list.py` を使うこと。
"""

import argparse
import csv
from pathlib import Path

BASE_URL = "https://casual-interview-dev.brainverse-ai.com"
SRC_COMPANY_LIST = Path("data/company_list.csv")
TARGET_CSV = Path("data/company_list_delivery_urls.csv")


def build_enterprise_id_map(csv_path: Path) -> dict:
    """`{企業名: 企業ID}` のマップを返す。"""
    if not csv_path.exists():
        raise FileNotFoundError(f"参照元CSVが見つかりません: {csv_path}")
    id_map = {}
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("企業名") or "").strip()
            ent_id = (row.get("企業ID") or "").strip()
            if name:
                id_map[name] = ent_id
    return id_map


def fill_delivery_urls(
    target_csv: Path,
    id_map: dict,
    add_predicted_prefix: bool = False,
    dry_run: bool = False,
) -> tuple:
    """対象CSVの納品URL列を埋める。

    Returns:
        (生成成功数, 企業ID未取得数, 全行数) のタプル
    """
    if not target_csv.exists():
        raise FileNotFoundError(f"対象CSVが見つかりません: {target_csv}")

    with open(target_csv, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    filled = 0
    missing_id = []
    for row in rows:
        name = (row.get("企業名") or "").strip()
        ent_id = id_map.get(name, "").strip()
        if ent_id:
            url = f"{BASE_URL}/{ent_id}"
            if add_predicted_prefix:
                url = f"[推定] {url}"
            row["納品URL"] = url
            filled += 1
        else:
            row["納品URL"] = ""
            missing_id.append(name)

    if dry_run:
        print("=== Dry-run: 書き戻しは行いません ===")
        for row in rows:
            print(f"  {row['企業名']:<35} | {row['納品URL']}")
    else:
        with open(target_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["企業名", "納品URL"])
            writer.writeheader()
            writer.writerows(rows)

    return filled, missing_id, len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--predicted", action="store_true",
        help="URL の先頭に [推定] プレフィックスを付ける",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="ファイル書き戻しを行わず、生成結果を標準出力に表示するだけ",
    )
    args = parser.parse_args()

    id_map = build_enterprise_id_map(SRC_COMPANY_LIST)
    print(f"企業ID マップ構築完了: {len(id_map)} 社 (参照元: {SRC_COMPANY_LIST})")

    filled, missing, total = fill_delivery_urls(
        TARGET_CSV,
        id_map,
        add_predicted_prefix=args.predicted,
        dry_run=args.dry_run,
    )

    print()
    print(f"納品URL生成: {filled}/{total} 社")
    if missing:
        print(f"企業ID 未取得 ({len(missing)} 社): {SRC_COMPANY_LIST} に該当行なし")
        for name in missing:
            print(f"  - {name}")
    if not args.dry_run:
        print(f"書き戻し完了: {TARGET_CSV}")


if __name__ == "__main__":
    main()
