"""
FAQ生成ロジックの頑強性テスト

2026-05-19 の修正点を回帰防止のために検証する:
    - 「FAQを生成中…」「企業情報を生成中…」表示中は完了と判定しない
    - スピナーが消えていても「生成中」テキストが残っていれば待ち続ける
    - 「生成中」消失後にだけ FAQ_VERIFY_TIMEOUT_SECONDS のタイマーを開始する
"""

import asyncio
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_app_operator as wop
from web_app_operator import (  # noqa: E402
    WebAppOperator,
    ContentSaveVerificationError,
)


# -----------------------------------------------------------------------------
# テストヘルパー: 状態遷移する page locator のモック
# -----------------------------------------------------------------------------


class FakeLocator:
    """page.locator(...) の代わりに使うシンプルなモック。

    `text` を順次更新でき、 inner_text() / is_visible() / count() を返す。
    """

    def __init__(self, text_provider, count_provider=None, visible_provider=None):
        self._text_provider = text_provider
        self._count_provider = count_provider or (lambda: 1)
        self._visible_provider = visible_provider or (lambda: True)
        self.first = self

    async def inner_text(self, timeout=None):
        return self._text_provider()

    async def is_visible(self):
        return self._visible_provider()

    async def count(self):
        return self._count_provider()

    async def fill(self, text):
        self._filled_text = text
        if hasattr(self, "_page"):
            self._page.last_filled_text = text

    async def screenshot(self, **kwargs):
        pass

    async def wait_for(self, **kwargs):
        return None

    def locator(self, *_args, **_kwargs):
        return self


class FakePage:
    """page の最低限のスタブ。

    `texts` リストを順次返し、 wait_for_timeout はカウンタを進めるだけ。
    """

    def __init__(self, texts, spinner_visible=False):
        self._texts = list(texts)
        self._idx = 0
        self._spinner_visible = spinner_visible
        self._timeline = 0  # 仮想時刻 ms

    def _current_text(self):
        if self._idx < len(self._texts):
            return self._texts[self._idx]
        return self._texts[-1] if self._texts else ""

    def locator(self, selector):
        if "stSpinner" in selector:
            locator = FakeLocator(
                text_provider=lambda: "",
                visible_provider=lambda: self._spinner_visible,
            )
        elif "stAlert" in selector:
            locator = FakeLocator(
                text_provider=lambda: "",
                count_provider=lambda: 0,
            )
        else:
            locator = FakeLocator(text_provider=self._current_text)
        self.last_locator = locator
        return locator

    async def wait_for_timeout(self, ms):
        self._idx += 1
        self._timeline += ms

    async def screenshot(self, **kwargs):
        pass

    async def content(self):
        return self._current_text()


# -----------------------------------------------------------------------------
# 共通ヘルパー: WebAppOperator を mock page で組み立てる
# -----------------------------------------------------------------------------


def make_operator(page):
    op = WebAppOperator(page=page)
    return op


def make_company(name="株式会社テスト", eid="test-id"):
    c = MagicMock()
    c.name = name
    c.enterprise_id = eid
    return c


# -----------------------------------------------------------------------------
# テスト本体
# -----------------------------------------------------------------------------


def test_verify_faq_succeeds_after_in_progress_clears():
    """生成中表示が消えた後にFAQが現れれば成功する"""
    company = make_company()

    # 仮想ページ: 最初は「生成中」、次に FAQ 実体が出る
    texts = [
        "🤖 株式会社テスト コンテンツ生成\nFAQを生成中...\n企業情報を生成中...",
        "🤖 株式会社テスト コンテンツ生成\nFAQを生成中...\n企業情報を生成中...",
        "🤖 株式会社テスト コンテンツ生成\nFAQを生成中...\n企業情報を生成中...",
        "🤖 株式会社テスト コンテンツ生成\nFAQを生成中...\n企業情報を生成中...",
        # 生成完了 → FAQ実体表示
        "🤖 株式会社テスト コンテンツ生成\nFAQ 1: 御社の事業内容は？\nQ1: 給与は？\nFAQ 2: 残業は？\nよくある質問",
    ]
    page = FakePage(texts)
    op = make_operator(page)

    # 短いタイムアウトでテスト
    asyncio.run(
        op._verify_faq_generation(company=company, max_wait_seconds=10)
    )


def test_verify_faq_fails_if_only_one_pattern_after_in_progress():
    """生成中が消えた後にもFAQが1件しか見つからない場合は失敗"""
    company = make_company()
    texts = [
        "FAQを生成中...",
        "FAQを生成中...",
        # 生成中が消えたが FAQ は 1 パターン (=「よくある質問」のみ) しか出ない
        "🤖 株式会社テスト コンテンツ生成 よくある質問",
    ] + ["🤖 株式会社テスト コンテンツ生成 よくある質問"] * 50
    page = FakePage(texts)
    op = make_operator(page)

    with pytest.raises(ContentSaveVerificationError):
        asyncio.run(
            op._verify_faq_generation(company=company, max_wait_seconds=8)
        )


def test_verify_faq_waits_through_long_in_progress():
    """「生成中」が長く続いても、検証タイマーは消失後に開始される"""
    company = make_company()

    # 30ポーリング(=75秒)生成中が続き、その後FAQ実体が表示される
    texts = ["FAQを生成中...\n企業情報を生成中..."] * 30 + [
        "🤖 株式会社テスト コンテンツ生成\nFAQ 1\nQ1: ?\nFAQ 2\nよくある質問"
    ]
    page = FakePage(texts)
    op = make_operator(page)

    # max_wait_seconds=10 でも、生成中の間はカウントしないので
    # 生成終了後ただちに FAQ が見える限り成功する
    asyncio.run(
        op._verify_faq_generation(company=company, max_wait_seconds=10)
    )


def test_wait_for_generation_complete_extends_on_timeout_message():
    """コンテンツ生成タイムアウト表示が出ても追加で待機する"""
    page = FakePage(
        ["コンテンツ生成がタイムアウト (300.0秒)"] * 4 + [
            "🤖 株式会社テスト コンテンツ生成\nFAQ 1: ?\nQ1: ?\nFAQ 2: ?\nよくある質問"
        ],
        spinner_visible=False,
    )
    op = make_operator(page)

    orig_timeout = wop.CONTENT_GENERATION_TIMEOUT
    wop.CONTENT_GENERATION_TIMEOUT = 9000
    try:
        asyncio.run(op._wait_for_generation_complete())
    finally:
        wop.CONTENT_GENERATION_TIMEOUT = orig_timeout


def test_input_urls_for_content_trims_to_30():
    """URL入力時は30件超えを30件に制限する"""
    page = FakePage([""] * 5)
    op = make_operator(page)
    urls = [f"https://example.com/{i}" for i in range(40)]

    asyncio.run(op.input_urls_for_content(urls))

    assert page.last_filled_text is not None
    assert page.last_filled_text.count("\n") == 29


def test_in_progress_pattern_matches_real_messages():
    """Streamlit info ボックスのテキストパターンが期待通り検出される"""
    in_progress_pattern = re.compile(
        r"(FAQ.{0,3}生成中|企業情報.{0,3}生成中|生成中\.{2,}|生成しています)"
    )

    positives = [
        "FAQを生成中...",
        "FAQを 生成中...",
        "企業情報を生成中...",
        "企業情報 を 生成中...",
        "生成中...",
        "生成中.....",
        "今、生成しています",
    ]
    negatives = [
        "FAQ 1: 御社の事業内容は何ですか？",
        "Q1: 御社の従業員数は？",
        "コンテンツ生成",
        "生成準備完了",
        "✅ 生成完了",
    ]
    for s in positives:
        assert in_progress_pattern.search(s), f"検出されなかった: {s!r}"
    for s in negatives:
        assert not in_progress_pattern.search(s), f"誤検出: {s!r}"


def test_wait_for_in_progress_to_clear_returns_when_text_disappears():
    """新メソッド _wait_for_in_progress_to_clear が消失検出で正しく抜ける"""
    texts = [
        "FAQを生成中...",
        "FAQを生成中...",
        "FAQを生成中...",
        "🤖 株式会社テスト コンテンツ生成 FAQ 1 Q1 FAQ 2",  # 消失
    ]
    page = FakePage(texts)
    op = make_operator(page)

    asyncio.run(
        op._wait_for_in_progress_to_clear(
            max_wait_ms=60_000, source="unit-test"
        )
    )


def test_wait_for_in_progress_to_clear_respects_max_wait():
    """生成中が永遠に消えなくても max_wait_ms で warning ログのみで抜ける"""
    texts = ["FAQを生成中..."] * 1000
    page = FakePage(texts)
    op = make_operator(page)

    # 例外を投げずに正常終了することを確認
    asyncio.run(
        op._wait_for_in_progress_to_clear(
            max_wait_ms=12_500, source="unit-test"
        )
    )
