"""
flatten_company_names ユーティリティのテスト。

Colab で `COMPANY_NAMES = ["A\\nB\\nC"]` のように複数行を貼り付けた場合でも、
各行を個別の企業として認識できるように分割するための共通関数。
"""

import pytest
from spreadsheet_manager import flatten_company_names


class TestFlatten:
    def test_already_individual(self):
        """既に個別の文字列リストならそのまま。"""
        names = ["株式会社A", "株式会社B", "C 株式会社"]
        assert flatten_company_names(names) == names

    def test_newline_splits_into_multiple(self):
        """1要素に複数行が入っている場合は分割する。"""
        names = ["株式会社A\n株式会社B\n株式会社C"]
        assert flatten_company_names(names) == [
            "株式会社A", "株式会社B", "株式会社C",
        ]

    def test_mixed_with_individual_and_multiline(self):
        """個別要素と複数行要素が混在しても扱える。"""
        names = ["株式会社A", "株式会社B\n株式会社C", "株式会社D"]
        assert flatten_company_names(names) == [
            "株式会社A", "株式会社B", "株式会社C", "株式会社D",
        ]

    def test_crlf_normalized(self):
        """Windows形式の改行 (\\r\\n) も改行として扱う。"""
        names = ["株式会社A\r\n株式会社B"]
        assert flatten_company_names(names) == ["株式会社A", "株式会社B"]

    def test_blank_lines_dropped(self):
        """空行は除外する。"""
        names = ["株式会社A\n\n株式会社B\n\n\n"]
        assert flatten_company_names(names) == ["株式会社A", "株式会社B"]

    def test_strip_whitespace(self):
        """各行の前後空白は strip する。"""
        names = ["  株式会社A  \n\t株式会社B\t\n株式会社C   "]
        assert flatten_company_names(names) == [
            "株式会社A", "株式会社B", "株式会社C",
        ]

    def test_empty_list(self):
        assert flatten_company_names([]) == []

    def test_list_of_empty_strings(self):
        assert flatten_company_names(["", "  ", "\n\n"]) == []

    def test_preserves_japanese_punctuation(self):
        """企業名に含まれる『・』『、』などは区切り文字として扱わない。"""
        names = ["株式会社A・B", "株式会社C、D"]
        assert flatten_company_names(names) == [
            "株式会社A・B", "株式会社C、D",
        ]

    def test_comma_in_name_preserved(self):
        """カンマ区切りで企業を分けない (企業名にカンマが含まれる可能性のため)。"""
        names = ["株式会社A, 株式会社B"]
        assert flatten_company_names(names) == ["株式会社A, 株式会社B"]

    def test_user_real_case(self):
        """ユーザが実際に遭遇したケース。"""
        names = [
            "株式会社明円ソフト開発\n白鳥製薬株式会社\n株式会社ヴァンティブ\nYKT株式会社"
        ]
        result = flatten_company_names(names)
        assert len(result) == 4
        assert result[0] == "株式会社明円ソフト開発"
        assert result[1] == "白鳥製薬株式会社"
        assert result[2] == "株式会社ヴァンティブ"
        assert result[3] == "YKT株式会社"

    def test_handles_non_string_gracefully(self):
        """文字列以外を渡されてもクラッシュしない (None は除外、数値は文字列化)。"""
        names = [None, 123, "株式会社A"]
        assert flatten_company_names(names) == ["123", "株式会社A"]
