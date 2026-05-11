"""
納品物の最終検証スクリプト
フロントエンドURL を実機ブラウザで開き、対象企業の情報が表示されているかを確認する。
"""

import asyncio
import sys
import io
from playwright.async_api import async_playwright

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TARGET_URL = "https://casual-interview-dev.brainverse-ai.com/c-c-akiyama"
EXPECTED_KEYWORDS = ["カークリニック", "c-c-akiyama"]
SCREENSHOT_PATH = "screenshots/delivery_verify_c-c-akiyama.png"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=200)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        print(f"[1] フロントエンドURL アクセス中: {TARGET_URL}")
        response = await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
        print(f"    HTTP ステータス: {response.status if response else 'unknown'}")

        await page.wait_for_timeout(5000)

        print(f"[2] スクリーンショット保存: {SCREENSHOT_PATH}")
        await page.screenshot(path=SCREENSHOT_PATH, full_page=True)

        print("[3] ページ内容に企業情報が含まれているか確認")
        content = await page.content()
        found = []
        not_found = []
        for kw in EXPECTED_KEYWORDS:
            if kw in content:
                found.append(kw)
            else:
                not_found.append(kw)

        print(f"    検出キーワード:  {found}")
        print(f"    未検出キーワード: {not_found}")

        title = await page.title()
        print(f"    ページタイトル: {title}")

        try:
            body_text = await page.locator("body").inner_text()
            print(f"    本文先頭(500文字): {body_text[:500]}")
        except Exception as e:
            print(f"    本文取得失敗: {e}")

        await browser.close()

        if not_found:
            print("\n[NG] 期待キーワードが見つかりません")
            sys.exit(1)
        else:
            print("\n[OK] 納品物検証完了: 期待キーワードすべて検出")


if __name__ == "__main__":
    asyncio.run(main())
