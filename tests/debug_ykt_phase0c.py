"""
YKT 修復 Phase 0c: company_config.json への到達ルートを探す

- View Details 画面で company_config.json をクリックできるか
- S3同期タブ経由でファイル個別操作できるか
- いずれも不可なら、削除→再作成しか手段がないことを確定する
"""

import asyncio
import sys
import io
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright
from config import BROWSER_VIEWPORT
from web_app_operator import WebAppOperator

SCREENSHOTS = Path(__file__).parent.parent / "screenshots"
LOGS = Path(__file__).parent.parent / "logs"


async def select_ykt_in_main(page):
    label = page.locator('text=Select Company to Manage').first
    select_box = label.locator('xpath=following::*[@data-baseweb="select"][1]')
    await select_box.scroll_into_view_if_needed()
    await select_box.click()
    await page.wait_for_timeout(800)
    inputs = page.locator('[data-baseweb="select"] input, input[role="combobox"]')
    n = await inputs.count()
    target = None
    for i in range(n):
        try:
            if not await inputs.nth(i).is_visible():
                continue
            in_sidebar = await inputs.nth(i).evaluate(
                'el => !!el.closest("[data-testid=\\"stSidebar\\"]")'
            )
            if not in_sidebar:
                target = inputs.nth(i)
                break
        except Exception:
            continue
    await target.fill("ykt")
    await page.wait_for_timeout(1000)
    option = page.locator('li[role="option"]:has-text("ykt")').first
    try:
        await option.click(timeout=4000)
    except Exception:
        await target.press("Enter")
    await page.wait_for_timeout(3000)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 3000})
        page = await context.new_page()
        op = WebAppOperator(page=page)
        await op.login()

        # --- 1) View Details 内で company_config.json がクリック可能か ---
        print("[1] 企業管理タブ → ykt 選択 → View Details")
        await op.navigate_to_company_setup()
        await page.wait_for_timeout(2500)
        await page.locator('[role="tab"]:has-text("企業管理")').first.click()
        await page.wait_for_timeout(2500)
        await select_ykt_in_main(page)
        await page.locator('button:has-text("View Details")').first.click()
        await page.wait_for_timeout(4000)

        # company_config.json テキストを探してインタラクションを試す
        cfg_locator = page.locator('text=company_config.json').first
        if await cfg_locator.count() > 0:
            tag = await cfg_locator.evaluate('el => el.tagName')
            clickable = await cfg_locator.evaluate(
                'el => { let cur = el; while (cur && cur !== document.body) { '
                'if (cur.tagName === "A" || cur.tagName === "BUTTON" || '
                'cur.getAttribute("role") === "button" || cur.onclick) return cur.tagName + ":" + (cur.tagName === "A" ? cur.href : "click"); '
                'cur = cur.parentElement; } return null; }'
            )
            print(f"  company_config.json found: tag={tag}, clickable_parent={clickable}")
        else:
            print("  company_config.json テキストが見つかりません")

        # 試しにクリック
        try:
            await cfg_locator.click(timeout=3000)
            await page.wait_for_timeout(2000)
            await page.screenshot(
                path=str(SCREENSHOTS / "ykt_p0c_after_cfg_click.png"), full_page=True
            )
            print("  クリック後のスクショ保存")
        except Exception as e:
            print(f"  クリック失敗: {str(e)[:120]}")

        # --- 2) S3同期タブで個別ファイル操作機能を探す ---
        print("\n[2] S3同期タブを開いて機能を確認")
        s3_tab = page.locator('[role="tab"]:has-text("S3同期")').first
        if await s3_tab.count() > 0:
            await s3_tab.click()
            await page.wait_for_timeout(3500)
            await page.screenshot(
                path=str(SCREENSHOTS / "ykt_p0c_s3sync_tab.png"), full_page=True
            )
            # S3同期タブの主要ボタン・選択肢を列挙
            buttons = page.locator('button:visible')
            n = await buttons.count()
            print(f"  S3同期タブ内 visible buttons: {n}")
            for i in range(n):
                try:
                    t = (await buttons.nth(i).text_content() or "").strip()
                    if t:
                        in_sb = await buttons.nth(i).evaluate(
                            'el => !!el.closest("[data-testid=\\"stSidebar\\"]")'
                        )
                        if not in_sb:
                            print(f"    btn[{i}]: {t[:80]}")
                except Exception:
                    continue

        # --- 3) 新規企業作成タブで「Copy structure from existing company」オプションがあれば ---
        # それを使って yktの構造をコピーした別企業 (ykt-fix 等) を作って差し替えできるか確認
        print("\n[3] 新規企業作成タブで Copy structure オプション確認")
        new_tab = page.locator('[role="tab"]:has-text("新規")').first
        if await new_tab.count() > 0:
            await new_tab.click()
            await page.wait_for_timeout(2500)

            # Advanced Options を展開
            advanced = page.locator('text=Advanced Options').first
            if await advanced.count() > 0:
                try:
                    await advanced.click()
                    await page.wait_for_timeout(1500)
                except Exception:
                    pass

            # Copy structure 系セレクトを探す
            copy_label = page.locator('text=Copy structure').first
            print(f"  Copy structure ラベル発見: {await copy_label.count() > 0}")
            await page.screenshot(
                path=str(SCREENSHOTS / "ykt_p0c_new_tab_advanced.png"), full_page=True
            )

        await browser.close()
        print("\n[完了] Phase 0c 結果保存")


if __name__ == "__main__":
    asyncio.run(main())
