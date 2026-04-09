"""
テスト: spreadsheet_manager.py — スプレッドシート管理

CSV読み書き・進捗管理の動作を検証する。
"""

import csv
import pytest
from pathlib import Path

from models import CompanyInfo, ProcessStatus
from spreadsheet_manager import SpreadsheetManager
from config import CSV_COLUMNS


@pytest.fixture
def tmp_csv(tmp_path):
    """テスト用一時CSVファイルパスを返す"""
    return tmp_path / "test_companies.csv"


@pytest.fixture
def sample_companies():
    """テスト用企業データを返す"""
    return [
        CompanyInfo(row_index=0, name="テスト株式会社A"),
        CompanyInfo(row_index=1, name="テスト株式会社B"),
        CompanyInfo(row_index=2, name="テスト株式会社C"),
    ]


class TestCreateInitialCSV:
    """初期CSV作成のテスト"""

    def test_create_csv(self, tmp_csv):
        """CSVファイルが正しく作成されること"""
        names = ["テスト株式会社A", "テスト株式会社B"]
        path = SpreadsheetManager.create_initial_csv(names, tmp_csv)

        assert path.exists()

        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2
        assert rows[0][CSV_COLUMNS["company_name"]] == "テスト株式会社A"
        assert rows[1][CSV_COLUMNS["company_name"]] == "テスト株式会社B"

    def test_create_csv_with_status(self, tmp_csv):
        """初期ステータスが"未処理"であること"""
        names = ["テスト株式会社"]
        SpreadsheetManager.create_initial_csv(names, tmp_csv)

        with open(tmp_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            row = next(reader)

        assert row[CSV_COLUMNS["status"]] == "未処理"

    def test_create_csv_with_enterprise_id(self, tmp_csv):
        """企業IDが自動生成されること"""
        names = ["株式会社Felnis"]
        SpreadsheetManager.create_initial_csv(names, tmp_csv)

        with open(tmp_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            row = next(reader)

        assert row[CSV_COLUMNS["enterprise_id"]] == "felnis"

    def test_create_csv_empty_names_skipped(self, tmp_csv):
        """空の企業名はスキップされること"""
        names = ["テスト株式会社", "", "  "]
        SpreadsheetManager.create_initial_csv(names, tmp_csv)

        with open(tmp_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1


class TestReadCompanyList:
    """企業リスト読み込みのテスト"""

    def test_read_existing_csv(self, tmp_csv):
        """正常なCSVを読み込めること"""
        names = ["テスト株式会社A", "テスト株式会社B"]
        SpreadsheetManager.create_initial_csv(names, tmp_csv)

        manager = SpreadsheetManager(tmp_csv)
        companies = manager.read_company_list()

        assert len(companies) == 2
        assert companies[0].name == "テスト株式会社A"
        assert companies[1].name == "テスト株式会社B"

    def test_read_with_status(self, tmp_csv):
        """ステータスが正しく復元されること"""
        names = ["テスト株式会社"]
        SpreadsheetManager.create_initial_csv(names, tmp_csv)

        manager = SpreadsheetManager(tmp_csv)
        companies = manager.read_company_list()

        assert companies[0].status == ProcessStatus.PENDING

    def test_read_nonexistent_csv(self, tmp_csv):
        """存在しないCSVはFileNotFoundError"""
        manager = SpreadsheetManager(tmp_csv)
        with pytest.raises(FileNotFoundError):
            manager.read_company_list()


class TestSaveAndUpdate:
    """企業リスト保存・更新のテスト"""

    def test_save_company_list(self, tmp_csv, sample_companies):
        """企業リストの保存と再読み込みが一致すること"""
        manager = SpreadsheetManager(tmp_csv)
        manager.save_company_list(sample_companies)

        loaded = manager.read_company_list()
        assert len(loaded) == 3
        for orig, loaded_c in zip(sample_companies, loaded):
            assert orig.name == loaded_c.name

    def test_update_company(self, tmp_csv, sample_companies):
        """個別企業の更新が反映されること"""
        manager = SpreadsheetManager(tmp_csv)
        manager.save_company_list(sample_companies)

        # 1社目を更新
        sample_companies[0].homepage_url = "https://example.com"
        sample_companies[0].status = ProcessStatus.URL_FOUND
        manager.update_company(sample_companies[0], sample_companies)

        loaded = manager.read_company_list()
        assert loaded[0].homepage_url == "https://example.com"
        assert loaded[0].status == ProcessStatus.URL_FOUND

    def test_get_pending_companies(self, tmp_csv, sample_companies):
        """未処理企業のフィルタリング"""
        sample_companies[0].status = ProcessStatus.COMPLETED
        sample_companies[1].status = ProcessStatus.ERROR

        manager = SpreadsheetManager(tmp_csv)
        pending = manager.get_pending_companies(sample_companies)

        assert len(pending) == 1
        assert pending[0].name == "テスト株式会社C"
