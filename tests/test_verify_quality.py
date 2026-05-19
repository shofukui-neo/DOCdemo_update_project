"""
テスト: verify_quality.py — Stage 4 品質チェック

品質保持テスト。Phase 2+ の高速化リファクタリングが既存の判定ロジックを
壊していないことを検証する。
"""

import csv
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import CompanyInfo, ProcessStatus
from verify_quality import (
    CheckResult,
    QualityVerifier,
    _analyze_chat_reply,
    _should_skip_after_http,
)


# =============================================================================
# CheckResult: 既存判定ロジックのリグレッション防止
# =============================================================================
class TestCheckResult:
    def test_overall_empty(self):
        assert CheckResult().overall() == ""

    def test_overall_all_ok(self):
        r = CheckResult()
        for label in CheckResult.LABELS:
            r.set(label, True)
        assert r.overall() == "OK"

    def test_overall_with_ng(self):
        r = CheckResult()
        r.set("HTTP", True)
        r.set("企業名", False, "ID不一致")
        r.set("背景画像", True)
        r.set("FAQ", True)
        r.set("AIチャット", True)
        assert r.overall() == "NG"

    def test_overall_with_skip_only(self):
        r = CheckResult()
        r.set("HTTP", True)
        r.set("企業名", True)
        r.set("背景画像", True)
        r.set("FAQ", True)
        r.skip("AIチャット", "ボタン未検出")
        assert r.overall() == "部分OK"

    def test_overall_ng_dominates_skip(self):
        """NG と SKIP が両方あるなら NG。Phase 2 の 4xx 早期SKIP で重要"""
        r = CheckResult()
        r.set("HTTP", False, "404")
        r.skip("企業名", "HTTP失敗")
        r.skip("背景画像", "HTTP失敗")
        r.skip("FAQ", "HTTP失敗")
        r.skip("AIチャット", "HTTP失敗")
        assert r.overall() == "NG"

    def test_detail_omits_none(self):
        """未設定の項目は detail() に含まれない"""
        r = CheckResult()
        r.set("HTTP", True, "status=200")
        d = r.detail()
        assert "HTTP=OK(status=200)" in d
        assert "企業名" not in d


# =============================================================================
# Phase 2: _should_skip_after_http
# =============================================================================
class TestShouldSkipAfterHttp:
    """HTTP 応答後に残り4項目を早期 SKIP すべきかの判定。"""

    def test_2xx_does_not_skip(self):
        assert _should_skip_after_http(200) is False
        assert _should_skip_after_http(204) is False
        assert _should_skip_after_http(299) is False

    def test_3xx_does_not_skip(self):
        """リダイレクトは Playwright が追従済のはずだが念のため"""
        assert _should_skip_after_http(301) is False
        assert _should_skip_after_http(302) is False
        assert _should_skip_after_http(399) is False

    def test_4xx_skips(self):
        assert _should_skip_after_http(400) is True
        assert _should_skip_after_http(401) is True
        assert _should_skip_after_http(403) is True
        assert _should_skip_after_http(404) is True
        assert _should_skip_after_http(499) is True

    def test_5xx_does_not_skip(self):
        """5xx は ServerDownError 経路で処理される。ここは False"""
        assert _should_skip_after_http(500) is False
        assert _should_skip_after_http(503) is False

    def test_zero_does_not_skip(self):
        """status=0 (response None) は既存例外分岐に委ねる"""
        assert _should_skip_after_http(0) is False


# =============================================================================
# Phase 1 リグレッション: _check_company_name (sync版)
# =============================================================================
class TestCheckCompanyName:
    def _verifier(self):
        # 実 CSV はテスト対象外。SpreadsheetManager は __init__ で
        # 親ディレクトリのみ確認するので、存在する data/ を指せばOK。
        return QualityVerifier(csv_path=Path("data/company_list.csv"))

    def _company(self, name="株式会社サンプル", ent_id="sample"):
        return CompanyInfo(
            row_index=0, name=name, enterprise_id=ent_id,
            status=ProcessStatus.COMPLETED,
        )

    def test_name_in_header_text_ok(self):
        v = self._verifier()
        c = self._company()
        r = CheckResult()
        v._check_company_name(c, title="Demo", header_text="株式会社サンプル | TOP", result=r)
        assert r.items["企業名"] == "OK"

    def test_id_in_title_ok(self):
        v = self._verifier()
        c = self._company()
        r = CheckResult()
        v._check_company_name(c, title="sample | カジュアル面談", header_text="", result=r)
        assert r.items["企業名"] == "OK"

    def test_id_in_header_text_does_not_match(self):
        """企業ID は title のみで判定 (header_text は使わない、false positive 防止)"""
        v = self._verifier()
        c = self._company()
        r = CheckResult()
        v._check_company_name(c, title="Other", header_text="... sample ...", result=r)
        assert r.items["企業名"] == "NG"

    def test_nothing_matches_ng(self):
        v = self._verifier()
        c = self._company()
        r = CheckResult()
        v._check_company_name(c, title="他社A", header_text="他社B | TOP", result=r)
        assert r.items["企業名"] == "NG"


# =============================================================================
# Phase 1 リグレッション: _check_faq
# =============================================================================
class TestCheckFaq:
    def _verifier(self):
        return QualityVerifier(csv_path=Path("data/company_list.csv"))

    def _company(self):
        return CompanyInfo(
            row_index=0, name="株式会社サンプル", enterprise_id="sample",
            status=ProcessStatus.COMPLETED,
        )

    def test_faq_pattern_and_company_name_ok(self):
        v, c, r = self._verifier(), self._company(), CheckResult()
        v._check_faq(c, "FAQ 1: 株式会社サンプルの強みは ...", r)
        assert r.items["FAQ"] == "OK"

    def test_q_pattern_and_company_name_ok(self):
        v, c, r = self._verifier(), self._company(), CheckResult()
        v._check_faq(c, "Q1: 株式会社サンプル の事業内容は？", r)
        assert r.items["FAQ"] == "OK"

    def test_yokuaru_shitsumon_pattern_ok(self):
        v, c, r = self._verifier(), self._company(), CheckResult()
        v._check_faq(c, "よくある質問\n株式会社サンプルについて", r)
        assert r.items["FAQ"] == "OK"

    def test_faq_pattern_without_company_ng(self):
        """他社混入疑い"""
        v, c, r = self._verifier(), self._company(), CheckResult()
        v._check_faq(c, "FAQ 1: 弊社の方針は ...", r)
        assert r.items["FAQ"] == "NG"

    def test_no_faq_pattern_ng(self):
        v, c, r = self._verifier(), self._company(), CheckResult()
        v._check_faq(c, "株式会社サンプルへようこそ", r)
        assert r.items["FAQ"] == "NG"

    def test_empty_text_skip(self):
        v, c, r = self._verifier(), self._company(), CheckResult()
        v._check_faq(c, "", r)
        assert r.items["FAQ"] == "SKIP"


# =============================================================================
# 配信CSV読み込み (回帰防止)
# =============================================================================
class TestDeliveryCsvIO:
    def test_read_strips_predicted_prefix(self, tmp_path):
        p = tmp_path / "del.csv"
        p.write_text(
            "企業名,納品URL\n"
            "株式会社A,[推定] https://example.com/a-corp\n"
            "株式会社B,https://example.com/b-corp\n",
            encoding="utf-8-sig",
        )
        v = QualityVerifier(delivery_csv_path=p)
        companies = v._read_delivery_csv(p)
        assert len(companies) == 2
        assert companies[0].frontend_app_url == "https://example.com/a-corp"
        assert companies[0].enterprise_id == "a-corp"
        assert companies[1].frontend_app_url == "https://example.com/b-corp"
        assert companies[1].enterprise_id == "b-corp"

    def test_write_includes_quality_columns(self, tmp_path):
        p = tmp_path / "del.csv"
        p.write_text(
            "企業名,納品URL\n株式会社A,https://example.com/a\n",
            encoding="utf-8-sig",
        )
        v = QualityVerifier(delivery_csv_path=p)
        companies = v._read_delivery_csv(p)
        companies[0].quality_check = "OK"
        companies[0].quality_detail = "HTTP=OK / 企業名=OK"
        v._save_delivery_csv(companies)

        with open(p, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert rows[0]["品質チェック"] == "OK"
        assert rows[0]["品質チェック詳細"] == "HTTP=OK / 企業名=OK"


# =============================================================================
# Phase 2: _verify_one 統合動作 (Playwright Page をモック)
# =============================================================================
def _mock_response(status: int):
    """Playwright Response モック"""
    r = MagicMock()
    r.status = status
    return r


def _mock_page(response_status: int = 200):
    """
    Playwright Page を AsyncMock で組む。
    page.goto は指定 status の Response を返し、それ以外の async メソッドは
    無害なデフォルトを返すようにする。
    """
    page = MagicMock()
    page.goto = AsyncMock(return_value=_mock_response(response_status))
    page.wait_for_load_state = AsyncMock(return_value=None)
    page.screenshot = AsyncMock(return_value=None)
    page.evaluate = AsyncMock(
        return_value={
            "title": "sample-corp | 面談",
            "headerText": "株式会社サンプル | TOP",
            "bodyText": "FAQ 1: 株式会社サンプルの強みは...",
        }
    )
    page.title = AsyncMock(return_value="sample-corp")
    page.wait_for_timeout = AsyncMock(return_value=None)
    # ロケータ系は AI チャットの起動ボタン探索で呼ばれる
    locator = MagicMock()
    locator.count = AsyncMock(return_value=0)
    locator.first = MagicMock()
    locator.first.wait_for = AsyncMock(return_value=None)
    page.locator = MagicMock(return_value=locator)
    return page


class TestVerifyOnePhase2:
    """Phase 2: 4xx で残り4項目が SKIP され早期returnすることを検証"""

    def _verifier(self):
        return QualityVerifier(csv_path=Path("data/company_list.csv"))

    def _company(self):
        return CompanyInfo(
            row_index=0, name="株式会社サンプル", enterprise_id="sample-corp",
            frontend_app_url="https://example.com/sample-corp",
            status=ProcessStatus.COMPLETED,
        )

    @pytest.mark.asyncio
    async def test_4xx_skips_remaining_checks(self):
        """404 が返ったら 企業名/背景画像/FAQ/AIチャット は SKIP され、evaluate は呼ばれない"""
        v = self._verifier()
        c = self._company()
        page = _mock_page(response_status=404)

        result = await v._verify_one(c, page)

        assert result.items["HTTP"] == "NG"
        assert result.items["企業名"] == "SKIP"
        assert result.items["背景画像"] == "SKIP"
        assert result.items["FAQ"] == "SKIP"
        assert result.items["AIチャット"] == "SKIP"
        assert result.overall() == "NG"
        # 4xx ではページ描画後の処理 (evaluate / screenshot) を呼ばずに早期return
        page.evaluate.assert_not_called()
        page.screenshot.assert_not_called()

    @pytest.mark.asyncio
    async def test_2xx_continues_full_check(self):
        """200 が返ったら通常通り全項目評価へ進む (evaluate が呼ばれる)"""
        v = self._verifier()
        c = self._company()
        page = _mock_page(response_status=200)

        result = await v._verify_one(c, page)

        assert result.items["HTTP"] == "OK"
        # evaluate を呼んで残り項目を評価したことを確認
        page.evaluate.assert_called()
        # 企業名は header_text に企業名が入っているので OK
        assert result.items["企業名"] == "OK"
        # FAQ は bodyText にパターン+企業名があるので OK
        assert result.items["FAQ"] == "OK"

    @pytest.mark.asyncio
    async def test_4xx_overall_matches_pre_phase2(self):
        """品質保持: 4xx の overall 判定 = 既存挙動と一致 (= NG)"""
        v = self._verifier()
        c = self._company()
        page = _mock_page(response_status=403)

        result = await v._verify_one(c, page)
        assert result.overall() == "NG"


# =============================================================================
# Phase 3: 並列ワーカー (_run_parallel)
# =============================================================================
class _FakeContext:
    """context.new_page() を呼ぶたびに新しい MagicMock を返す簡易 fake"""
    def __init__(self):
        self.pages_created = 0

    async def new_page(self):
        self.pages_created += 1
        p = MagicMock()
        p.close = AsyncMock(return_value=None)
        return p


class TestRunParallel:
    """並列ワーカーの基本的な correctness を mock 経由で検証"""

    def _verifier(self):
        return QualityVerifier(csv_path=Path("data/company_list.csv"))

    def _company(self, name, ent_id):
        return CompanyInfo(
            row_index=0, name=name, enterprise_id=ent_id,
            frontend_app_url=f"https://example.com/{ent_id}",
            status=ProcessStatus.COMPLETED,
        )

    def _ok_result(self):
        r = CheckResult()
        for label in CheckResult.LABELS:
            r.set(label, True)
        return r

    @pytest.mark.asyncio
    async def test_processes_all_companies(self, monkeypatch):
        """3社・並列度2 で全社の結果が apply される"""
        v = self._verifier()
        targets = [
            self._company("A", "a"),
            self._company("B", "b"),
            self._company("C", "c"),
        ]
        ctx = _FakeContext()

        async def fake_verify(company, page):
            return self._ok_result()

        applied = []
        monkeypatch.setattr(v, "_verify_one", fake_verify)
        monkeypatch.setattr(
            v, "_apply_result",
            lambda c, lst, r: applied.append(c.name),
        )

        await v._run_parallel(
            targets, companies_list=targets, context=ctx, max_concurrent=2,
        )
        assert sorted(applied) == ["A", "B", "C"]
        assert ctx.pages_created == 3

    @pytest.mark.asyncio
    async def test_max_concurrent_respected(self, monkeypatch):
        """並列度2 のとき同時実行数 ≤ 2 が守られる (5社投入)"""
        import asyncio as _aio
        v = self._verifier()
        targets = [self._company(f"C{i}", f"c{i}") for i in range(5)]
        ctx = _FakeContext()

        active = [0]
        max_active = [0]

        async def fake_verify(company, page):
            active[0] += 1
            max_active[0] = max(max_active[0], active[0])
            await _aio.sleep(0.02)
            active[0] -= 1
            return self._ok_result()

        monkeypatch.setattr(v, "_verify_one", fake_verify)
        monkeypatch.setattr(v, "_apply_result", lambda c, lst, r: None)

        await v._run_parallel(
            targets, companies_list=targets, context=ctx, max_concurrent=2,
        )
        assert max_active[0] <= 2
        # 並列度2 なので少なくとも2社は同時実行されたはず
        assert max_active[0] == 2

    @pytest.mark.asyncio
    async def test_parallel_is_faster_than_serial(self, monkeypatch):
        """5社×並列度5 の実時間 < 5社直列の実時間 (parallelism 動作確認)"""
        import asyncio as _aio
        import time
        v = self._verifier()
        targets = [self._company(f"C{i}", f"c{i}") for i in range(5)]
        ctx = _FakeContext()
        per_company_sleep = 0.05

        async def fake_verify(company, page):
            await _aio.sleep(per_company_sleep)
            return self._ok_result()

        monkeypatch.setattr(v, "_verify_one", fake_verify)
        monkeypatch.setattr(v, "_apply_result", lambda c, lst, r: None)

        t0 = time.monotonic()
        await v._run_parallel(
            targets, companies_list=targets, context=ctx, max_concurrent=5,
        )
        elapsed = time.monotonic() - t0
        # 5社直列なら最低 5 × per_company_sleep。並列5なら ~1× で済むはず。
        # マージン取って 3× 未満であれば並列に動いていると判断。
        assert elapsed < per_company_sleep * 3

    @pytest.mark.asyncio
    async def test_per_company_exception_isolated(self, monkeypatch):
        """1社が一般例外で失敗しても他社は完走しその社は エラー扱いになる"""
        v = self._verifier()
        targets = [
            self._company("OK1", "ok1"),
            self._company("BAD", "bad"),
            self._company("OK2", "ok2"),
        ]
        ctx = _FakeContext()

        async def fake_verify(company, page):
            if company.name == "BAD":
                raise RuntimeError("intentional test exception")
            return self._ok_result()

        applied = []
        errored = []
        monkeypatch.setattr(v, "_verify_one", fake_verify)
        monkeypatch.setattr(
            v, "_apply_result",
            lambda c, lst, r: applied.append(c.name),
        )
        # 例外時の record 経路をテスト用に hook
        original_mark_error = CompanyInfo.mark_error

        def track_mark_error(self_company, message):
            errored.append(self_company.name)
            original_mark_error(self_company, message)

        monkeypatch.setattr(CompanyInfo, "mark_error", track_mark_error)
        monkeypatch.setattr(
            v.sheet_manager, "update_company",
            lambda c, lst: None,
        )

        await v._run_parallel(
            targets, companies_list=targets, context=ctx, max_concurrent=2,
        )
        assert sorted(applied) == ["OK1", "OK2"]
        assert errored == ["BAD"]


# =============================================================================
# Phase 4: _analyze_chat_reply (純関数化されたチャット返信解析)
# =============================================================================
class TestAnalyzeChatReply:
    """
    AIチャットの返信解析ロジック。3状態を返す:
    - matched           : 企業名 or 企業ID が返信に含まれる → OK
    - no_match_but_reply: 返信は来たが企業名なし (他社混入疑い) → NG
    - no_reply          : 返信なし or タイムアウト → NG
    """

    def test_matched_by_name(self):
        initial = "ようこそ"
        latest = "ようこそ\n株式会社サンプルの事業内容は以下の通りです: ..."
        status, note = _analyze_chat_reply(
            initial, latest, "株式会社サンプル", "sample",
        )
        assert status == "matched"

    def test_matched_by_enterprise_id(self):
        initial = "ようこそ"
        latest = "ようこそ\n貴社 sample の特徴は ... 50文字以上の十分な長さがあります"
        status, note = _analyze_chat_reply(
            initial, latest, "別企業名", "sample",
        )
        assert status == "matched"

    def test_no_match_but_reply(self):
        """50文字以上のテキストが追加されたが、企業名/IDが含まれない"""
        initial = "ようこそ"
        # 「他社」を入れて、サンプル/sample は含まない長文を作る
        latest = "ようこそ\n" + "他社の事業について説明します。" * 5
        status, note = _analyze_chat_reply(
            initial, latest, "株式会社サンプル", "sample",
        )
        assert status == "no_match_but_reply"

    def test_no_reply_when_text_unchanged(self):
        initial = "ようこそ"
        status, note = _analyze_chat_reply(
            initial, initial, "株式会社サンプル", "sample",
        )
        assert status == "no_reply"

    def test_no_reply_when_empty_latest(self):
        status, note = _analyze_chat_reply(
            "ようこそ", "", "株式会社サンプル", "sample",
        )
        assert status == "no_reply"

    def test_diff_under_threshold_treated_as_no_reply(self):
        """50文字未満の差分は no_reply 扱い (typing indicator 等のノイズ除外)"""
        initial = "ようこそ"
        latest = initial + "..."  # 3文字追加のみ
        status, note = _analyze_chat_reply(
            initial, latest, "株式会社サンプル", "sample",
        )
        assert status == "no_reply"

    def test_latest_not_prefixing_initial_uses_full_length(self):
        """latest が initial で始まらないとき (chat clear 等) は latest 全長で diff 判定"""
        initial = "ようこそ"
        latest = "新しいスレッド: " + "x" * 50
        status, note = _analyze_chat_reply(
            initial, latest, "株式会社サンプル", "sample",
        )
        # 企業名なし、長文 → no_match_but_reply
        assert status == "no_match_but_reply"

    def test_empty_company_name_falls_back_to_id(self):
        initial = "ようこそ"
        latest = "ようこそ\n貴社 myid の説明です。十分な長さがあります十分な長さがあります。"
        status, note = _analyze_chat_reply(initial, latest, "", "myid")
        assert status == "matched"
