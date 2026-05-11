"""
初期CSVファイル生成スクリプト

ユーザーから提供された企業名リストをCSVファイルに変換する。

更新履歴:
- 2026-04-22: 新規5社に完全入れ替え
"""

import logging
from spreadsheet_manager import SpreadsheetManager

logging.basicConfig(level=logging.INFO, format="%(message)s")

# 提供された企業リスト（2026-04-22 更新 — 新規5社に完全入れ替え）
COMPANY_NAMES = [
    "ブロードマインド株式会社",
    "株式会社4976ホールディングス",
    "株式会社クレーネル",
    "株式会社カークリニックアキヤマ",
    "株式会社テラ",
]

if __name__ == "__main__":
    csv_path = SpreadsheetManager.create_initial_csv(COMPANY_NAMES)
    print(f"\n✅ 初期CSV作成完了: {csv_path}")
    print(f"   登録企業数: {len(COMPANY_NAMES)}社")
    print("\n登録企業一覧:")
    for i, name in enumerate(COMPANY_NAMES, 1):
        print(f"  {i}. {name}")
