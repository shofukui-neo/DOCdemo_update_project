"""
デバッグ: 候補者面談ページの実DOM構造を調査する。
クレーネル を選択した状態でページに何があるかを全部ダンプする。
"""

import asyncio
import sys
import os
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import async_playwright
from config import (
    WEB_APP_BASE_URL, BROWSER_VIEWPORT, BROWSER_SLOW_MO,
)
from web_app_operator import WebAppOperator


async def debug_candidate_interview_page():
    print("=" * 60)
    print("候補者面談ページ DOM 調査")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=BROWSER_SLOW_MO)
        context = await browser.new_context(viewport=BROWSER_VIEWPORT)
        page = await context.new_page()

        operator = WebAppOperator(page)
        await operator.login()

        # 候補者面談ページへ遷移
        print("\n[1] 候補者面談ページへ遷移...")
        await operator.navigate_to_candidate_interview()
        await page.wait_for_timeout(3000)
        await operator._wait_for_streamlit_load()

        # 企業選択前のサイドバー全リンクを確認
        print("\n[2] 企業選択 *前* のサイドバー全aタグ:")
        sidebar_links_before = await page.eval_on_selector_all(
            "[data-testid='stSidebar'] a",
            """elements => elements.map(el => ({
                text: (el.textContent || '').trim().substring(0, 80),
                href: el.href,
                target: el.target,
            }))"""
        )
        for i, link in enumerate(sidebar_links_before):
            print(f"  [{i}] text={link['text']!r}")
            print(f"      href={link['href']}")
            print(f"      target={link['target']}")

        # 企業選択
        print("\n[3] 企業選択: クレーネル")
        await operator.select_company("クレーネル")
        await page.wait_for_timeout(3000)
        await operator._wait_for_streamlit_load()

        # 企業選択後のサイドバー全リンクを確認
        print("\n[4] 企業選択 *後* のサイドバー全aタグ:")
        sidebar_links_after = await page.eval_on_selector_all(
            "[data-testid='stSidebar'] a",
            """elements => elements.map(el => ({
                text: (el.textContent || '').trim().substring(0, 80),
                href: el.href,
                target: el.target,
            }))"""
        )
        for i, link in enumerate(sidebar_links_after):
            print(f"  [{i}] text={link['text']!r}")
            print(f"      href={link['href']}")
            print(f"      target={link['target']}")

        # 全aタグ
        print("\n[5] ページ全体の全aタグ (主要な10件):")
        all_links = await page.eval_on_selector_all(
            "a[href]",
            """elements => elements.map(el => ({
                text: (el.textContent || '').trim().substring(0, 80),
                href: el.href,
                target: el.target,
            }))"""
        )
        for i, link in enumerate(all_links[:20]):
            print(f"  [{i}] text={link['text']!r}")
            print(f"      href={link['href']}")

        # 全button要素
        print("\n[6] ページ全体の全button要素:")
        all_buttons = await page.eval_on_selector_all(
            "button",
            """elements => elements.map(el => ({
                text: (el.textContent || '').trim().substring(0, 80),
                dataTestId: el.getAttribute('data-testid'),
                type: el.type,
                visible: el.offsetParent !== null,
            }))"""
        )
        for i, btn in enumerate(all_buttons):
            vis = "visible" if btn['visible'] else "hidden"
            print(f"  [{i}] text={btn['text']!r} ({vis})")
            print(f"      data-testid={btn['dataTestId']}")

        # ページのテキスト抜粋
        print("\n[7] ページテキスト抜粋 (先頭1500文字):")
        text = await page.inner_text("body")
        print(f"  {text[:1500]}")

        # 「フロント」の周辺を抽出
        print("\n[8] テキストに「フロント」「アプリ」が出現する箇所:")
        for keyword in ["フロント", "アプリ", "URL", "リンク", "開く"]:
            idx = text.find(keyword)
            while idx != -1:
                start = max(0, idx - 50)
                end = min(len(text), idx + 100)
                snippet = text[start:end].replace("\n", " | ")
                print(f"  「{keyword}」at {idx}: ...{snippet}...")
                idx = text.find(keyword, idx + 1)

        # 一定時間ブラウザを開いたまま
        print("\n[9] 30秒待機 (ブラウザを目視確認可能)...")
        await page.wait_for_timeout(30000)

        await browser.close()

    print("\n" + "=" * 60)
    print("デバッグ完了")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(debug_candidate_interview_page())
