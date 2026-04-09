"""
テスト: models.py — データモデル

CompanyInfo と ProcessStatus の動作を検証する。
"""

import pytest
from models import CompanyInfo, ProcessStatus


class TestProcessStatus:
    """ProcessStatus Enumのテスト"""

    def test_status_values(self):
        """全ステータス値が日本語で定義されていること"""
        assert ProcessStatus.PENDING.value == "未処理"
        assert ProcessStatus.URL_FOUND.value == "URL特定済"
        assert ProcessStatus.COMPANY_ADDED.value == "企業追加済"
        assert ProcessStatus.CONTENT_GENERATED.value == "コンテンツ生成済"
        assert ProcessStatus.IMAGE_UPLOADED.value == "画像UP済"
        assert ProcessStatus.COMPLETED.value == "完了"
        assert ProcessStatus.ERROR.value == "エラー"
        assert ProcessStatus.SKIPPED.value == "スキップ"

    def test_status_from_value(self):
        """日本語値からEnumを復元できること"""
        assert ProcessStatus("未処理") == ProcessStatus.PENDING
        assert ProcessStatus("完了") == ProcessStatus.COMPLETED


class TestCompanyInfoEnterpriseId:
    """企業ID自動生成ロジックのテスト"""

    def test_english_company_with_suffix(self):
        """英字企業名 + 株式会社"""
        c = CompanyInfo(row_index=0, name="one-hat株式会社")
        assert c.enterprise_id == "one-hat"

    def test_english_company_prefix(self):
        """株式会社 + 英字企業名"""
        c = CompanyInfo(row_index=0, name="株式会社Felnis")
        assert c.enterprise_id == "felnis"

    def test_japanese_company_suffix(self):
        """日本語企業名 + 株式会社"""
        c = CompanyInfo(row_index=0, name="伊勢住宅株式会社")
        assert c.enterprise_id == "伊勢住宅"

    def test_medical_corporation(self):
        """医療法人社団の除去"""
        c = CompanyInfo(row_index=0, name="医療法人社団日生会")
        assert c.enterprise_id == "日生会"

    def test_general_association(self):
        """一般社団法人の除去"""
        c = CompanyInfo(row_index=0, name="一般社団法人新経済連盟")
        assert c.enterprise_id == "新経済連盟"

    def test_public_foundation(self):
        """公益財団法人の除去"""
        c = CompanyInfo(row_index=0, name="公益財団法人日本ラグビーフットボール協会")
        assert c.enterprise_id == "日本ラグビーフットボール協会"

    def test_general_foundation(self):
        """一般財団法人の除去"""
        c = CompanyInfo(row_index=0, name="一般財団法人メンケン品質検査協会")
        assert c.enterprise_id == "メンケン品質検査協会"

    def test_tax_corporation(self):
        """税理士法人の除去"""
        c = CompanyInfo(row_index=0, name="KPMG税理士法人")
        assert c.enterprise_id == "kpmg"

    def test_mixed_english_with_spaces(self):
        """スペース含み英字企業名"""
        c = CompanyInfo(row_index=0, name="株式会社Select Buddy")
        assert c.enterprise_id == "select-buddy"

    def test_uppercase_english(self):
        """大文字英字企業名の小文字化"""
        c = CompanyInfo(row_index=0, name="AGC株式会社")
        assert c.enterprise_id == "agc"

    def test_alphanumeric_hyphen(self):
        """ハイフン含み英字企業名"""
        c = CompanyInfo(row_index=0, name="T-NEXT株式会社")
        assert c.enterprise_id == "t-next"

    def test_no_legal_entity(self):
        """法人格なしの名称"""
        c = CompanyInfo(row_index=0, name="ノーザンファーム")
        assert c.enterprise_id == "ノーザンファーム"

    def test_municipality(self):
        """自治体名"""
        c = CompanyInfo(row_index=0, name="岩泉町")
        assert c.enterprise_id == "岩泉町"

    def test_custom_id_not_overwritten(self):
        """手動で設定した企業IDは上書きされないこと"""
        c = CompanyInfo(row_index=0, name="テスト株式会社", enterprise_id="custom-id")
        assert c.enterprise_id == "custom-id"


class TestCompanyInfoMethods:
    """CompanyInfoのメソッドテスト"""

    def test_is_processable_pending(self):
        """未処理は処理可能"""
        c = CompanyInfo(row_index=0, name="テスト")
        assert c.is_processable() is True

    def test_is_processable_completed(self):
        """完了は処理不可"""
        c = CompanyInfo(row_index=0, name="テスト", status=ProcessStatus.COMPLETED)
        assert c.is_processable() is False

    def test_is_processable_error(self):
        """エラーは処理不可"""
        c = CompanyInfo(row_index=0, name="テスト", status=ProcessStatus.ERROR)
        assert c.is_processable() is False

    def test_is_processable_intermediate(self):
        """中間ステータスは処理可能（レジューム対応）"""
        c = CompanyInfo(row_index=0, name="テスト", status=ProcessStatus.URL_FOUND)
        assert c.is_processable() is True

    def test_mark_error(self):
        """エラー記録"""
        c = CompanyInfo(row_index=0, name="テスト")
        c.mark_error("テストエラー")
        assert c.status == ProcessStatus.ERROR
        assert c.error_message == "テストエラー"

    def test_mark_skipped(self):
        """スキップ記録"""
        c = CompanyInfo(row_index=0, name="テスト")
        c.mark_skipped("URL不明")
        assert c.status == ProcessStatus.SKIPPED
        assert c.error_message == "URL不明"

    def test_str_representation(self):
        """文字列表現"""
        c = CompanyInfo(row_index=0, name="テスト株式会社")
        s = str(c)
        assert "テスト株式会社" in s
        assert "テスト" in s
