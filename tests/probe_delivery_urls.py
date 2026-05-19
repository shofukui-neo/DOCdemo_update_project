"""
納品URL現状調査スクリプト (詳細版)

Stage2でエラー判定された企業のURLが実際にどういう状態(FAQ生成済み/未生成)
にあるかを Playwright で開いて DOM 上のFAQ要素や「生成中」テキストの
有無を実機で確認する。

使い方:
    python tests/probe_delivery_urls.py
"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows コンソール文字化け対策
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )

from playwright.async_api import async_playwright

PROBE_TARGETS = [
    ("株式会社竹中製作所", "takenaka-mfg"),
    ("社会福祉法人あしたば", "ashitaba"),
    ("フジプレアム株式会社", "fujipream"),
    ("医療法人松澤呼吸器クリニック", "matsuzawa-sas"),
    ("浜田電気工業株式会社", "hama-grp"),
    ("アウトルックコンサルティング株式会社", "outlook"),
    ("株式会社i-plug", "i-plug"),
    ("etlabo", "etlabo"),
]

FAQ_PATTERNS = [
    re.compile(r"FAQ\s*\d+"),
    re.compile(r"Q\s*\d+\s*[:：.\)]"),
    re.compile(r"質問\s*\d+"),
    re.compile(r"よくある質問"),
]


async def probe(name: str, enterprise_id: str, browser_context):
    url = f"https://casual-interview-dev.brainverse-ai.com/{enterprise_id}"
    page = await browser_context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # SPA描画完全待ち
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        await page.wait_for_timeout(15000)

        try:
            title = await page.title()
        except Exception:
            title = "(取得失敗)"

        try:
            body_text = await page.locator("body").inner_text(timeout=8000)
        except Exception:
            body_text = ""

        # ボディに会社名が出ない場合、もう少し待ってリトライ
        if name not in body_text and enterprise_id not in body_text:
            await page.wait_for_timeout(8000)
            try:
                body_text = await page.locator("body").inner_text(timeout=8000)
            except Exception:
                pass

        faq_hits = sum(1 for p in FAQ_PATTERNS if p.search(body_text))
        has_name = name in body_text or enterprise_id in body_text

        # チャット起動ボタンを探す (テキスト範囲を広げる)
        has_chat_button = False
        try:
            chat_btn = page.locator(
                "button, [role='button'], a"
            ).filter(
                has_text=re.compile(
                    r"(チャット|面談|質問|相談|問い合わせ|始める|スタート)"
                )
            )
            has_chat_button = await chat_btn.count() > 0
        except Exception:
            pass

        # 背景画像
        has_bg = False
        try:
            has_bg = await page.evaluate(
                """() => {
                    const els = document.querySelectorAll('*');
                    for (const el of els) {
                        const s = window.getComputedStyle(el);
                        if (s.backgroundImage && s.backgroundImage !== 'none'
                            && !s.backgroundImage.includes('linear-gradient')) {
                            return true;
                        }
                    }
                    return false;
                }"""
            )
        except Exception:
            pass

        # スクショ保存 (証跡)
        try:
            ss_path = Path(__file__).resolve().parent / "probe_screenshots"
            ss_path.mkdir(parents=True, exist_ok=True)
            await page.screenshot(
                path=str(ss_path / f"{enterprise_id}.png"), full_page=True
            )
        except Exception:
            pass

        # 「FAQ」をテキスト検索 (緩めの判定)
        body_lower = body_text.lower()
        has_faq_word = "faq" in body_lower or "よくある質問" in body_text

        result = {
            "name": name,
            "id": enterprise_id,
            "url": url,
            "title": title,
            "body_chars": len(body_text),
            "body_preview": body_text[:120].replace("\n", " "),
            "faq_pattern_hits": faq_hits,
            "has_faq_word": has_faq_word,
            "has_company_in_body": has_name,
            "has_chat_button": has_chat_button,
            "has_background_image": has_bg,
        }

        if faq_hits >= 1 and has_name:
            result["verdict"] = "OK (FAQ生成済)"
        elif has_name and (has_chat_button or has_faq_word):
            result["verdict"] = "△ 企業名+チャットUIあり (FAQ実体は文中未抽出)"
        elif has_name:
            result["verdict"] = "△ 企業名はあるがチャットUIもFAQも未検出"
        elif result["body_chars"] < 100:
            result["verdict"] = "× SPA未ロード or 空ページ"
        else:
            result["verdict"] = "× 企業名もFAQも見えない"

        return result
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800}
        )

        results = []
        for name, eid in PROBE_TARGETS:
            print(f"\n[Probe] {name} ({eid})")
            r = await probe(name, eid, context)
            results.append(r)
            print(
                f"  title={r['title']!r}, body={r['body_chars']}chars, "
                f"FAQ_pattern={r['faq_pattern_hits']}, FAQ_word={r['has_faq_word']}, "
                f"name_in_body={r['has_company_in_body']}, "
                f"chat_btn={r['has_chat_button']}, bg={r['has_background_image']}"
            )
            print(f"  body[:120]={r['body_preview']!r}")
            print(f"  => {r['verdict']}")

        await browser.close()

        print("\n" + "=" * 60)
        print("Probe summary")
        print("=" * 60)
        for r in results:
            print(f"  {r['name'][:32]:32s}  {r['verdict']}")

        return results


if __name__ == "__main__":
    asyncio.run(main())
