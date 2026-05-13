"""
YKT 修復 Phase 0b: ykt Details セクションを詳細に取得する

前回のスクショは画面上部までしか写っていなかった。
View Details 後にページ全体のHTMLを保存し、ykt Details セクションの
具体的なフィールド構造を解析する。
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
    await page.wait_for_timeout(500)
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
    if target is None:
        raise RuntimeError("メイン combobox 未検出")
    await target.fill("ykt")
    await page.wait_for_timeout(1000)
    option = page.locator('li[role="option"]:has-text("ykt")').first
    try:
        await option.click(timeout=4000)
    except Exception:
        await target.press("Enter")
    await page.wait_for_timeout(3000)


async def main():
    SCREENSHOTS.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # 縦長ビューポートにして1枚にすべて収まるように
        context = await browser.new_context(viewport={"width": 1280, "height": 3000})
        page = await context.new_page()
        op = WebAppOperator(page=page)

        print("[1] ログイン")
        await op.login()

        print("[2] 企業の追加 → 企業管理 タブ")
        await op.navigate_to_company_setup()
        await page.wait_for_timeout(2500)
        await page.locator(
            '[role="tab"]:has-text("企業管理")'
        ).first.click()
        await page.wait_for_timeout(2500)

        print("[3] ykt をメインドロップダウンで選択")
        await select_ykt_in_main(page)

        print("[4] View Details クリック")
        await page.locator('button:has-text("View Details")').first.click()
        await page.wait_for_timeout(4000)

        # ページ全体スクショ
        await page.screenshot(
            path=str(SCREENSHOTS / "ykt_p0b_view_details_fullpage.png"),
            full_page=True,
        )

        # HTML全体保存
        html = await page.content()
        (LOGS / "ykt_p0b_full_page.html").write_text(html, encoding="utf-8")
        print(f"  HTML 保存: ykt_p0b_full_page.html ({len(html)}字)")

        # 「ykt Details」見出しを起点に、その後の Streamlit ブロックの
        # テキスト構造を JSON 化する
        result = await page.evaluate("""
            () => {
                // ykt Details セクションを特定
                const allH2 = Array.from(document.querySelectorAll("h2"));
                const ykt_h2 = allH2.find(h => /ykt/i.test(h.textContent) && /details/i.test(h.textContent));
                if (!ykt_h2) return {error: 'ykt Details 見出し未検出', allH2: allH2.map(h => h.textContent)};

                // 見出しの親 stVerticalBlock を取得
                const section = ykt_h2.closest('[data-testid="stVerticalBlock"]') || ykt_h2.parentElement;

                // セクション内のすべての Markdown ブロック・コードブロック・JSONを抽出
                const items = [];
                section.querySelectorAll('[data-testid="stMarkdown"], pre, code, [data-testid="stJson"], [data-testid="stMetric"]').forEach((el, idx) => {
                    items.push({
                        index: idx,
                        tag: el.tagName,
                        testid: el.getAttribute('data-testid') || '',
                        textLength: el.innerText.length,
                        text: el.innerText.substring(0, 800),
                    });
                });

                // 全体テキスト
                return {
                    section_text: section.innerText.substring(0, 5000),
                    items: items,
                };
            }
        """)
        print(f"\n[5] ykt Details セクション抽出結果:")
        import json
        out_path = LOGS / "ykt_p0b_details.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  保存: {out_path}")

        if "error" in result:
            print(f"  エラー: {result['error']}")
            print(f"  検出したH2: {result.get('allH2', [])[:10]}")
        else:
            print(f"  section_text 全長: {len(result['section_text'])}字")
            print(f"  section_text 先頭2000字:")
            print(result["section_text"][:2000])
            print(f"\n  items ({len(result['items'])} 件):")
            for item in result["items"][:20]:
                print(f"    [{item['index']}] {item['tag']}/{item['testid']} ({item['textLength']}字): {item['text'][:150]}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
