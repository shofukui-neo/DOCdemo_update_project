"""
YKT 修復 Phase 0 (簡易版): 非破壊調査

メインの「Select Company to Manage」で ykt を選択し、
View Details / Validate Structure を順に実行し、各段階のスクショと
ページ HTML を保存する。破壊操作は一切行わない。
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


async def select_ykt_in_main_dropdown(page):
    """「Select Company to Manage」で ykt を選択する。サイドバーの選択ボックスは無視。"""
    # ラベルテキスト「Select Company to Manage」直後の最初の baseweb select を探す
    label = page.locator('text=Select Company to Manage').first
    if await label.count() == 0:
        raise RuntimeError("ラベル「Select Company to Manage」が見つかりません")

    # 同一フォームコンテナ内の baseweb select
    select_box = label.locator('xpath=following::*[@data-baseweb="select"][1]')

    await select_box.scroll_into_view_if_needed()
    await page.wait_for_timeout(500)
    await select_box.click()
    await page.wait_for_timeout(800)

    # サイドバー以外の visible な combobox input を選ぶ
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
    if target is None:
        raise RuntimeError("メイン側の combobox 入力欄が見つかりません")

    await target.fill("ykt")
    await page.wait_for_timeout(1000)

    option = page.locator('li[role="option"]:has-text("ykt")').first
    try:
        await option.click(timeout=4000)
    except Exception:
        await target.press("Enter")
    await page.wait_for_timeout(3000)


async def get_main_text_dump(page) -> str:
    """サイドバー以外のメインコンテンツのテキストを抽出して返す。"""
    return await page.evaluate("""
        () => {
            const sidebar = document.querySelector('[data-testid="stSidebar"]');
            const body = document.body.cloneNode(true);
            // クローンからサイドバー要素を除去
            const sidebarClone = body.querySelector('[data-testid="stSidebar"]');
            if (sidebarClone) sidebarClone.remove();
            return body.innerText;
        }
    """)


async def main():
    SCREENSHOTS.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=BROWSER_VIEWPORT)
        page = await context.new_page()
        op = WebAppOperator(page=page)

        print("[P0-1] ログイン")
        await op.login()

        print("[P0-2] 企業の追加ページに遷移 → 企業管理タブ")
        await op.navigate_to_company_setup()
        await page.wait_for_timeout(2500)
        mgmt = page.locator(
            '[role="tab"]:has-text("企業管理"), button:has-text("企業管理")'
        ).first
        await mgmt.click()
        await page.wait_for_timeout(2500)
        await page.screenshot(path=str(SCREENSHOTS / "ykt_p0_01_mgmt_default.png"), full_page=True)

        print("[P0-3] メインで ykt を選択")
        await select_ykt_in_main_dropdown(page)
        await page.screenshot(path=str(SCREENSHOTS / "ykt_p0_02_ykt_selected.png"), full_page=True)
        dump_after_select = await get_main_text_dump(page)
        (LOGS / "ykt_p0_text_after_select.txt").write_text(dump_after_select, encoding="utf-8")
        print(f"  メインテキスト先頭400字:\n{dump_after_select[:400]}")
        print(f"  ...\n  メイン全長: {len(dump_after_select)}字")

        print("[P0-4] View Details クリック")
        vd = page.locator('button:has-text("View Details")').first
        if await vd.count() > 0:
            await vd.click()
            await page.wait_for_timeout(4000)
            await page.screenshot(path=str(SCREENSHOTS / "ykt_p0_03_view_details.png"), full_page=True)
            dump_after_vd = await get_main_text_dump(page)
            (LOGS / "ykt_p0_text_after_view_details.txt").write_text(dump_after_vd, encoding="utf-8")
            print(f"  View Details 後メイン全長: {len(dump_after_vd)}字")
            # ykt 関連の行のみ抽出
            ykt_lines = [ln for ln in dump_after_vd.split("\n") if "ykt" in ln.lower() or "YKT" in ln]
            print(f"  ykt関連行 ({len(ykt_lines)}件):")
            for ln in ykt_lines[:30]:
                print(f"    {ln.strip()[:200]}")
        else:
            print("  View Details ボタンが見つかりません")

        print("[P0-5] 編集可能な input/textarea を列挙")
        all_inputs = page.locator('input:visible')
        n_inputs = await all_inputs.count()
        print(f"  visible inputs: {n_inputs}")
        for i in range(n_inputs):
            try:
                in_sb = await all_inputs.nth(i).evaluate(
                    'el => !!el.closest("[data-testid=\\"stSidebar\\"]")'
                )
                if in_sb:
                    continue
                aria = await all_inputs.nth(i).get_attribute("aria-label") or ""
                ph = await all_inputs.nth(i).get_attribute("placeholder") or ""
                val = await all_inputs.nth(i).input_value() or ""
                print(f"    input[{i}] aria={aria[:50]}, placeholder={ph[:30]}, value={val[:60]}")
            except Exception:
                continue

        all_ta = page.locator('textarea:visible')
        n_ta = await all_ta.count()
        print(f"  visible textareas: {n_ta}")
        for i in range(n_ta):
            try:
                in_sb = await all_ta.nth(i).evaluate(
                    'el => !!el.closest("[data-testid=\\"stSidebar\\"]")'
                )
                if in_sb:
                    continue
                aria = await all_ta.nth(i).get_attribute("aria-label") or ""
                val = await all_ta.nth(i).input_value() or ""
                print(f"    textarea[{i}] aria={aria[:50]}, value={val[:200]}")
            except Exception:
                continue

        print("[P0-6] Validate Structure クリック")
        vs = page.locator('button:has-text("Validate Structure")').first
        if await vs.count() > 0:
            await vs.click()
            await page.wait_for_timeout(4500)
            await page.screenshot(path=str(SCREENSHOTS / "ykt_p0_04_validate.png"), full_page=True)
            dump_after_vs = await get_main_text_dump(page)
            (LOGS / "ykt_p0_text_after_validate.txt").write_text(dump_after_vs, encoding="utf-8")
            print(f"  Validate 後メイン全長: {len(dump_after_vs)}字")
            # ykt 行のみ
            for ln in [l for l in dump_after_vs.split("\n") if "ykt" in l.lower()][:20]:
                print(f"    {ln.strip()[:200]}")

        await browser.close()
        print("\n[P0 完了] スクショとテキストダンプを保存しました")


if __name__ == "__main__":
    asyncio.run(main())
