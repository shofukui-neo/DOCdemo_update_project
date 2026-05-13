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

from config import COMPANY_LIST_CSV, CSV_COLUMNS, DATA_DIR, LEGACY_COLUMN_ALIASES
from models import CompanyInfo, ProcessStatus

logger = logging.getLogger(__name__)


def flatten_company_names(items) -> List[str]:
    """企業名リストの各要素を改行で分割し、個別の企業名リストに正規化する。

    Colab で `COMPANY_NAMES = ["A\\nB\\nC"]` のように複数行を1要素に貼り付けた
    ケースを救う共通ユーティリティ。

    仕様:
    - 各要素を `\\n` (CRLF含む) で分割
    - 各行は strip
    - 空行は除外
    - None は除外、その他の非文字列は str() で文字列化
    - カンマや読点(、)では分割しない (企業名に含まれる可能性のため)
    """
    result = []
    for item in items:
        if item is None:
            continue
        text = str(item).replace("\r\n", "\n").replace("\r", "\n")
        for line in text.split("\n"):
            line = line.strip()
            if line:
                result.append(line)
    return result


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

        def _cell(row: dict, key: str, default: str = "") -> str:
            """カラム値を None セーフに取得。
            csv.DictReader は行のカラム数が見出しより少ない場合 None を返すため、
            row.get(..., default) だけでは .strip() が落ちる。"""
            value = row.get(col[key])
            if value is None or value == "":
                return default
            return value.strip()

        def _get_with_legacy(row: dict, key: str) -> str:
            """現在のカラム名で取得し、見つからない場合は旧名にフォールバック"""
            value = _cell(row, key)
            if not value:
                for alias in LEGACY_COLUMN_ALIASES.get(key, []):
                    alias_val = row.get(alias)
                    if alias_val:
                        value = alias_val.strip()
                        break
            return value

        with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                company_name = _cell(row, "company_name")
                if not company_name:
                    continue

                status_str = _cell(row, "status", "未処理")
                try:
                    status = ProcessStatus(status_str)
                except ValueError:
                    status = ProcessStatus.PENDING

                # URL候補列をパイプ区切りで読み込み
                candidates_raw = _cell(row, "url_candidates")
                url_candidates = [
                    u.strip() for u in candidates_raw.split("|") if u.strip()
                ] if candidates_raw else []

                company = CompanyInfo(
                    row_index=i,
                    name=company_name,
                    enterprise_id=_cell(row, "enterprise_id"),
                    homepage_url=_cell(row, "homepage_url"),
                    url_candidates=url_candidates,
                    frontend_app_url=_get_with_legacy(row, "frontend_url"),
                    status=status,
                    error_message=_cell(row, "error_message"),
                    screenshot_path=_cell(row, "screenshot_path"),
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
                    col["url_candidates"]: "|".join(company.url_candidates),
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

        # 改行が混じった入力 (Colab で複数行貼り付け等) を個別企業に正規化
        normalized = flatten_company_names(company_names)

        col = CSV_COLUMNS
        fieldnames = list(col.values())

        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for i, name in enumerate(normalized):
                company = CompanyInfo(row_index=i, name=name)
                writer.writerow({
                    col["company_name"]: company.name,
                    col["homepage_url"]: "",
                    col["url_candidates"]: "",
                    col["enterprise_id"]: company.enterprise_id,
                    col["frontend_url"]: "",
                    col["status"]: ProcessStatus.PENDING.value,
                    col["error_message"]: "",
                    col["screenshot_path"]: "",
                })

        logger.info(f"初期CSV作成完了: {len(company_names)}社 → {path}")
        return path
