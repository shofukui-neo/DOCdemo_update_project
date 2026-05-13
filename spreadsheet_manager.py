"""
DOCdemo 自動化フロー — スプレッドシート管理モジュール

CSVファイルを用いた企業リストの読み書きを担当する。
Google Sheets対応も将来的に拡張可能。

CSV運用 (2026-05-13 更新):
- メインCSV (data/company_list.csv) は 6カラムに簡素化:
  企業名 / ホームページURL / 企業ID / 納品URL / ステータス / エラー詳細
- URL候補はサイドカーJSON (data/url_candidates/<企業ID>.json) に分離。
  パイプ区切りで列幅が広がる見にくさを解消。
- スクリーンショットパスは logs/automation.log にのみ記録 (CSV からは削除)。
- 入力CSVは下記3形式に自動対応:
    (a) 1列のみ: 「企業名」                    → 内部で6列に展開
    (b) 2列:    「企業名」「ホームページURL」  → 内部で6列に展開
    (c) 6列以上: 既存スキーマ (旧8列も読込可)
"""

import csv
import json
import logging
from pathlib import Path
from typing import List, Optional

from config import (
    COMPANY_LIST_CSV,
    CSV_COLUMNS,
    LEGACY_COLUMN_ALIASES,
    URL_CANDIDATES_DIR,
)
from models import CompanyInfo, ProcessStatus

logger = logging.getLogger(__name__)


def _candidates_file(enterprise_id: str) -> Path:
    """指定企業IDのURL候補JSONファイルパスを返す"""
    return URL_CANDIDATES_DIR / f"{enterprise_id}.json"


def read_url_candidates(enterprise_id: str) -> List[str]:
    """サイドカーJSONからURL候補リストを読み込む。なければ空リスト。"""
    if not enterprise_id:
        return []
    path = _candidates_file(enterprise_id)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(u).strip() for u in data if str(u).strip()]
        if isinstance(data, dict) and isinstance(data.get("candidates"), list):
            return [str(u).strip() for u in data["candidates"] if str(u).strip()]
    except Exception as e:
        logger.warning(f"URL候補JSON読込失敗 ({path}): {e}")
    return []


def write_url_candidates(enterprise_id: str, candidates: List[str]):
    """サイドカーJSONにURL候補リストを書き込む。空なら既存ファイルを削除。"""
    if not enterprise_id:
        return
    URL_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    path = _candidates_file(enterprise_id)
    cleaned = [str(u).strip() for u in candidates if str(u).strip()]
    if not cleaned:
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"enterprise_id": enterprise_id, "candidates": cleaned},
                f, ensure_ascii=False, indent=2,
            )
    except Exception as e:
        logger.warning(f"URL候補JSON書込失敗 ({path}): {e}")


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

        以下3形式に自動対応:
            (a) 1列のみ(企業名): 内部で6列スキーマに展開
            (b) 2列(企業名,ホームページURL): 内部で6列スキーマに展開
            (c) フルスキーマ(6列以上, 旧8列含む): そのまま読込

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

        # ヘッダー行を見て入力形式を判定
        with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                logger.warning(f"CSVが空です: {self.csv_path}")
                return []

        header_clean = [h.strip() for h in header]
        col = CSV_COLUMNS

        # 入力形式判定: ヘッダーに「ステータス」が含まれていればフルスキーマ、
        # それ以外は最小入力 (1列 or 2列) と見なす
        is_full_schema = col["status"] in header_clean

        if not is_full_schema:
            return self._read_minimal_csv(header_clean)

        return self._read_full_schema_csv()

    def _read_minimal_csv(self, header_clean: List[str]) -> List[CompanyInfo]:
        """
        最小入力CSV (1列 or 2列) を読み込み、内部スキーマに展開する。
        - 1列: 「企業名」のみ → ステータス=未処理
        - 2列: 「企業名」「ホームページURL」 → URL有なら URL_FOUND、無なら 未処理
        """
        col = CSV_COLUMNS
        company_col = col["company_name"]
        url_col = col["homepage_url"]

        # 企業名カラムを必ず特定 (ヘッダーが「企業名」でなくても先頭列を企業名と見なす)
        if company_col in header_clean:
            name_key = company_col
        else:
            name_key = header_clean[0] if header_clean else company_col

        # 2列目があればURLとして扱う
        url_key = None
        if url_col in header_clean:
            url_key = url_col
        elif len(header_clean) >= 2:
            url_key = header_clean[1]

        companies: List[CompanyInfo] = []
        skipped_empty_rows = 0
        with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if not row or all(
                    (v is None or str(v).strip() == "") for v in row.values()
                ):
                    skipped_empty_rows += 1
                    continue

                company_name = (row.get(name_key) or "").strip()
                if not company_name:
                    skipped_empty_rows += 1
                    logger.warning(
                        f"  [skip] {i+2}行目: 企業名が空のためスキップ"
                    )
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
                # サイドカーから既存URL候補を読み込む (再実行時の継続性)
                company.url_candidates = read_url_candidates(company.enterprise_id)
                companies.append(company)

        if skipped_empty_rows:
            logger.info(f"  空欄行をスキップ: {skipped_empty_rows}行")

        logger.info(
            f"企業リスト読み込み完了 (最小入力形式 {len(header_clean)}列): "
            f"{len(companies)}社"
        )
        # 6列フルスキーマに正規化して書き戻す (後続のステータス更新で必要)
        self.save_company_list(companies)
        return companies

    def _read_full_schema_csv(self) -> List[CompanyInfo]:
        """
        フルスキーマCSV (6列以上、旧8列も含む) を読み込む。

        空欄/None/列不足に対して堅牢に動作する:
        - 空セル: csv.DictReader は "" を返すが、行のセル数がヘッダーより少ない
          場合は不足キーの値が None になる → 全アクセスで None ガード必須
        - 完全空行/企業名空行: スキップして警告ログ
        - 不正なステータス文字列: PENDING にフォールバック
        """
        companies: List[CompanyInfo] = []
        col = CSV_COLUMNS

        def _safe(row: dict, key: str, default: str = "") -> str:
            """row.get(key) が None でも安全に strip() できる取得関数"""
            v = row.get(key)
            return (v if v is not None else default).strip()

        def _get_with_legacy(row: dict, key: str) -> str:
            """現在のカラム名で取得し、見つからない場合は旧名にフォールバック"""
            value = _safe(row, col[key])
            if not value:
                for alias in LEGACY_COLUMN_ALIASES.get(key, []):
                    alias_val = _safe(row, alias)
                    if alias_val:
                        value = alias_val
                        break
            return value

        skipped_empty_rows = 0
        with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                # 全セル空 or 完全に空行 → スキップ
                if not row or all(
                    (v is None or str(v).strip() == "") for v in row.values()
                ):
                    skipped_empty_rows += 1
                    continue

                company_name = _safe(row, col["company_name"])
                if not company_name:
                    skipped_empty_rows += 1
                    logger.warning(
                        f"  [skip] {i+2}行目: 企業名が空のためスキップ"
                    )
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

                company = CompanyInfo(
                    row_index=i,
                    name=company_name,
                    enterprise_id=_safe(row, col["enterprise_id"]),
                    homepage_url=_safe(row, col["homepage_url"]),
                    frontend_app_url=_get_with_legacy(row, "frontend_url"),
                    status=status,
                    error_message=_safe(row, col["error_message"]),
                )

                # URL候補: 旧スキーマ (CSV「URL候補」列) からの移行を優先
                legacy_candidates_raw = _safe(row, "URL候補")
                if legacy_candidates_raw:
                    candidates = [
                        u.strip() for u in legacy_candidates_raw.split("|") if u.strip()
                    ]
                    company.url_candidates = candidates
                    # 移行: サイドカーJSONに書き出す
                    if candidates:
                        write_url_candidates(company.enterprise_id, candidates)
                else:
                    company.url_candidates = read_url_candidates(company.enterprise_id)

                companies.append(company)

        if skipped_empty_rows:
            logger.info(f"  空欄行をスキップ: {skipped_empty_rows}行")
        logger.info(f"企業リスト読み込み完了: {len(companies)}社")
        return companies

    def save_company_list(self, companies: List[CompanyInfo]):
        """
        企業リスト全体をCSVファイルに保存する。
        URL候補はサイドカーJSONにも書き出す。

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
                })

                # URL候補はサイドカーJSONに分離保存
                if company.url_candidates:
                    write_url_candidates(company.enterprise_id, company.url_candidates)

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
                })

        logger.info(f"初期CSV作成完了: {len(company_names)}社 → {path}")
        return path
