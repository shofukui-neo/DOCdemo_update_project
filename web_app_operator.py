"""
DOCdemo 自動化フロー — Webアプリ操作モジュール

Playwrightを使って管理画面（カジュアル面談エージェント）の
各種操作（企業追加・コンテンツ生成・画像アップロード・リンク取得）を自動化する。

対象: Streamlit製Webアプリ
セレクター: 2026-04時点のDOM構造に基づく

変更履歴:
- 2026-04-22: 保存フロー修正（2ボタン保存 + コンテンツ管理確認）
- 2026-04-22: 再ログイン＆キャッシュクリア機能追加
- 2026-04-22: 企業選択をサイドバースクロールで行いタイトル確認を追加
- 2026-04-22: 企業IDをURLから自動抽出する対応
"""

import asyncio
import re
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import Page

from config import (
    WEB_APP_BASE_URL,
    LOGIN_EMAIL,
    LOGIN_PASSWORD,
    PAGE_LOAD_TIMEOUT,
    NAVIGATION_TIMEOUT,
    CONTENT_GENERATION_TIMEOUT,
    ELEMENT_WAIT_TIMEOUT,
    RETRY_COUNT,
    RETRY_DELAY,
)
from models import CompanyInfo

logger = logging.getLogger(__name__)


class WebAppOperator:
    """
    Webアプリ管理画面を自動操作するクラス。

    Streamlitアプリは動的DOMを持つため、各操作後に
    適切な待機処理を行う。aria-label属性を主セレクターとして使用。
    """

    def __init__(self, page: Page):
        self.page = page
        self._logged_in = False

    # =========================================================================
    # ログイン
    # =========================================================================
    async def login(self):
        """ログイン画面で認証を実行する。リトライ機能付き。"""
        logger.info("管理画面にログイン中...")
        
        for attempt in range(RETRY_COUNT):
            try:
                await self.page.goto(
                    WEB_APP_BASE_URL,
                    wait_until="networkidle",
                    timeout=PAGE_LOAD_TIMEOUT,
                )
                await self._wait_for_streamlit_load()
                await self._dismiss_popup()

                # ログイン画面の要素を待機
                email_selector = 'input[aria-label="メールアドレス"]'
                await self.page.wait_for_selector(email_selector, timeout=ELEMENT_WAIT_TIMEOUT)
                
                # メールアドレス入力
                await self.page.locator(email_selector).fill(LOGIN_EMAIL)

                # パスワード入力
                password_selector = 'input[aria-label="パスワード"]'
                await self.page.locator(password_selector).fill(LOGIN_PASSWORD)

                # ログインボタンクリック
                login_btn = self.page.locator('button:has-text("ログイン")')
                await login_btn.click()

                # ログイン完了（サイドバーの出現）を待機
                await self.page.wait_for_selector("[data-testid='stSidebar']", timeout=ELEMENT_WAIT_TIMEOUT)
                await self._wait_for_streamlit_load()

                self._logged_in = True
                logger.info("ログイン成功")
                return
                
            except Exception as e:
                logger.warning(f"ログイン試行 {attempt + 1}/{RETRY_COUNT} 失敗: {e}")
                if attempt < RETRY_COUNT - 1:
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    raise e

    async def re_login_with_cache_clear(self):
        """
        再ログイン＆キャッシュクリアを行う。
        1件の企業処理が完了した後に実行する。
        
        手順:
        1. ブラウザのlocalStorage/sessionStorageをクリア
        2. Cookieをクリア
        3. ページをリロード
        4. 再ログイン
        """
        logger.info("再ログイン＆キャッシュクリア開始...")
        
        try:
            # JavaScript経由でlocalStorage/sessionStorageをクリア
            await self.page.evaluate("""
                () => {
                    try { localStorage.clear(); } catch(e) {}
                    try { sessionStorage.clear(); } catch(e) {}
                }
            """)
            logger.info("  LocalStorage/SessionStorageをクリアしました")
        except Exception as e:
            logger.debug(f"  Storage クリアエラー（無視）: {e}")
        
        try:
            # Cookieをクリア
            await self.page.context.clear_cookies()
            logger.info("  Cookieをクリアしました")
        except Exception as e:
            logger.debug(f"  Cookie クリアエラー（無視）: {e}")
        
        # ログイン状態リセット
        self._logged_in = False
        
        # 少し待ってから再ログイン
        await asyncio.sleep(2)
        await self.login()
        logger.info("再ログイン＆キャッシュクリア完了")

    async def ensure_logged_in(self):
        """ログイン済みか確認し、未ログインなら再ログイン"""
        if not self._logged_in:
            await self.login()
            return

        # セッション切れチェック
        try:
            login_btn = self.page.locator('button:has-text("ログイン")')
            if await login_btn.is_visible():
                logger.warning("セッション切れ検出 -> 再ログイン")
                self._logged_in = False
                await self.login()
        except Exception:
            pass

    # =========================================================================
    # ナビゲーション
    # =========================================================================
    async def _navigate_sidebar(self, link_text: str):
        """サイドバーのリンクをクリックしてページ遷移する。"""
        await self.ensure_logged_in()
        logger.debug(f"ナビゲーション: {link_text}")

        sidebar = self.page.locator("[data-testid='stSidebar']")
        link = sidebar.get_by_text(link_text, exact=False).first
        await link.click()
        await self._wait_for_streamlit_load()

    async def navigate_to_company_setup(self):
        """「企業の追加」ページに遷移"""
        await self._navigate_sidebar("企業の追加")

    async def navigate_to_content_generator(self):
        """「コンテンツ生成」ページに遷移"""
        await self._navigate_sidebar("コンテンツ生成")

    async def navigate_to_settings(self):
        """「システム設定」ページに遷移"""
        await self._navigate_sidebar("システム設定")

    async def navigate_to_candidate_interview(self):
        """「候補者面談」ページに遷移"""
        await self._navigate_sidebar("候補者面談")

    async def navigate_to_content_management(self):
        """「コンテンツ管理」ページに遷移"""
        await self._navigate_sidebar("コンテンツ管理")

    # =========================================================================
    # 企業追加
    # =========================================================================
    async def add_company(self, company: CompanyInfo):
        """
        「企業の追加」ページで企業情報を登録する。

        Streamlit UIのaria-label属性を使って各フィールドを特定する。
        ボタン名は「Create Company」（英語）。
        """
        logger.info(f"企業追加開始: {company.name}")

        await self.navigate_to_company_setup()
        await self.page.wait_for_timeout(2000)

        # 「新規企業作成」タブが選択されていることを確認
        new_tab = self.page.locator('[id*="tab-0"]:has-text("新規")')
        try:
            if await new_tab.count() > 0:
                await new_tab.first.click()
                await self.page.wait_for_timeout(1000)
        except Exception:
            pass

        # --- aria-labelで各フィールドに入力 ---

        # 企業ID（URLのドメインから生成済みの enterprise_id を使用）
        enterprise_id_input = self.page.locator(
            'input[aria-label*="企業ID"]'
        )
        if await enterprise_id_input.count() > 0:
            await enterprise_id_input.first.fill(company.enterprise_id)
            await self.page.wait_for_timeout(300)
            logger.debug(f"  企業ID入力: {company.enterprise_id}")
        else:
            logger.warning("  企業IDフィールドが見つかりません")

        # 表示名 / 音声読み上げ名
        display_name_input = self.page.locator(
            'input[aria-label*="表示名"]'
        )
        if await display_name_input.count() > 0:
            await display_name_input.first.fill(company.name)
            await self.page.wait_for_timeout(300)
            logger.debug(f"  表示名入力: {company.name}")
        else:
            logger.warning("  表示名フィールドが見つかりません")

        # WebサイトURL
        website_url_input = self.page.locator(
            'input[aria-label*="WebサイトURL"], input[aria-label*="URL"]'
        )
        if await website_url_input.count() > 0:
            await website_url_input.first.fill(company.homepage_url)
            await self.page.wait_for_timeout(300)
            logger.debug(f"  URL入力: {company.homepage_url}")
        else:
            logger.warning("  URLフィールドが見つかりません")

        # ページ下部までスクロールしてボタンを表示
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self.page.wait_for_timeout(1000)

        # 「Create Company」ボタンをクリック（英語名）
        create_btn = self.page.locator('button:has-text("Create Company")')
        if await create_btn.count() == 0:
            # フォールバック: 日本語ボタン名
            create_btn = self.page.locator(
                'button:has-text("作成"), button:has-text("create")'
            )
        await create_btn.first.click()
        await self.page.wait_for_timeout(3000)
        await self._wait_for_streamlit_load()

        logger.info(f"企業追加完了: {company.name} (ID: {company.enterprise_id})")

    # =========================================================================
    # コンテンツ生成
    # =========================================================================
    async def select_company_from_sidebar(self, company: CompanyInfo):
        """
        コンテンツ生成ページ内の左サイドバー（企業管理セクション）から
        企業をスクロールして選択し、タイトルが変更されたことを確認する。

        手順:
        1. サイドバーの企業選択UI（セレクトボックス or ラジオ）を探す
        2. スクロールしながら対象企業を選択
        3. タイトルが「🤖 {企業名} コンテンツ生成」または対象企業名に変わることを確認
        """
        logger.info(f"サイドバーから企業選択開始: {company.name}")

        sidebar = self.page.locator("[data-testid='stSidebar']")
        
        # 企業選択UIを探す (selectbox / combobox)
        company_selector = sidebar.locator(
            'input[aria-label*="企業を選択"], '
            'input[aria-label*="選択"], '
            '[data-baseweb="select"]'
        )
        
        # セレクトボックスをクリックして開く
        if await company_selector.count() > 0:
            await company_selector.first.click()
            await self.page.wait_for_timeout(800)
            
            # 企業名を入力して絞り込み
            await company_selector.first.fill(company.enterprise_id)
            await self.page.wait_for_timeout(1000)
            
            # ドロップダウンから選択
            option = self.page.locator(
                f'li[role="option"]:has-text("{company.enterprise_id}")'
            ).first
            try:
                await option.click(timeout=4000)
                logger.debug(f"  プルダウンから選択: {company.enterprise_id}")
            except Exception:
                # フォールバック: 企業名で試す
                option_name = self.page.locator(
                    f'li[role="option"]:has-text("{company.name}")'
                ).first
                try:
                    await option_name.click(timeout=3000)
                    logger.debug(f"  企業名でフォールバック選択: {company.name}")
                except Exception:
                    await company_selector.first.press("Enter")
                    logger.debug("  Enterキーで確定")
        else:
            # フォールバック: サイドバー全体から探す
            logger.warning("  企業選択セレクターが見つかりません → フォールバック")
            await self.select_company(company.enterprise_id)

        await self.page.wait_for_timeout(2000)
        await self._wait_for_streamlit_load()

        # === タイトルが対象企業名に変わったことを確認 ===
        await self._verify_content_title(company)

        logger.info(f"サイドバー企業選択完了: {company.name}")

    async def _verify_content_title(self, company: CompanyInfo):
        """
        コンテンツ生成ページのタイトルが対象企業名を含んでいることを検証する。

        タイトルは「🤖 {enterprise_id} コンテンツ生成」 または
        「{company.name} コンテンツ生成」などの形式になることを確認。
        """
        logger.info(f"  タイトル確認中: 企業名={company.name}, ID={company.enterprise_id}")
        
        # h1, h2, h3 またはタイトル的な要素を探す
        title_locators = [
            self.page.locator('h1, h2'),
            self.page.locator('[data-testid="stHeading"] p'),
            self.page.locator('.stMarkdown h1, .stMarkdown h2'),
        ]
        
        title_found = False
        for locator in title_locators:
            try:
                count = await locator.count()
                for i in range(count):
                    text = await locator.nth(i).text_content()
                    text = text or ""
                    # 企業ID または 企業名が含まれているか確認
                    if company.enterprise_id in text or company.name in text:
                        logger.info(f"  タイトル確認成功: 「{text.strip()}」")
                        title_found = True
                        break
                if title_found:
                    break
            except Exception:
                pass
        
        if not title_found:
            # ページ全体テキストで確認
            page_content = await self.page.content()
            if company.enterprise_id in page_content or company.name in page_content:
                logger.info(f"  タイトル: ページ内に企業情報を確認（タイトル要素特定は不完全）")
            else:
                logger.warning(
                    f"  [WARN] タイトルに企業名が見つかりません: "
                    f"name={company.name}, id={company.enterprise_id}"
                )
                # スクリーンショットを撮って記録
                screenshot_path = f"screenshots/title_warn_{company.enterprise_id}.png"
                await self.page.screenshot(path=screenshot_path)
                logger.warning(f"  スクリーンショット保存: {screenshot_path}")

    async def select_company(self, company_id: str):
        """
        「コンテンツ生成」ページの「🏢 企業管理」セクションで企業を選択する（後方互換）。
        """
        logger.info(f"企業選択 (ID): {company_id}")

        combobox = self.page.locator('input[aria-label*="企業を選択"], input[aria-label*="選択"]')
        
        if await combobox.count() > 0:
            await combobox.first.click()
            await self.page.wait_for_timeout(500)
            await combobox.first.fill(company_id)
            await self.page.wait_for_timeout(1000)

            option = self.page.locator(
                f'li[role="option"]:has-text("{company_id}")'
            ).first
            
            try:
                await option.click(timeout=3000)
            except Exception:
                await combobox.first.press("Enter")
        else:
            logger.warning("「企業を選択」フィールドが見つかりません。")
            selectbox = self.page.locator("div[data-baseweb='select']").first
            if await selectbox.count() > 0:
                await selectbox.click()
                await self.page.fill("input", company_id)
                await self.page.keyboard.press("Enter")

        await self.page.wait_for_timeout(2000)
        await self._wait_for_streamlit_load()
        logger.info(f"企業選択完了: {company_id}")

    async def input_urls_for_content(self, urls: list):
        """コンテンツ生成ページのURL欄にURLリストを入力する。"""
        logger.info(f"URL入力開始: {len(urls)}件")

        # 「コンテンツ入力」タブをクリック（tab-0）
        input_tab = self.page.locator('[id*="tab-0"]:has-text("コンテンツ入力")')
        try:
            if await input_tab.count() > 0:
                await input_tab.first.click()
                await self.page.wait_for_timeout(1000)
        except Exception:
            pass

        # URL入力テキストエリア
        url_textarea = self.page.locator(
            'textarea[aria-label*="URLを入力"]'
        )
        if await url_textarea.count() > 0:
            url_text = "\n".join(urls)
            await url_textarea.first.fill(url_text)
            await self.page.wait_for_timeout(1000)
        else:
            textarea = self.page.locator("section.main textarea").first
            url_text = "\n".join(urls)
            await textarea.fill(url_text)
            await self.page.wait_for_timeout(1000)

        # 「全入力を処理」ボタンをクリック
        process_button = self.page.locator('button:has-text("全入力を処理")')
        if await process_button.count() > 0:
            await process_button.first.click()
            logger.info("  「全入力を処理」ボタンをクリックしました")
            await self.page.wait_for_timeout(3000)
            await self._wait_for_streamlit_load()
        else:
            logger.warning("  「全入力を処理」ボタンが見つかりません")

        logger.info(f"URL入力・処理開始完了: {len(urls)}件")

    async def generate_content(self):
        """コンテンツを生成する。"""
        logger.info("コンテンツ生成開始...")

        # 「生成」タブをクリック（tab-1）
        gen_tab = self.page.locator('[id*="tab-1"]:has-text("生成")')
        if await gen_tab.count() > 0:
            await gen_tab.first.click()
        else:
            tabs = self.page.locator("[data-baseweb='tab'], [role='tab']")
            tab_count = await tabs.count()
            for i in range(tab_count):
                text = await tabs.nth(i).text_content()
                if "生成" in (text or "") and "コンテンツ" not in (text or ""):
                    await tabs.nth(i).click()
                    break

        await self.page.wait_for_timeout(2000)

        # 「生成準備完了」のメッセージを待機
        logger.info("  生成準備完了の待機中...")
        ready_msg = self.page.locator('div, p, span').filter(has_text=re.compile(r"✅.*準備完了"))
        try:
            await ready_msg.first.wait_for(state="visible", timeout=20000)
            logger.info("  生成準備完了を確認しました")
        except Exception:
            logger.warning("  生成準備完了メッセージが特定できませんでしたが、続行を試みます")

        # 赤い「コンテンツ生成」ボタンをクリック
        gen_button = self.page.locator(
            'button[data-testid="stBaseButton-primary"]:has-text("コンテンツ生成"), '
            'button[data-testid="stBaseButton-primary"]:has-text("生成"), '
            'button:has-text("コンテンツ生成")'
        )
        if await gen_button.count() > 0:
            await gen_button.first.click()
        else:
            logger.warning("生成ボタンが見つかりません")
            return

        # 生成完了を待機（最大5分）
        logger.info("コンテンツ生成中... (最大5分待機)")
        await self._wait_for_generation_complete()

        logger.info("コンテンツ生成完了")

    async def save_content(self, company: CompanyInfo):
        """
        生成したコンテンツを保存する。

        2段階保存フロー:
        1. 「プレビュー・保存」タブで第1のボタン（FAQ保存）をクリック
        2. ページを進めて 赤い「企業情報を保存」ボタンをクリック
        3. 「コンテンツ管理」タブで保存が正しく行われたか確認
        """
        logger.info("コンテンツ保存開始...")

        # 「プレビュー・保存」タブをクリック
        save_tab = self.page.locator('[id*="tab-2"]:has-text("プレビュー"), [id*="tab-2"]:has-text("保存")')
        if await save_tab.count() > 0:
            await save_tab.first.click()
        else:
            tabs = self.page.locator("[data-baseweb='tab'], [role='tab']")
            tab_count = await tabs.count()
            for i in range(tab_count):
                text = await tabs.nth(i).text_content()
                if "保存" in (text or "") or "プレビュー" in (text or ""):
                    await tabs.nth(i).click()
                    break

        await self.page.wait_for_timeout(2000)
        await self._wait_for_streamlit_load()

        # === FAQプレビューの表示を確認 ===
        logger.info("  FAQプレビューの確認中...")
        faq_preview = self.page.locator('div, h1, h2, h3, p, span').filter(
            has_text=re.compile(r"FAQ", re.IGNORECASE)
        )
        try:
            await faq_preview.first.wait_for(state="visible", timeout=20000)
            logger.info("  FAQプレビューの表示を確認しました")
        except Exception:
            logger.error("  FAQプレビューが確認できません。コンテンツ生成に失敗している可能性があります。")
            screenshot_err = f"screenshots/faq_not_found_{company.enterprise_id}.png"
            await self.page.screenshot(path=screenshot_err)
            raise RuntimeError(f"FAQプレビューが確認できませんでした。詳細は {screenshot_err} を確認してください。")

        # === STEP 1: 第1保存ボタン（FAQ保存・置換）をクリック ===
        logger.info("  STEP1: 第1保存ボタン（FAQ保存）をクリック...")
        first_save_btn = self.page.locator(
            'button:has-text("FAQ保存（置換）"), '
            'button:has-text("FAQ保存"), '
            'button:has-text("保存")'
        ).first

        if await first_save_btn.count() > 0:
            await first_save_btn.scroll_into_view_if_needed()
            await self.page.wait_for_timeout(500)
            await first_save_btn.click()
            logger.info("  第1保存ボタンをクリックしました")
            await self.page.wait_for_timeout(2000)
            await self._wait_for_streamlit_load()
        else:
            logger.warning("  第1保存ボタンが見つかりません（スキップ）")

        # === STEP 2: ページを下にスクロールして「企業情報を保存」赤ボタンを探す ===
        logger.info("  STEP2: 企業情報を保存ボタン（赤）を探してクリック...")
        
        # まずスクロールダウン
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self.page.wait_for_timeout(1500)

        # 赤い「企業情報を保存」ボタンを探す
        # primary ボタン（Streamlitではtype="primary"が赤い）
        red_save_btn = self.page.locator(
            'button[data-testid="stBaseButton-primary"]:has-text("企業情報を保存"), '
            'button[kind="primary"]:has-text("企業情報を保存"), '
            'button:has-text("企業情報を保存")'
        )
        
        # 見つからない場合はページ内の全ボタンを確認
        if await red_save_btn.count() == 0:
            logger.info("  「企業情報を保存」ボタンが直接見つからないため、全ボタンを確認...")
            all_buttons = self.page.locator("button")
            btn_count = await all_buttons.count()
            for i in range(btn_count):
                btn_text = await all_buttons.nth(i).text_content()
                logger.debug(f"    ボタン[{i}]: {btn_text}")
                if btn_text and ("保存" in btn_text or "save" in btn_text.lower()):
                    logger.info(f"    保存関連ボタン発見: [{btn_text}]")
            
            # primaryクラスのボタンを探す
            red_save_btn = self.page.locator(
                'button[data-testid="stBaseButton-primary"]'
            )

        if await red_save_btn.count() > 0:
            # 最後のprimaryボタン（企業情報を保存は通常一番下）
            btn_count = await red_save_btn.count()
            target_btn = red_save_btn.nth(btn_count - 1)
            
            btn_text = await target_btn.text_content()
            logger.info(f"  赤いボタンをクリック: [{btn_text}]")
            
            await target_btn.scroll_into_view_if_needed()
            await self.page.wait_for_timeout(500)
            await target_btn.click()
            
            await self.page.wait_for_timeout(3000)
            await self._wait_for_streamlit_load()
            logger.info("  企業情報を保存ボタンのクリック完了")
        else:
            logger.error("  赤い保存ボタン（企業情報を保存）が見つかりません")
            screenshot_path = f"screenshots/save_btn_not_found_{company.enterprise_id}.png"
            await self.page.screenshot(path=screenshot_path)
            raise RuntimeError(f"企業情報を保存ボタンが見つかりませんでした。スクリーンショット: {screenshot_path}")

        # 保存完了メッセージの確認
        success_msg = self.page.locator('[data-testid="stNotification"], .stAlert, [data-testid="stToast"]').filter(
            has_text=re.compile(r"(保存|成功|完了|Success|saved)", re.IGNORECASE)
        )
        try:
            await success_msg.first.wait_for(state="visible", timeout=10000)
            logger.info("  保存完了メッセージを確認しました")
        except Exception:
            logger.warning("  明確な保存完了メッセージは確認できませんでしたが、処理を続行します")

        # === STEP 3: コンテンツ管理タブで保存確認 ===
        await self._verify_content_saved(company)

        await self.page.wait_for_timeout(2000)
        logger.info("コンテンツ保存処理完了")

    async def _verify_content_saved(self, company: CompanyInfo):
        """
        コンテンツ管理タブに移動して、保存が正しく行われたか確認する。
        """
        logger.info(f"  コンテンツ管理タブで保存確認中: {company.name}")
        
        try:
            # コンテンツ管理タブへ移動
            await self.navigate_to_content_management()
            await self.page.wait_for_timeout(2000)
            await self._wait_for_streamlit_load()
            
            # 企業を選択（コンテンツ管理ページにも企業選択がある場合）
            await self._try_select_company_in_page(company.enterprise_id)
            await self.page.wait_for_timeout(1500)
            
            # 企業名やコンテンツが表示されているか確認
            page_content = await self.page.content()
            
            if company.enterprise_id in page_content or company.name in page_content:
                logger.info(f"  [OK] コンテンツ管理タブ: {company.enterprise_id} のコンテンツを確認")
                
                # FAQコンテンツの存在確認
                faq_check = self.page.locator('div, p, span, td').filter(
                    has_text=re.compile(r"(Q\.|FAQ|よくある質問)", re.IGNORECASE)
                )
                if await faq_check.count() > 0:
                    logger.info("  [OK] FAQコンテンツの存在を確認しました")
                else:
                    logger.warning("  [WARN] FAQコンテンツが確認できませんでした")
            else:
                logger.warning(f"  [WARN] コンテンツ管理タブに {company.enterprise_id} の情報が見つかりません")
            
            # スクリーンショットを保存
            screenshot_path = f"screenshots/content_mgmt_{company.enterprise_id}.png"
            await self.page.screenshot(path=screenshot_path)
            logger.info(f"  コンテンツ管理スクリーンショット: {screenshot_path}")
            
        except Exception as e:
            logger.warning(f"  コンテンツ管理確認中にエラー（無視して続行）: {e}")

    async def _try_select_company_in_page(self, company_id: str):
        """ページ内に企業選択UIがあれば選択を試みる"""
        try:
            combobox = self.page.locator(
                'input[aria-label*="企業"], input[aria-label*="選択"]'
            )
            if await combobox.count() > 0:
                await combobox.first.click()
                await self.page.wait_for_timeout(500)
                await combobox.first.fill(company_id)
                await self.page.wait_for_timeout(1000)
                
                option = self.page.locator(
                    f'li[role="option"]:has-text("{company_id}")'
                ).first
                try:
                    await option.click(timeout=3000)
                except Exception:
                    await combobox.first.press("Enter")
                await self.page.wait_for_timeout(1500)
        except Exception:
            pass

    # =========================================================================
    # 画像アップロード
    # =========================================================================
    async def upload_background_image(self, company_id: str, image_path: str):
        """
        システム設定ページで背景画像をアップロードする。
        1. 設定ページでまず「対象企業」を選択する。
        2. 「企業アセット管理」セクション内の「背景画像」セクションを特定して画像を登録する。
        """
        logger.info(f"背景画像アップロード開始 (企業ID: {company_id}): {image_path}")

        if not Path(image_path).exists():
            raise FileNotFoundError(f"画像ファイルが見つかりません: {image_path}")

        await self.navigate_to_settings()
        await self.page.wait_for_timeout(2000)
        await self._wait_for_streamlit_load()

        # 1. 企業選択
        logger.info(f"  設定ページで企業を選択中: {company_id}")
        await self.select_company(company_id)

        # 2. 「背景画像」アップロード欄の特定
        section_heading = self.page.locator('h1, h2, h3, h4, [data-testid="stHeader"] p').filter(has_text="背景画像").first
        
        if await section_heading.count() == 0:
             section_heading = self.page.get_by_text("背景画像", exact=False).first

        target_container = self.page.locator('div, section, [data-testid="stVerticalBlock"]').filter(
            has=section_heading
        ).last

        upload_btn = target_container.locator('button:has-text("Upload"), button:has-text("アップロード"), button:has-text("Browse")')
        file_input = target_container.locator('input[type="file"]')

        if await upload_btn.count() > 0:
            logger.info("  「Upload」関連ボタンを検出。クリックしてファイル選択を開始します。")
            try:
                async with self.page.expect_file_chooser(timeout=5000) as fc_info:
                    await upload_btn.first.click()
                file_chooser = await fc_info.value
                await file_chooser.set_files(image_path)
            except Exception as e:
                logger.debug(f"    ボタンクリック失敗、直接セットを試みます: {e}")
                if await file_input.count() > 0:
                    await file_input.first.set_input_files(image_path)
        elif await file_input.count() > 0:
            logger.info("  ファイル入力欄を直接操作します。")
            await file_input.first.set_input_files(image_path)
        else:
            logger.warning("  指定位置にアップロード欄が見つからないため、ページ全体から最適な場所を探します。")
            fallback_input = self.page.locator('input[type="file"]')
            if await fallback_input.count() > 0:
                await fallback_input.first.set_input_files(image_path)
            else:
                raise RuntimeError("背景画像のアップロード欄が見つかりませんでした。")

        await self.page.wait_for_timeout(3000)
        await self._wait_for_streamlit_load()
        logger.info(f"背景画像アップロード完了: {image_path}")

    # =========================================================================
    # フロントエンドアプリURL取得
    # =========================================================================
    async def get_frontend_app_url(self) -> str:
        """候補者面談ページから「フロントエンドアプリを開く」ボタンをクリックしてURLを取得する。"""
        logger.info("フロントエンドアプリURL取得開始...")

        await self.navigate_to_candidate_interview()
        await self.page.wait_for_timeout(2000)
        await self._wait_for_streamlit_load()

        # メインコンテンツ内のボタンを探す
        main_content = self.page.locator("section.main")
        btn = main_content.locator('button:has-text("フロントエンド"), button:has-text("アプリを開く")')
        
        if await btn.count() == 0:
            btn = self.page.locator('button:has-text("フロントエンド"), button:has-text("アプリを開く")')

        if await btn.count() > 0:
            logger.info("  「フロントエンドアプリを開く」ボタンを検出しました。クリックしてページを開きます。")
            try:
                async with self.page.context.expect_page(timeout=10000) as new_page_info:
                    await btn.first.click()
                new_page = await new_page_info.value
                
                try:
                    await new_page.wait_for_load_state("domcontentloaded")
                    await new_page.wait_for_timeout(3000)
                    url = new_page.url
                    logger.info(f"  新規タブからURLを取得成功: {url}")
                    return url
                finally:
                    await new_page.close()
            except Exception as e:
                logger.warning(f"  新規タブの捕捉またはURL取得に失敗しました: {e}")

        # フォールバック: aタグのhref属性を探す
        logger.debug("  フォールバック: リンク(aタグ)の属性確認を試みます")
        link = self.page.locator('a:has-text("フロントエンド"), a:has-text("アプリを開く")')
        if await link.count() > 0:
            url = await link.first.get_attribute("href")
            if url:
                logger.info(f"  aタグのhref属性からURLを取得成功: {url}")
                return url

        raise RuntimeError("フロントエンドアプリのURLが取得できませんでした。ボタンまたはリンクが見つかりません。")

    # =========================================================================
    # ヘルパーメソッド
    # =========================================================================
    async def _wait_for_streamlit_load(self):
        """Streamlitのページ読み込み完了を待機する"""
        try:
            spinner = self.page.locator("[data-testid='stSpinner']")
            await spinner.wait_for(state="hidden", timeout=NAVIGATION_TIMEOUT)
        except Exception:
            pass
        await self.page.wait_for_timeout(1500)

    async def _dismiss_popup(self):
        """ポップアップダイアログが表示されていたら閉じる"""
        try:
            close_btns = self.page.locator(
                "button[aria-label='Close'], "
                "button:has-text('Close'), "
                "[data-dismiss='modal']"
            )
            if await close_btns.count() > 0:
                await close_btns.first.click()
                await self.page.wait_for_timeout(500)
        except Exception:
            pass

    async def _wait_for_generation_complete(self):
        """コンテンツ生成の完了を待機する（ポーリング方式）"""
        poll_interval = 5000  # 5秒ごとにチェック
        elapsed = 0

        while elapsed < CONTENT_GENERATION_TIMEOUT:
            await self.page.wait_for_timeout(poll_interval)
            elapsed += poll_interval

            # 完了判定: スピナーが消える
            spinner_visible = False
            try:
                spinner = self.page.locator(
                    "[data-testid='stSpinner'], .stSpinner"
                )
                spinner_visible = await spinner.is_visible()
            except Exception:
                pass

            if not spinner_visible:
                # エラーチェック
                try:
                    error = self.page.locator("[data-testid='stAlert']")
                    if await error.count() > 0:
                        error_text = await error.first.text_content()
                        if "エラー" in (error_text or "") or "Error" in (error_text or ""):
                            raise RuntimeError(
                                f"コンテンツ生成エラー: {error_text}"
                            )
                except RuntimeError:
                    raise
                except Exception:
                    pass

                logger.info(f"生成完了検出 (経過: {elapsed / 1000}秒)")
                return

            logger.debug(f"生成中... (経過: {elapsed / 1000}秒)")

        raise TimeoutError(
            f"コンテンツ生成がタイムアウト ({CONTENT_GENERATION_TIMEOUT / 1000}秒)"
        )
