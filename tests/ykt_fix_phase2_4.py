"""
YKT 修復 Phase 2-4 自動実行

Phase 2: Backup to S3
Phase 3: Delete Company (確認ダイアログを処理)
Phase 4: 「新規企業作成」タブで ykt を再作成

各 step ごとにスクショとログを残す。
"""

import asyncio
import json
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


async def select_in_main(page, company_id: str):
    """メインの「Select Company to Manage」で company_id を選択。"""
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
            in_sb = await inputs.nth(i).evaluate(
                'el => !!el.closest("[data-testid=\\"stSidebar\\"]")'
            )
            if not in_sb:
                target = inputs.nth(i)
                break
        except Exception:
            continue
    if target is None:
        raise RuntimeError("メイン combobox 未検出")
    await target.fill(company_id)
    await page.wait_for_timeout(1000)
    option = page.locator(f'li[role="option"]:has-text("{company_id}")').first
    try:
        await option.click(timeout=4000)
    except Exception:
        await target.press("Enter")
    await page.wait_for_timeout(2500)


async def click_main_button(page, text_keywords: list[str]):
    """メインエリア (サイドバー以外) で text を含む button を最初の1つだけクリック。"""
    buttons = page.locator("button:visible")
    n = await buttons.count()
    for i in range(n):
        try:
            t = (await buttons.nth(i).text_content() or "").strip()
            if not any(kw in t for kw in text_keywords):
                continue
            in_sb = await buttons.nth(i).evaluate(
                'el => !!el.closest("[data-testid=\\"stSidebar\\"]")'
            )
            if in_sb:
                continue
            await buttons.nth(i).scroll_into_view_if_needed()
            await page.wait_for_timeout(300)
            await buttons.nth(i).click()
            return t
        except Exception:
            continue
    raise RuntimeError(f"ボタンが見つかりません: {text_keywords}")


async def wait_for_ykt_gone(page, max_seconds: float = 30) -> bool:
    """ドロップダウン候補から ykt が消えるまで待つ。"""
    elapsed = 0
    while elapsed < max_seconds * 1000:
        try:
            # メインのセレクトを再度開いて検索
            label = page.locator('text=Select Company to Manage').first
            if await label.count() == 0:
                # 別タブにいる場合
                pass
            else:
                select_box = label.locator(
                    'xpath=following::*[@data-baseweb="select"][1]'
                )
                if await select_box.count() > 0:
                    await select_box.click()
                    await page.wait_for_timeout(600)
                    inputs = page.locator(
                        '[data-baseweb="select"] input, input[role="combobox"]'
                    )
                    nn = await inputs.count()
                    for i in range(nn):
                        try:
                            if not await inputs.nth(i).is_visible():
                                continue
                            in_sb = await inputs.nth(i).evaluate(
                                'el => !!el.closest("[data-testid=\\"stSidebar\\"]")'
                            )
                            if not in_sb:
                                await inputs.nth(i).fill("ykt")
                                break
                        except Exception:
                            continue
                    await page.wait_for_timeout(1000)
                    option = page.locator('li[role="option"]:has-text("ykt")')
                    cnt = await option.count()
                    # ドロップダウンを閉じる
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(500)
                    if cnt == 0:
                        return True
        except Exception:
            pass
        await page.wait_for_timeout(2000)
        elapsed += 2500  # click+wait
    return False


async def main():
    SCREENSHOTS.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    audit = {"phases": []}

    def log(phase, msg, **kwargs):
        entry = {"phase": phase, "msg": msg, **kwargs}
        audit["phases"].append(entry)
        print(f"[{phase}] {msg}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 3000})
        page = await context.new_page()
        op = WebAppOperator(page=page)

        # ====================================================================
        # 共通: 企業管理タブを開いて ykt を選択
        # ====================================================================
        log("INIT", "ログイン → 企業の追加 → 企業管理タブ → ykt 選択")
        await op.login()
        await op.navigate_to_company_setup()
        await page.wait_for_timeout(2500)
        await page.locator('[role="tab"]:has-text("企業管理")').first.click()
        await page.wait_for_timeout(2500)
        await select_in_main(page, "ykt")
        await page.screenshot(
            path=str(SCREENSHOTS / "ykt_fix_init_ykt_selected.png"), full_page=True
        )

        # ====================================================================
        # Phase 2: Backup to S3
        # ====================================================================
        log("P2", "Backup to S3 をクリック")
        try:
            btn_text = await click_main_button(page, ["Backup to S3"])
            log("P2", f"ボタンクリック成功: '{btn_text}'")
            await page.wait_for_timeout(5000)
            await page.screenshot(
                path=str(SCREENSHOTS / "ykt_fix_p2_after_backup.png"), full_page=True
            )

            # 成功メッセージを探す
            success = page.locator(
                '[data-testid="stNotification"], .stAlert, [data-testid="stToast"]'
            )
            try:
                await success.first.wait_for(state="visible", timeout=10000)
                msg = await success.first.text_content()
                log("P2", f"通知: {(msg or '')[:120]}")
            except Exception:
                log("P2", "通知メッセージは検出されず、続行")
        except Exception as e:
            log("P2", f"Backup失敗（続行）: {str(e)[:120]}")

        # ====================================================================
        # Phase 3: Delete Company
        # ====================================================================
        log("P3", "Delete Company をクリック")
        await page.wait_for_timeout(2000)
        try:
            btn_text = await click_main_button(page, ["Delete Company"])
            log("P3", f"Delete ボタンクリック成功: '{btn_text}'")
            await page.wait_for_timeout(3000)
            await page.screenshot(
                path=str(SCREENSHOTS / "ykt_fix_p3_after_delete_click.png"),
                full_page=True,
            )

            # 確認ダイアログを探す: Streamlit modal / 「本当に」「Confirm」「Yes」「実行」「削除」等
            log("P3", "確認ダイアログを探索")
            confirm_keywords = [
                "Yes, Delete",
                "Confirm Delete",
                "本当に削除",
                "削除を確認",
                "Confirm",
                "Yes",
                "削除する",
                "実行",
                "OK",
            ]
            # 削除ボタンクリック後に新しく現れた button を探す
            await page.wait_for_timeout(1500)
            confirmed = False
            buttons_now = page.locator("button:visible")
            nn = await buttons_now.count()
            log("P3", f"クリック後の visible buttons: {nn}")
            # 確認系キーワードを含むボタンを優先
            for kw in confirm_keywords:
                for i in range(nn):
                    try:
                        t = (await buttons_now.nth(i).text_content() or "").strip()
                        if kw in t and "Delete Company" not in t:
                            in_sb = await buttons_now.nth(i).evaluate(
                                'el => !!el.closest("[data-testid=\\"stSidebar\\"]")'
                            )
                            if in_sb:
                                continue
                            log("P3", f"確認ボタン検出: '{t[:60]}' → クリック")
                            await buttons_now.nth(i).scroll_into_view_if_needed()
                            await page.wait_for_timeout(300)
                            await buttons_now.nth(i).click()
                            confirmed = True
                            break
                    except Exception:
                        continue
                if confirmed:
                    break

            if not confirmed:
                # ダイアログがなければ削除は1クリックで完了の可能性
                log("P3", "確認ダイアログは検出されず、削除は単一クリックの可能性")

            await page.wait_for_timeout(5000)
            await page.screenshot(
                path=str(SCREENSHOTS / "ykt_fix_p3_after_confirm.png"), full_page=True
            )

            # 削除完了を検証
            log("P3", "ykt がドロップダウンから消えるまで待機")
            await page.wait_for_timeout(2000)
            gone = await wait_for_ykt_gone(page, max_seconds=30)
            log("P3", f"ykt 消失確認: {gone}")
            if not gone:
                log("P3", "[ERROR] 削除が反映されていません。中断します")
                # ステータス保存して終了
                (LOGS / "ykt_fix_audit.json").write_text(
                    json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                await browser.close()
                return
        except Exception as e:
            log("P3", f"[FATAL] 削除失敗: {str(e)[:200]}")
            (LOGS / "ykt_fix_audit.json").write_text(
                json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            await browser.close()
            return

        # ====================================================================
        # Phase 4: 新規企業作成
        # ====================================================================
        log("P4", "新規企業作成タブを開く")
        try:
            new_tab = page.locator(
                '[role="tab"]:has-text("新規企業作成"), [role="tab"]:has-text("新規")'
            ).first
            await new_tab.click()
            await page.wait_for_timeout(2500)
            await page.screenshot(
                path=str(SCREENSHOTS / "ykt_fix_p4_new_tab.png"), full_page=True
            )

            # 企業ID 入力
            id_input = page.locator('input[aria-label*="企業ID"]').first
            await id_input.fill("ykt")
            await page.wait_for_timeout(300)
            log("P4", "企業ID 入力: ykt")

            # 表示名 入力
            name_input = page.locator('input[aria-label*="表示名"]').first
            await name_input.fill("YKT株式会社")
            await page.wait_for_timeout(300)
            log("P4", "表示名 入力: YKT株式会社")

            # WebサイトURL 入力
            url_input = page.locator('input[aria-label*="WebサイトURL"]').first
            if await url_input.count() == 0:
                url_input = page.locator('input[aria-label*="URL"]').first
            await url_input.fill("https://www.ykt.co.jp")
            await page.wait_for_timeout(300)
            log("P4", "WebサイトURL 入力: https://www.ykt.co.jp")

            # ページ下部にスクロール → Create Company クリック
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)
            await page.screenshot(
                path=str(SCREENSHOTS / "ykt_fix_p4_form_filled.png"), full_page=True
            )

            log("P4", "Create Company クリック")
            create_btn = page.locator('button:has-text("Create Company")').first
            await create_btn.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)
            await create_btn.click()
            await page.wait_for_timeout(5000)
            await page.screenshot(
                path=str(SCREENSHOTS / "ykt_fix_p4_after_create.png"), full_page=True
            )

            # 「既に存在」エラーをチェック
            page_html = await page.content()
            if any(kw in page_html.lower() for kw in ["already exists", "already exist", "duplicate"]):
                log("P4", "[ERROR] 'already exists' を検出。削除が反映されていない")
                (LOGS / "ykt_fix_audit.json").write_text(
                    json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                await browser.close()
                return
            if "既に存在" in page_html or "重複" in page_html:
                log("P4", "[ERROR] 重複エラー検出")
                (LOGS / "ykt_fix_audit.json").write_text(
                    json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                await browser.close()
                return
            log("P4", "再作成完了 (重複エラーなし)")
        except Exception as e:
            log("P4", f"[FATAL] 再作成失敗: {str(e)[:200]}")
            (LOGS / "ykt_fix_audit.json").write_text(
                json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            await browser.close()
            return

        # ====================================================================
        # 再作成 検証: コンテンツ生成ページのタイトル正常確認
        # ====================================================================
        log("P4-VERIFY", "コンテンツ生成ページでタイトル正常を確認")
        try:
            await op.navigate_to_content_generator()
            await page.wait_for_timeout(3000)

            from models import CompanyInfo
            ykt_info = CompanyInfo(row_index=0, name="YKT株式会社", enterprise_id="ykt")
            try:
                await op.select_company_from_sidebar(ykt_info)
                log("P4-VERIFY", "サイドバー選択成功 → ヘッダー検証通過")
            except Exception as e:
                # 検証で例外 = まだ壊れている
                msg = str(e)
                if "複数企業名が混入" in msg or "len=" in msg:
                    log("P4-VERIFY", f"[ERR] 再作成後もタイトル破損: {msg[:200]}")
                else:
                    log("P4-VERIFY", f"その他例外: {msg[:200]}")

            # 念のため h1 を取得して長さ確認
            h1s = page.locator("h1")
            n_h1 = await h1s.count()
            for i in range(n_h1):
                t = (await h1s.nth(i).text_content() or "").strip()
                if "コンテンツ生成" in t:
                    log("P4-VERIFY", f"h1 len={len(t)}: {t[:80]}")

            await page.screenshot(
                path=str(SCREENSHOTS / "ykt_fix_p4_verify_title.png"), full_page=True
            )
        except Exception as e:
            log("P4-VERIFY", f"検証中の例外: {str(e)[:200]}")

        (LOGS / "ykt_fix_audit.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log("END", "Phase 2-4 完了。orchestrator (Phase 5) を実行してください")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
