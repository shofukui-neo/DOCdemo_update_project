"""
テスト: models.py — データモデル

CompanyInfo と ProcessStatus の動作を検証する。
"""

import pytest
from models import (
    CompanyInfo,
    ProcessStatus,
    TRANSIENT_RETRY_MAX,
    format_transient_error,
    is_transient_error_message,
    parse_transient_retry_count,
)


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


# =============================================================================
# 一時的失敗の分類・リトライ予算
# =============================================================================
class TestIsTransientErrorMessage:
    """生成過程のUI/タイムアウト系エラーを一時的失敗と判定できること"""

    def test_login_email_field_timeout(self):
        msg = (
            'Page.wait_for_selector: Timeout 10000ms exceeded.\n'
            'Call log:\n  - waiting for locator("input[aria-label=\\"メールアドレス\\"]") to be visible'
        )
        assert is_transient_error_message(msg) is True

    def test_sidebar_click_timeout(self):
        msg = (
            'Locator.click: Timeout 30000ms exceeded.\n'
            'Call log:\n  - waiting for locator("[data-testid=\'stSidebar\']")'
            '.get_by_text("コンテンツ生成").first'
        )
        assert is_transient_error_message(msg) is True

    def test_target_closed(self):
        assert is_transient_error_message("Target closed") is True

    def test_page_closed(self):
        assert is_transient_error_message("Page closed") is True

    def test_net_err(self):
        assert is_transient_error_message("net::ERR_CONNECTION_REFUSED") is True

    def test_empty_message_is_not_transient(self):
        assert is_transient_error_message("") is False
        assert is_transient_error_message(None) is False

    def test_genuine_business_error_is_not_transient(self):
        """企業が見つからない等の業務ロジックエラーは一時的失敗ではない"""
        assert is_transient_error_message("企業IDが空です") is False
        assert is_transient_error_message("企業名が見つかりません") is False
        assert is_transient_error_message("URL not specified") is False


class TestTransientRetryEncoding:
    """エラーメッセージへのリトライ回数プレフィックス埋め込み"""

    def test_parse_no_prefix_returns_zero(self):
        assert parse_transient_retry_count("Timeout exceeded") == 0
        assert parse_transient_retry_count("") == 0
        assert parse_transient_retry_count(None) == 0

    def test_parse_with_prefix(self):
        assert parse_transient_retry_count("[一時的失敗 1/3] Timeout") == 1
        assert parse_transient_retry_count("[一時的失敗 2/3] Timeout") == 2
        assert parse_transient_retry_count("[一時的失敗 3/3] Timeout") == 3

    def test_format_first_failure(self):
        out = format_transient_error("Timeout exceeded", attempt=1)
        assert out == f"[一時的失敗 1/{TRANSIENT_RETRY_MAX}] Timeout exceeded"

    def test_format_strips_existing_prefix(self):
        """既存のプレフィックスがあれば置き換える (二重付与にならない)"""
        prev = f"[一時的失敗 1/{TRANSIENT_RETRY_MAX}] Timeout exceeded"
        out = format_transient_error(prev, attempt=2)
        assert out == f"[一時的失敗 2/{TRANSIENT_RETRY_MAX}] Timeout exceeded"
        assert out.count("[一時的失敗") == 1

    def test_format_empty_message(self):
        out = format_transient_error("", attempt=1)
        assert out.startswith(f"[一時的失敗 1/{TRANSIENT_RETRY_MAX}]")


class TestCompanyInfoTransientError:
    """CompanyInfo の一時的失敗関連メソッド"""

    def _company(self):
        return CompanyInfo(row_index=0, name="テスト株式会社")

    def test_is_transient_error_false_for_non_error_status(self):
        c = self._company()
        c.status = ProcessStatus.COMPLETED
        c.error_message = "Timeout exceeded"
        assert c.is_transient_error() is False

    def test_is_transient_error_false_for_error_with_business_message(self):
        c = self._company()
        c.mark_error("企業IDが空です")
        assert c.is_transient_error() is False

    def test_is_transient_error_true_for_error_with_transient_message(self):
        c = self._company()
        c.mark_error("Locator.click: Timeout 30000ms exceeded")
        assert c.is_transient_error() is True

    def test_mark_transient_error_increments_count(self):
        c = self._company()
        c.mark_transient_error("Timeout exceeded")
        assert c.status == ProcessStatus.ERROR
        assert parse_transient_retry_count(c.error_message) == 1

        c.mark_transient_error("Timeout exceeded again")
        assert parse_transient_retry_count(c.error_message) == 2

        c.mark_transient_error("yet another timeout")
        assert parse_transient_retry_count(c.error_message) == 3

    def test_transient_retry_remaining(self):
        c = self._company()
        # 1回目失敗
        c.mark_transient_error("Timeout")
        assert c.transient_retry_remaining() == TRANSIENT_RETRY_MAX - 1
        # 2回目失敗
        c.mark_transient_error("Timeout")
        assert c.transient_retry_remaining() == TRANSIENT_RETRY_MAX - 2
        # 上限到達
        c.mark_transient_error("Timeout")
        assert c.transient_retry_remaining() == 0

    def test_transient_retry_remaining_zero_for_non_transient(self):
        c = self._company()
        c.mark_error("genuine error")
        assert c.transient_retry_remaining() == 0


class TestIsProcessableWithTransient:
    """is_processable() が一時的失敗を再処理対象として扱うこと"""

    def _company(self):
        return CompanyInfo(row_index=0, name="テスト株式会社")

    def test_completed_not_processable(self):
        """既存挙動: 完了は再処理対象外"""
        c = self._company()
        c.status = ProcessStatus.COMPLETED
        assert c.is_processable() is False

    def test_skipped_not_processable(self):
        """既存挙動: スキップは再処理対象外"""
        c = self._company()
        c.mark_skipped("URL不明")
        assert c.is_processable() is False

    def test_permanent_error_not_processable(self):
        """業務ロジック起因のエラーは再処理対象外 (既存挙動)"""
        c = self._company()
        c.mark_error("企業IDが空です")
        assert c.is_processable() is False

    def test_transient_error_with_remaining_retries_processable(self):
        """新規挙動: 一時的失敗で残りリトライ回数があれば再処理対象"""
        c = self._company()
        c.mark_transient_error("Timeout exceeded")
        assert c.is_processable() is True

    def test_transient_error_after_max_retries_not_processable(self):
        """新規挙動: 一時的失敗が上限に達したら再処理対象外"""
        c = self._company()
        for _ in range(TRANSIENT_RETRY_MAX):
            c.mark_transient_error("Timeout exceeded")
        assert c.transient_retry_remaining() == 0
        assert c.is_processable() is False

    def test_raw_transient_message_without_prefix_processable(self):
        """既存CSVに残っている素のタイムアウトメッセージも自動的にリトライ対象"""
        c = self._company()
        c.mark_error('Page.wait_for_selector: Timeout 10000ms exceeded')
        # 「mark_error」で書かれたがメッセージは一時的パターン → リトライ対象
        assert c.is_transient_error() is True
        assert c.is_processable() is True

    def test_pending_still_processable(self):
        """既存挙動回帰: 未処理は引き続き処理対象"""
        c = self._company()
        assert c.is_processable() is True


class TestResetForRetry:
    """一時的失敗企業を再処理するために URL_FOUND へリセットするヘルパー"""

    def _company(self):
        return CompanyInfo(
            row_index=0, name="テスト株式会社",
            homepage_url="https://example.com",
        )

    def test_reset_clears_transient_error(self):
        """reset_for_transient_retry() で URL_FOUND + error_message 空 にリセットされる"""
        c = self._company()
        c.mark_transient_error("Page.wait_for_selector: Timeout 10000ms exceeded")
        assert c.status == ProcessStatus.ERROR
        # リセット呼び出し
        c.reset_for_transient_retry()
        assert c.status == ProcessStatus.URL_FOUND
        # error_message は履歴として残してもよいが、ここでは空に倒す
        # (リセットされたことを CSV 上で視覚的に確認できるよう)
        assert c.error_message == ""

    def test_reset_no_op_for_completed(self):
        """完了済はリセット対象外 (誤呼び出し防止)"""
        c = self._company()
        c.status = ProcessStatus.COMPLETED
        c.reset_for_transient_retry()
        assert c.status == ProcessStatus.COMPLETED

    def test_reset_no_op_for_permanent_error(self):
        """永続エラーはリセットしない"""
        c = self._company()
        c.mark_error("企業IDが空です")
        c.reset_for_transient_retry()
        # 永続エラーは保持
        assert c.status == ProcessStatus.ERROR
        assert "企業IDが空" in c.error_message
