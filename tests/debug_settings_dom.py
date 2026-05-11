"""
システム設定ページのDOM構造を調査するデバッグスクリプト。

「背景画像」アップローダーがどこにあるか、設定ページの実構造を
スクリーンショット＋DOM dump で確認する。
"""

import asyncio
import logging
import sys
from pathlib import Path

# parent dir を import path に追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from web_app_operator import WebAppOperator
from config import BROWSER_VIEWPORT

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


TARGET_COMPANY_ID = "seibu-const"  # 既に登録済の企業
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

            await op.navigate_to_settings()
            await page.wait_for_timeout(3000)
            await op._wait_for_streamlit_load()
            logger.info("設定ページ遷移完了")

            await op.select_company(TARGET_COMPANY_ID)
            await page.wait_for_timeout(3000)
            await op._wait_for_streamlit_load()
            logger.info(f"企業選択完了: {TARGET_COMPANY_ID}")

            await page.wait_for_timeout(5000)

            # 1. スクリーンショット
            shot = OUT_DIR / "debug_settings_full.png"
            await page.screenshot(path=str(shot), full_page=True)
            logger.info(f"スクリーンショット保存: {shot}")

            # 2. ページ全体の HTML dump
            html = await page.content()
            html_path = OUT_DIR / "debug_settings_dom.html"
            html_path.write_text(html, encoding="utf-8")
            logger.info(f"HTML dump 保存: {html_path} ({len(html):,} bytes)")

            # 3. 「背景画像」テキストの所在を検索
            print("\n=== 「背景画像」テキスト所在調査 ===")
            for selector in [
                'text=背景画像',
                'h1:has-text("背景画像")',
                'h2:has-text("背景画像")',
                'h3:has-text("背景画像")',
                'h4:has-text("背景画像")',
                'h5:has-text("背景画像")',
                'h6:has-text("背景画像")',
                'p:has-text("背景画像")',
                'label:has-text("背景画像")',
                'span:has-text("背景画像")',
                'div:has-text("背景画像")',
                '[data-testid="stMarkdown"]:has-text("背景画像")',
                '[data-testid="stHeader"]:has-text("背景画像")',
                '[data-testid="stSubheader"]:has-text("背景画像")',
                '[data-testid="stExpander"]:has-text("背景画像")',
            ]:
                try:
                    cnt = await page.locator(selector).count()
                    print(f"  [{cnt:3d}] {selector}")
                except Exception as e:
                    print(f"  [ERR] {selector}: {e}")

            # 4. file uploader の所在
            print("\n=== file uploader 所在調査 ===")
            for selector in [
                'input[type="file"]',
                '[data-testid="stFileUploader"]',
                '[data-testid="stFileUploaderDropzone"]',
                'button:has-text("Browse")',
                'button:has-text("Upload")',
            ]:
                try:
                    cnt = await page.locator(selector).count()
                    print(f"  [{cnt:3d}] {selector}")
                except Exception as e:
                    print(f"  [ERR] {selector}: {e}")

            # 5. 主要な見出し/ラベルを列挙
            print("\n=== 主要見出し列挙 ===")
            for tag in ["h1", "h2", "h3", "h4", "h5", "h6"]:
                items = await page.locator(tag).all_inner_texts()
                for txt in items:
                    if txt.strip():
                        print(f"  <{tag}> {txt.strip()[:80]}")

            print("\n=== Streamlit Markdown/Subheader 列挙 ===")
            for testid in ["stMarkdown", "stHeader", "stSubheader", "stExpander"]:
                items = await page.locator(f'[data-testid="{testid}"]').all_inner_texts()
                for i, txt in enumerate(items):
                    cleaned = txt.strip().replace("\n", " ⏎ ")[:120]
                    if cleaned:
                        print(f"  [{testid}#{i}] {cleaned}")

        finally:
            await page.wait_for_timeout(1000)
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
