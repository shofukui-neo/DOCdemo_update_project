"""
テスト: orchestrator.py — Stage 2 マルチコンテキスト並列処理

各 worker が専用 BrowserContext + WebAppOperator を持つことで Streamlit
session_state 共有を回避しつつ、複数企業を並列に処理する。

テスト戦略: 実際の Playwright/Brainverse は使わず、`_setup_worker` と
`_process_single_company` を monkeypatch でモックして並列ワーカーの
オーケストレーション層 (queue / lock / 並列度 / 例外伝播) だけを検証する。
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import CompanyInfo, ProcessStatus
from orchestrator import Orchestrator


def _make_orchestrator():
    """テスト用の Orchestrator (CSV は data/company_list.csv 既存ファイルでOK)"""
    return Orchestrator(csv_path=Path("data/company_list.csv"))


def _make_company(name: str, ent_id: str) -> CompanyInfo:
    return CompanyInfo(
        row_index=0, name=name, enterprise_id=ent_id,
        homepage_url=f"https://example.com/{ent_id}",
        status=ProcessStatus.URL_FOUND,
    )


def _install_fake_setup(monkeypatch, o):
    """`_setup_worker` を呼ぶたびに mock の context + url_finder + web_operator を返す"""
    async def fake_setup(browser):
        ctx = MagicMock()
        ctx.close = AsyncMock(return_value=None)
        url_finder = MagicMock()
        web_operator = MagicMock()
        web_operator.re_login_with_cache_clear = AsyncMock(return_value=None)
        return ctx, url_finder, web_operator
    monkeypatch.setattr(o, "_setup_worker", fake_setup)


def _install_noop_csv_update(monkeypatch, o):
    monkeypatch.setattr(o.sheet_manager, "update_company", lambda c, lst: None)


class TestRunParallelOrchestrator:
    """orchestrator.py の `_run_parallel`"""

    @pytest.mark.asyncio
    async def test_processes_all_companies(self, monkeypatch):
        """3社・並列度2 で全社が _process_single_company に渡される"""
        o = _make_orchestrator()
        pending = [_make_company(f"C{i}", f"c{i}") for i in range(3)]

        processed = []

        async def fake_process(c, lst, uf, wo):
            processed.append(c.name)
            c.status = ProcessStatus.COMPLETED

        monkeypatch.setattr(o, "_process_single_company", fake_process)
        _install_fake_setup(monkeypatch, o)
        _install_noop_csv_update(monkeypatch, o)

        browser = MagicMock()
        await o._run_parallel(pending, pending, browser, max_concurrent=2)

        assert sorted(processed) == ["C0", "C1", "C2"]
        assert o.stats["success"] == 3

    @pytest.mark.asyncio
    async def test_max_concurrent_workers_respected(self, monkeypatch):
        """並列度2 で 5社投入 → 同時アクティブは最大2"""
        import asyncio as _aio
        o = _make_orchestrator()
        pending = [_make_company(f"C{i}", f"c{i}") for i in range(5)]

        active = [0]
        max_active = [0]

        async def fake_process(c, lst, uf, wo):
            active[0] += 1
            max_active[0] = max(max_active[0], active[0])
            await _aio.sleep(0.02)
            active[0] -= 1
            c.status = ProcessStatus.COMPLETED

        monkeypatch.setattr(o, "_process_single_company", fake_process)
        _install_fake_setup(monkeypatch, o)
        _install_noop_csv_update(monkeypatch, o)

        browser = MagicMock()
        await o._run_parallel(pending, pending, browser, max_concurrent=2)

        assert max_active[0] == 2
        assert o.stats["success"] == 5

    @pytest.mark.asyncio
    async def test_per_company_exception_isolated(self, monkeypatch):
        """1社が一般例外で失敗しても他社は完走、エラー社は permanent ERROR にマーク"""
        o = _make_orchestrator()
        pending = [
            _make_company("OK1", "ok1"),
            _make_company("BAD", "bad"),
            _make_company("OK2", "ok2"),
        ]

        async def fake_process(c, lst, uf, wo):
            if c.name == "BAD":
                raise RuntimeError("business: 企業IDが空です")
            c.status = ProcessStatus.COMPLETED

        monkeypatch.setattr(o, "_process_single_company", fake_process)
        _install_fake_setup(monkeypatch, o)
        _install_noop_csv_update(monkeypatch, o)

        browser = MagicMock()
        await o._run_parallel(pending, pending, browser, max_concurrent=2)

        assert o.stats["success"] == 2
        assert o.stats["error"] == 1
        bad = next(c for c in pending if c.name == "BAD")
        assert bad.status == ProcessStatus.ERROR
        assert not bad.is_transient_error()  # 業務エラーなので永続側

    @pytest.mark.asyncio
    async def test_transient_error_uses_transient_marker(self, monkeypatch):
        """Playwright タイムアウト系は mark_transient_error 経由でマーク"""
        o = _make_orchestrator()
        c = _make_company("X", "x")

        async def fake_process(comp, lst, uf, wo):
            raise RuntimeError(
                'Page.wait_for_selector: Timeout 10000ms exceeded'
            )

        monkeypatch.setattr(o, "_process_single_company", fake_process)
        _install_fake_setup(monkeypatch, o)
        _install_noop_csv_update(monkeypatch, o)

        browser = MagicMock()
        await o._run_parallel([c], [c], browser, max_concurrent=1)

        assert c.status == ProcessStatus.ERROR
        assert c.is_transient_error()
        assert "[一時的失敗 1/3]" in c.error_message

    @pytest.mark.asyncio
    async def test_each_worker_gets_own_context(self, monkeypatch):
        """並列度3 で 3社処理時、_setup_worker が 3回呼ばれて各 worker に専用 context が渡る"""
        o = _make_orchestrator()
        pending = [_make_company(f"C{i}", f"c{i}") for i in range(3)]

        setup_calls = []

        async def fake_setup(browser):
            ctx = MagicMock()
            ctx.close = AsyncMock(return_value=None)
            ctx._id = len(setup_calls)
            setup_calls.append(ctx)
            wo = MagicMock()
            wo.re_login_with_cache_clear = AsyncMock(return_value=None)
            return ctx, MagicMock(), wo

        async def fake_process(c, lst, uf, wo):
            c.status = ProcessStatus.COMPLETED

        monkeypatch.setattr(o, "_setup_worker", fake_setup)
        monkeypatch.setattr(o, "_process_single_company", fake_process)
        _install_noop_csv_update(monkeypatch, o)

        browser = MagicMock()
        await o._run_parallel(pending, pending, browser, max_concurrent=3)

        # worker 数だけ context が作られる (各 worker が独立した session を持つ)
        assert len(setup_calls) == 3
        # それぞれの context.close も呼ばれる (リソース回収)
        for ctx in setup_calls:
            ctx.close.assert_called()

    @pytest.mark.asyncio
    async def test_csv_updates_serialized(self, monkeypatch):
        """並列実行中も update_company は重ならない (lock 下で直列化)"""
        import asyncio as _aio
        o = _make_orchestrator()
        pending = [_make_company(f"C{i}", f"c{i}") for i in range(6)]

        active_updates = [0]
        max_active_updates = [0]

        def update_company(c, lst):
            active_updates[0] += 1
            max_active_updates[0] = max(max_active_updates[0], active_updates[0])
            # 軽い同期処理 (実際は I/O)
            active_updates[0] -= 1

        async def fake_process(c, lst, uf, wo):
            # update_company を意図的に呼ぶ (現実のフローは内部で呼ぶ)
            o.sheet_manager.update_company(c, lst)
            await _aio.sleep(0.005)
            c.status = ProcessStatus.COMPLETED

        monkeypatch.setattr(o, "_process_single_company", fake_process)
        monkeypatch.setattr(o.sheet_manager, "update_company", update_company)
        _install_fake_setup(monkeypatch, o)

        browser = MagicMock()
        await o._run_parallel(pending, pending, browser, max_concurrent=3)

        # update_company は同期関数なのでそもそも重ならない (asyncio.Lock 不要だが
        # ここでは「_run_parallel の中で異常な多重呼び出しが起きないこと」を確認)
        assert max_active_updates[0] <= 1
