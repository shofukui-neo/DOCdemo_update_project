"""
DOCdemo 自動化フロー — スプレッドシート管理モジュール

CSVファイルを用いた企業リストの読み書きを担当する。
Google Sheets対応も将来的に拡張可能。
"""

import csv
import logging
import os
from pathlib import Path
from typing import List, Optional

from config import COMPANY_LIST_CSV, CSV_COLUMNS, DATA_DIR
from models import CompanyInfo, ProcessStatus

logger = logging.getLogger(__name__)


class SpreadsheetManager:
    """企業リストCSVの読み書きを管理するクラス"""

    def __init__(self, csv_path: Optional[Path] = None):
        """
        Args:
            csv_path: CSVファイルのパス。Noneの場合はconfig.pyのデフォルトを使用。
        """
        self.csv_path = csv_path or COMPANY_LIST_CSV
        self._ensure_data_dir()

    def _ensure_data_dir(self):
        """データディレクトリが存在しない場合は作成"""
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

    def read_company_list(self) -> List[CompanyInfo]:
        """
        CSVファイルから企業リストを読み込み、CompanyInfoリストを返す。

        Returns:
            List[CompanyInfo]: 企業情報のリスト

        Raises:
            FileNotFoundError: CSVファイルが存在しない場合
        """
        if not self.csv_path.exists():
            raise FileNotFoundError(
                f"企業リストCSVが見つかりません: {self.csv_path}\n"
                f"先に create_initial_csv() で初期CSVを作成してください。"
            )

        companies = []
        col = CSV_COLUMNS

        with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                company_name = row.get(col["company_name"], "").strip()
                if not company_name:
                    continue

                status_str = row.get(col["status"], "未処理").strip()
                try:
                    status = ProcessStatus(status_str)
                except ValueError:
                    status = ProcessStatus.PENDING

                company = CompanyInfo(
                    row_index=i,
                    name=company_name,
                    enterprise_id=row.get(col["enterprise_id"], "").strip(),
                    homepage_url=row.get(col["homepage_url"], "").strip(),
                    frontend_app_url=row.get(col["frontend_url"], "").strip(),
                    status=status,
                    error_message=row.get(col["error_message"], "").strip(),
                    screenshot_path=row.get(col["screenshot_path"], "").strip(),
                )
                companies.append(company)

        logger.info(f"企業リスト読み込み完了: {len(companies)}社")
        return companies

    def save_company_list(self, companies: List[CompanyInfo]):
        """
        企業リスト全体をCSVファイルに保存する。

        Args:
            companies: 保存する企業情報のリスト
        """
        col = CSV_COLUMNS
        fieldnames = list(col.values())

        with open(self.csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for company in companies:
                writer.writerow({
                    col["company_name"]: company.name,
                    col["homepage_url"]: company.homepage_url,
                    col["enterprise_id"]: company.enterprise_id,
                    col["frontend_url"]: company.frontend_app_url,
                    col["status"]: company.status.value,
                    col["error_message"]: company.error_message,
                    col["screenshot_path"]: company.screenshot_path,
                })

        logger.info(f"企業リスト保存完了: {len(companies)}社 → {self.csv_path}")

    def update_company(self, company: CompanyInfo, companies: List[CompanyInfo]):
        """
        特定の企業の情報を更新し、CSV全体を保存する。

        Args:
            company: 更新対象の企業
            companies: 全企業リスト
        """
        # row_indexで該当企業を探して更新
        for i, c in enumerate(companies):
            if c.row_index == company.row_index:
                companies[i] = company
                break

        self.save_company_list(companies)
        logger.debug(f"企業情報更新: {company.name} → {company.status.value}")

    def get_pending_companies(self, companies: List[CompanyInfo]) -> List[CompanyInfo]:
        """
        未処理または途中の企業のみをフィルタして返す。

        Args:
            companies: 全企業リスト

        Returns:
            処理可能な企業のリスト
        """
        pending = [c for c in companies if c.is_processable()]
        logger.info(f"処理対象企業: {len(pending)}社 / 全{len(companies)}社")
        return pending

    @staticmethod
    def create_initial_csv(
        company_names: List[str],
        csv_path: Optional[Path] = None,
    ) -> Path:
        """
        企業名リストから初期CSVファイルを作成する。

        Args:
            company_names: 企業名のリスト
            csv_path: 保存先パス。Noneの場合はconfig.pyのデフォルト。

        Returns:
            作成したCSVファイルのパス
        """
        path = csv_path or COMPANY_LIST_CSV
        path.parent.mkdir(parents=True, exist_ok=True)

        col = CSV_COLUMNS
        fieldnames = list(col.values())

        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for i, name in enumerate(company_names):
                name = name.strip()
                if not name:
                    continue

                company = CompanyInfo(row_index=i, name=name)
                writer.writerow({
                    col["company_name"]: company.name,
                    col["homepage_url"]: "",
                    col["enterprise_id"]: company.enterprise_id,
                    col["frontend_url"]: "",
                    col["status"]: ProcessStatus.PENDING.value,
                    col["error_message"]: "",
                    col["screenshot_path"]: "",
                })

        logger.info(f"初期CSV作成完了: {len(company_names)}社 → {path}")
        return path
