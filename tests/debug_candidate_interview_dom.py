"""
候補者面談ページのDOM構造を調査するデバッグスクリプト。

Step 6 の get_frontend_app_url が失敗する原因を特定する。
「フロントエンドアプリを開く」相当のUIがどこにあるかを確認する。
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from web_app_operator import WebAppOperator
from config import BROWSER_VIEWPORT

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


TARGET_COMPANY_ID = "nippon-delica"  # Step 6 で失敗した企業
OUT_DIR = Path("screenshots")


async def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=BROWSER_VIEWPORT)
        page = await context.new_page()

        op = WebAppOperator(page)

        try:
            await op.login()
            logger.info("ログイン成功")

            await op.navigate_to_candidate_interview()
            await page.wait_for_timeout(3000)
            await op._wait_for_streamlit_load()
            logger.info("候補者面談ページ遷移完了")

            await op.select_company(TARGET_COMPANY_ID)
            await page.wait_for_timeout(3000)
            await op._wait_for_streamlit_load()
            logger.info(f"企業選択完了: {TARGET_COMPANY_ID}")

            await page.wait_for_timeout(5000)

            shot = OUT_DIR / "debug_candidate_interview_full.png"
            await page.screenshot(path=str(shot), full_page=True)
            logger.info(f"スクリーンショット保存: {shot}")

            html = await page.content()
            html_path = OUT_DIR / "debug_candidate_interview_dom.html"
            html_path.write_text(html, encoding="utf-8")
            logger.info(f"HTML dump 保存: {html_path} ({len(html):,} bytes)")

            print("\n=== 「フロントエンド/アプリを開く」関連要素 ===")
            for selector in [
                'a:has-text("フロントエンド")',
                'a:has-text("アプリを開く")',
                'a:has-text("開く")',
                'a:has-text("プレビュー")',
                'button:has-text("フロントエンド")',
                'button:has-text("アプリを開く")',
                'button:has-text("開く")',
                'button:has-text("プレビュー")',
                'a[href*="casual-interview-dev"]',
                'a[target="_blank"]',
            ]:
                try:
                    cnt = await page.locator(selector).count()
                    print(f"  [{cnt:3d}] {selector}")
                    if cnt > 0 and cnt <= 5:
                        for i in range(cnt):
                            try:
                                elem = page.locator(selector).nth(i)
                                href = await elem.get_attribute("href")
                                text = await elem.text_content()
                                target = await elem.get_attribute("target")
                                snippet = (text or "").strip()[:60]
                                print(f"        [{i}] href={href} target={target} text={snippet!r}")
                            except Exception as e:
                                print(f"        [{i}] 取得エラー: {e}")
                except Exception as e:
                    print(f"  [ERR] {selector}: {e}")

            print("\n=== サイドバー内の <a> 要素 (href, text) 一覧 ===")
            sidebar_links = page.locator("[data-testid='stSidebar'] a")
            n = await sidebar_links.count()
            print(f"  サイドバー内 <a>: {n}件")
            for i in range(min(n, 30)):
                try:
                    href = await sidebar_links.nth(i).get_attribute("href")
                    text = await sidebar_links.nth(i).text_content()
                    snippet = (text or "").strip()[:50]
                    print(f"    [{i}] href={href} text={snippet!r}")
                except Exception:
                    pass

            print("\n=== メインコンテンツ内の <a target=_blank> 一覧 ===")
            main_links = page.locator("section.main a[target='_blank'], [data-testid='stMain'] a[target='_blank']")
            n = await main_links.count()
            print(f"  外部リンク (target=_blank): {n}件")
            for i in range(min(n, 20)):
                try:
                    href = await main_links.nth(i).get_attribute("href")
                    text = await main_links.nth(i).text_content()
                    snippet = (text or "").strip()[:60]
                    print(f"    [{i}] href={href} text={snippet!r}")
                except Exception:
                    pass

            print("\n=== メインコンテンツ内の全 button 一覧 (text) ===")
            buttons = page.locator("section.main button, [data-testid='stMain'] button")
            n = await buttons.count()
            print(f"  button: {n}件")
            for i in range(min(n, 30)):
                try:
                    text = await buttons.nth(i).text_content()
                    snippet = (text or "").strip().replace("\n", " ")[:60]
                    if snippet:
                        print(f"    [{i}] {snippet!r}")
                except Exception:
                    pass

            print("\n=== /<enterprise_id> を含む全ての <a> 一覧 ===")
            all_links = page.locator(f"a[href*='/{TARGET_COMPANY_ID}']")
            n = await all_links.count()
            print(f"  対象企業IDを含むリンク: {n}件")
            for i in range(min(n, 10)):
                try:
                    href = await all_links.nth(i).get_attribute("href")
                    text = await all_links.nth(i).text_content()
                    print(f"    [{i}] href={href} text={(text or '').strip()[:50]!r}")
                except Exception:
                    pass

        finally:
            await page.wait_for_timeout(1000)
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
