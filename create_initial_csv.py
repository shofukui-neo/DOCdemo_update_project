"""
初期CSVファイル生成スクリプト

ユーザーから提供された企業名リストをCSVファイルに変換する。

更新履歴:
- 2026-04-22: 新規5社に完全入れ替え
- 2026-05-12: 新規30社に完全入れ替え
"""

import logging
from spreadsheet_manager import SpreadsheetManager

logging.basicConfig(level=logging.INFO, format="%(message)s")

# 提供された企業リスト（2026-05-12 更新 — 新規30社に完全入れ替え）
COMPANY_NAMES = [
    "株式会社明円ソフト開発",
    "白鳥製薬株式会社",
    "株式会社ヴァンティブ",
    "YKT株式会社",
    "片倉工業株式会社",
    "株式会社ソリューションジャパン",
    "三政テキスタイル株式会社",
    "田中産業株式会社",
    "株式会社アト",
    "富士シティオ株式会社",
    "ポーライト株式会社",
    "株式会社ドルフィンスルー",
    "株式会社jig.jp",
    "株式会社ティ・アイ・ディ",
    "特定非営利活動法人　ジャパンハート",
    "株式会社ブリングアップ史",
    "株式会社ステップ",
    "TOHOピクス株式会社",
    "株式会社マン・マシンインターフェース",
    "株式会社ビッグルーフ",
    "IIMヒューマン・ソリューション株式会社",
    "株式会社カクシン",
    "三愛電子工業株式会社",
    "ウェルス・マネジメント株式会社",
    "平岩建設株式会社",
    "防衛装備庁",
    "日信電子サービス株式会社",
    "神奈川県森林組合連合会",
    "株式会社ヌカベ",
    "株式会社ロジコ",
]

if __name__ == "__main__":
    csv_path = SpreadsheetManager.create_initial_csv(COMPANY_NAMES)
    print(f"\n✅ 初期CSV作成完了: {csv_path}")
    print(f"   登録企業数: {len(COMPANY_NAMES)}社")
    print("\n登録企業一覧:")
    for i, name in enumerate(COMPANY_NAMES, 1):
        print(f"  {i}. {name}")
