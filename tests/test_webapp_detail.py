"""
Webアプリ接続の詳細診断テスト
Streamlitアプリのロード待ちを十分に行い、DOM要素を詳細確認する。
"""

import asyncio
import sys
import os
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def diagnose_web_app():
    from playwright.async_api import async_playwright
    from config import WEB_APP_BASE_URL

    print("=" * 60)
    print("Webアプリ詳細診断")
    print("=" * 60)
    print(f"対象URL: {WEB_APP_BASE_URL}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        # 1. ページ遷移
        print("\n[1] ページ遷移中...")
        response = await page.goto(
            WEB_APP_BASE_URL,
            wait_until="domcontentloaded",
            timeout=30000,
        )
        print(f"  HTTP Status: {response.status if response else 'N/A'}")

        # 2. Streamlit完全ロード待ち (最大15秒)
        print("[2] Streamlit完全ロード待機中 (最大15秒)...")
        try:
            # Streamlitのスピナーが消えるのを待つ
            spinner = page.locator("[data-testid='stSpinner']")
            await spinner.wait_for(state="hidden", timeout=15000)
        except Exception:
            pass

        # 追加の待機
        await page.wait_for_timeout(5000)
        print("  ロード待機完了")

        # 3. ページ情報
        title = await page.title()
        url = page.url
        print(f"\n[3] ページ情報:")
        print(f"  タイトル: {title}")
        print(f"  URL: {url}")

        # 4. 全input要素の調査
        print(f"\n[4] input要素の調査:")
        inputs = await page.eval_on_selector_all(
            "input",
            """elements => elements.map(el => ({
                type: el.type,
                ariaLabel: el.getAttribute('aria-label'),
                placeholder: el.placeholder,
                id: el.id,
                name: el.name,
                className: el.className.substring(0, 80)
            }))"""
        )
        if inputs:
            for i, inp in enumerate(inputs):
                print(f"  [{i}] type={inp['type']}, aria-label={inp['ariaLabel']}, "
                      f"placeholder={inp['placeholder']}, id={inp['id']}")
        else:
            print("  入力フィールドが見つかりません")

        # 5. 全button要素の調査
        print(f"\n[5] button要素の調査:")
        buttons = await page.eval_on_selector_all(
            "button",
            """elements => elements.map(el => ({
                text: (el.textContent || '').trim().substring(0, 50),
                dataTestId: el.getAttribute('data-testid'),
                ariaLabel: el.getAttribute('aria-label'),
                visible: el.offsetParent !== null
            }))"""
        )
        if buttons:
            for i, btn in enumerate(buttons):
                vis = "visible" if btn['visible'] else "hidden"
                print(f"  [{i}] text='{btn['text']}', data-testid={btn['dataTestId']}, "
                      f"aria-label={btn['ariaLabel']}, {vis}")
        else:
            print("  ボタンが見つかりません")

        # 6. 主要テキストコンテンツ
        print(f"\n[6] ページテキスト抜粋 (先頭500文字):")
        text = await page.inner_text("body")
        print(f"  {text[:500]}")

        # 7. iframe確認
        print(f"\n[7] iframe確認:")
        iframes = page.locator("iframe")
        iframe_count = await iframes.count()
        print(f"  iframe数: {iframe_count}")
        if iframe_count > 0:
            for i in range(iframe_count):
                src = await iframes.nth(i).get_attribute("src")
                print(f"  [{i}] src={src}")

        await browser.close()

    print("\n" + "=" * 60)
    print("診断完了")
    print("=" * 60)


async def diagnose_link_extraction():
    """リンク抽出テストを別URLで試す"""
    from link_extractor import extract_internal_links_and_screenshot

    print("\n" + "=" * 60)
    print("リンク抽出 -- DNS解決テスト")
    print("=" * 60)

    # まずDNS解決のみテスト
    import socket
    test_domains = [
        ("www.neocareer.co.jp", "ネオキャリア"),
        ("www.google.com", "Google"),
        ("example.com", "Example"),
    ]

    for domain, name in test_domains:
        try:
            ip = socket.getaddrinfo(domain, 443)[0][4][0]
            print(f"  OK {name} ({domain}) -> {ip}")
        except Exception as e:
            print(f"  NG {name} ({domain}) -> {e}")

    # DNSが解決できるURLでリンク抽出テスト
    print("\n  別URLでリンク抽出テスト (example.com)...")
    try:
        result = await extract_internal_links_and_screenshot("https://example.com")
        print(f"  結果: {result['total_links']}件のリンク")
        has_screenshot = os.path.exists(result["screenshot_path"])
        print(f"  スクリーンショット: {'OK' if has_screenshot else 'NG'} ({result['screenshot_path']})")
    except Exception as e:
        print(f"  エラー: {e}")


async def main():
    await diagnose_link_extraction()
    await diagnose_web_app()


if __name__ == "__main__":
    asyncio.run(main())
