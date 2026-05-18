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

        入力CSVは以下3形式に自動対応:
            (a) 1列のみ「企業名」 → 8列に展開して保存
            (b) 2列「企業名」「ホームページURL」 → 8列に展開して保存
            (c) フルスキーマ (8列、ステータス列を含む) → そのまま読込

        空欄/None/列不足の行に対しても堅牢に動作:
            - 全セル空 or 企業名空 → スキップ + 警告ログ
            - csv.DictReader は不足列の値を None で返す → 全アクセスを None ガード

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

        # ヘッダーを覗いて入力形式を判定 (ステータス列の有無で決める)
        with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f)
            try:
                header = [h.strip() for h in next(r)]
            except StopIteration:
                logger.warning(f"CSVが空です: {self.csv_path}")
                return []

        col = CSV_COLUMNS
        is_full_schema = col["status"] in header

        if not is_full_schema:
            return self._read_minimal_csv(header)

        return self._read_full_schema_csv()

    def _read_minimal_csv(self, header: list) -> List[CompanyInfo]:
        """
        最小入力CSV (1列 or 2列) を読込み、8列スキーマに正規化して書き戻す。
        - 1列: 企業名のみ → status=未処理
        - 2列: 企業名,ホームページURL → URL有なら URL_FOUND、無なら 未処理
        """
        col = CSV_COLUMNS

        # 企業名キー (ヘッダーが「企業名」でなくても先頭列を企業名と見なす)
        name_key = col["company_name"] if col["company_name"] in header else (
            header[0] if header else col["company_name"]
        )
        # URLキー (2列目があればURLとして扱う)
        url_key = None
        if col["homepage_url"] in header:
            url_key = col["homepage_url"]
        elif len(header) >= 2:
            url_key = header[1]

        companies: List[CompanyInfo] = []
        skipped = 0
        with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if not row or all(
                    (v is None or str(v).strip() == "") for v in row.values()
                ):
                    skipped += 1
                    continue
                company_name = (row.get(name_key) or "").strip()
                if not company_name:
                    skipped += 1
                    logger.warning(f"  [skip] {i+2}行目: 企業名が空")
                    continue
                homepage_url = (
                    (row.get(url_key) or "").strip() if url_key else ""
                )
                company = CompanyInfo(
                    row_index=i,
                    name=company_name,
                    homepage_url=homepage_url,
                    status=ProcessStatus.URL_FOUND if homepage_url else ProcessStatus.PENDING,
                )
                companies.append(company)

        if skipped:
            logger.info(f"  空欄行をスキップ: {skipped}行")
        logger.info(
            f"企業リスト読み込み完了 (最小入力 {len(header)}列): {len(companies)}社"
        )
        # 8列フルスキーマに正規化して書き戻す
        self.save_company_list(companies)
        return companies

    def _read_full_schema_csv(self) -> List[CompanyInfo]:
        """フルスキーマCSV (8列、旧カラム互換含む) を null-safe に読み込む。"""
        companies: List[CompanyInfo] = []
        col = CSV_COLUMNS

        def _safe(row: dict, key: str, default: str = "") -> str:
            """None でも安全に strip() できる取得関数"""
            v = row.get(key)
            return (v if v is not None else default).strip()

        def _get_with_legacy(row: dict, key: str) -> str:
            value = _safe(row, col[key])
            if not value:
                for alias in LEGACY_COLUMN_ALIASES.get(key, []):
                    alias_val = _safe(row, alias)
                    if alias_val:
                        value = alias_val
                        break
            return value

        skipped = 0
        with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if not row or all(
                    (v is None or str(v).strip() == "") for v in row.values()
                ):
                    skipped += 1
                    continue

                company_name = _safe(row, col["company_name"])
                if not company_name:
                    skipped += 1
                    logger.warning(f"  [skip] {i+2}行目: 企業名が空")
                    continue

                status_str = _safe(row, col["status"], "未処理") or "未処理"
                try:
                    status = ProcessStatus(status_str)
                except ValueError:
                    logger.warning(
                        f"  [warn] {company_name}: 不明なステータス "
                        f"'{status_str}' → 未処理として扱います"
                    )
                    status = ProcessStatus.PENDING

                # URL候補列をパイプ区切りで読み込み
                candidates_raw = _safe(row, col["url_candidates"])
                url_candidates = [
                    u.strip() for u in candidates_raw.split("|") if u.strip()
                ] if candidates_raw else []

                company = CompanyInfo(
                    row_index=i,
                    name=company_name,
                    enterprise_id=_safe(row, col["enterprise_id"]),
                    homepage_url=_safe(row, col["homepage_url"]),
                    url_candidates=url_candidates,
                    frontend_app_url=_get_with_legacy(row, "frontend_url"),
                    status=status,
                    error_message=_safe(row, col["error_message"]),
                    screenshot_path=_safe(row, col["screenshot_path"]),
                    quality_check=_safe(row, col["quality_check"]),
                    quality_detail=_safe(row, col["quality_detail"]),
                )
                companies.append(company)

        if skipped:
            logger.info(f"  空欄行をスキップ: {skipped}行")
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
                    col["quality_check"]: company.quality_check,
                    col["quality_detail"]: company.quality_detail,
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

    def get_completed_companies(
        self,
        companies: List[CompanyInfo],
        require_delivery_url: bool = True,
    ) -> List[CompanyInfo]:
        """
        ステータス「完了」の企業を返す (Stage 4 verify_quality.py 用)。

        Args:
            companies: 全企業リスト
            require_delivery_url: True なら納品URL が空の行を除外。
                Stage 4 は納品URL を実機で開いて検証するので、URL がなければ
                検証しようがない。デフォルト True。
        """
        completed = [
            c for c in companies if c.status == ProcessStatus.COMPLETED
        ]
        if require_delivery_url:
            before = len(completed)
            completed = [c for c in completed if c.frontend_app_url]
            skipped = before - len(completed)
            if skipped:
                logger.info(
                    f"  Stage 4 フィルタ: 納品URL未確定の {skipped}社 をスキップ"
                )
        logger.info(
            f"品質チェック対象企業: {len(completed)}社 / 全{len(companies)}社"
        )
        return completed

    def get_pending_companies(
        self,
        companies: List[CompanyInfo],
        require_url: bool = False,
    ) -> List[CompanyInfo]:
        """
        未処理または途中の企業のみをフィルタして返す。

        Args:
            companies: 全企業リスト
            require_url: True なら homepage_url が空の行を除外 (Stage 2 用)。
                Stage 1 (select_urls.py) では False、Stage 2 (orchestrator.py)
                では True を指定して、URL未確定の行を確実にスキップする。

        Returns:
            処理可能な企業のリスト
        """
        pending = [c for c in companies if c.is_processable()]
        if require_url:
            before = len(pending)
            pending = [c for c in pending if c.homepage_url]
            skipped = before - len(pending)
            if skipped:
                logger.info(
                    f"  Stage 2 フィルタ: URL未入力の {skipped}社 をスキップ"
                )
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
                if not name:
                    continue

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

        logger.info(f"初期CSV作成完了: {len(normalized)}社 → {path}")
        return path
