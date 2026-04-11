"""
DOCdemo 自動化フロー — Webアプリ操作モジュール

Playwrightを使って管理画面（カジュアル面談エージェント）の
各種操作（企業追加・コンテンツ生成・画像アップロード・リンク取得）を自動化する。

対象: Streamlit製Webアプリ
セレクター: 2026-04時点のDOM構造に基づく
"""

import asyncio
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
        """管理画面にログインする。"""
        logger.info("管理画面にログイン中...")

        await self.page.goto(
            WEB_APP_BASE_URL,
            wait_until="domcontentloaded",
            timeout=PAGE_LOAD_TIMEOUT,
        )
        await self._wait_for_streamlit_load()

        # Page not found ポップアップがあれば閉じる
        await self._dismiss_popup()

        # メールアドレス入力 (aria-label="メールアドレス")
        email_input = self.page.locator('input[aria-label="メールアドレス"]')
        await email_input.fill(LOGIN_EMAIL)

        # パスワード入力 (aria-label="パスワード")
        password_input = self.page.locator('input[aria-label="パスワード"]')
        await password_input.fill(LOGIN_PASSWORD)

        # ログインボタンクリック
        login_btn = self.page.locator('button:has-text("ログイン")')
        await login_btn.click()

        # ログイン完了を待機
        await self.page.wait_for_timeout(3000)
        await self._wait_for_streamlit_load()

        self._logged_in = True
        logger.info("ログイン成功")

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

        # 企業ID（半角英数字）
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

        # 連絡先メールアドレス (空白でOK)
        # email_input = self.page.locator('input[aria-label*="連絡先"]')

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
    async def select_company(self, company_id: str):
        """
        「コンテンツ生成」ページの「🏢 企業管理」セクションで企業を選択する。

        引数:
            company_id: 選択対象の企業ID
        """
        logger.info(f"企業選択 (ID): {company_id}")

        # 「🏢 企業管理」セクションの特定を試みる
        # Streamlitではラベルやヘッダーとして表示される可能性がある
        section_label = self.page.locator('text="🏢 企業管理"')
        
        # 企業選択入力フィールドを探す
        # ユーザー提示の「企業を選択:」というラベルをターゲットにする
        combobox = self.page.locator('input[aria-label*="企業を選択"], input[aria-label*="選択"]')
        
        if await combobox.count() > 0:
            await combobox.first.click()
            await self.page.wait_for_timeout(500)

            # 企業IDを直接入力
            await combobox.first.fill(company_id)
            await self.page.wait_for_timeout(1000)

            # ドロップダウンから完全一致するオプションを選択
            # Streamlitの st.selectbox/st.multiselect は li[role="option"] を使用
            option = self.page.locator(
                f'li[role="option"]:has-text("{company_id}")'
            ).first
            
            try:
                # タイムアウトを短めに設定して試行
                await option.click(timeout=3000)
            except Exception:
                # 見つからない場合はEnterキーで確定を試みる
                await combobox.first.press("Enter")
        else:
            logger.warning("「企業を選択」フィールドが見つかりません。")
            # フォールバック: data-baseweb="select" を探す
            selectbox = self.page.locator("div[data-baseweb='select']").first
            if await selectbox.count() > 0:
                await selectbox.click()
                await self.page.fill("input", company_id)
                await self.page.keyboard.press("Enter")

        await self.page.wait_for_timeout(2000)
        await self._wait_for_streamlit_load()

        # ヘッダーの検証
        # 「コンテンツ追加ページのヘッダの名前が企業IDに表示が変更されていることを確認」
        logger.info(f"ヘッダー検証開始: {company_id}")
        header = self.page.locator('h1, h2, h3, [data-testid="stHeader"] p').filter(has_text=company_id).first
        
        try:
            # ヘッダーが表示されるのを待機
            await header.wait_for(state="visible", timeout=5000)
            logger.info(f"ヘッダー検証成功: {company_id} を検出")
        except Exception:
            # 別の要素（例えば st.write 的なもの）に含まれている可能性を考慮
            all_text = await self.page.content()
            if company_id in all_text:
                logger.info(f"ヘッダーとしての特定は不十分ですが、ページ内に {company_id} を確認しました")
            else:
                logger.warning(f"ヘッダーに {company_id} が見つかりませんでした。遷移が不完全な可能性があります")

        logger.info(f"企業選択・検証完了: {company_id}")

    async def input_urls_for_content(self, urls: list):
        """コンテンツ生成ページのURL欄にURLリストを入力する。"""
        logger.info(f"URL入力開始: {len(urls)}件")

        await self.navigate_to_content_generator()
        await self.page.wait_for_timeout(2000)

        # 「コンテンツ入力」タブをクリック（tab-0）
        input_tab = self.page.locator('[id*="tab-0"]:has-text("コンテンツ入力")')
        try:
            if await input_tab.count() > 0:
                await input_tab.first.click()
                await self.page.wait_for_timeout(1000)
        except Exception:
            pass

        # URL入力テキストエリア (aria-label="URLを入力（1行に1つ）：")
        url_textarea = self.page.locator(
            'textarea[aria-label*="URLを入力"]'
        )
        if await url_textarea.count() > 0:
            url_text = "\n".join(urls)
            await url_textarea.first.fill(url_text)
            await self.page.wait_for_timeout(1000)
        else:
            # フォールバック: 最初のtextarea
            textarea = self.page.locator(
                "section.main textarea"
            ).first
            url_text = "\n".join(urls)
            await textarea.fill(url_text)
            await self.page.wait_for_timeout(1000)

        logger.info(f"URL入力完了: {len(urls)}件")

    async def generate_content(self):
        """コンテンツを生成する。"""
        logger.info("コンテンツ生成開始...")

        # 「生成」タブをクリック（tab-1）
        gen_tab = self.page.locator('[id*="tab-1"]:has-text("生成")')
        if await gen_tab.count() > 0:
            await gen_tab.first.click()
        else:
            # フォールバック: テキストで検索
            tabs = self.page.locator("[data-baseweb='tab'], [role='tab']")
            tab_count = await tabs.count()
            for i in range(tab_count):
                text = await tabs.nth(i).text_content()
                if "生成" in (text or "") and "コンテンツ" not in (text or ""):
                    await tabs.nth(i).click()
                    break

        await self.page.wait_for_timeout(2000)

        # 「生成」ボタンをクリック
        gen_button = self.page.locator(
            'button:has-text("生成"), button:has-text("Generate")'
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

    async def save_content(self):
        """生成したコンテンツを保存する。"""
        logger.info("コンテンツ保存開始...")

        # 「プレビュー・保存」タブをクリック（tab-2）
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

        # 「保存」ボタンをクリック
        save_button = self.page.locator(
            'button:has-text("保存"), button:has-text("Save")'
        )
        if await save_button.count() > 0:
            await save_button.first.click()
        else:
            logger.warning("保存ボタンが見つかりません")
            return

        await self.page.wait_for_timeout(3000)
        await self._wait_for_streamlit_load()
        logger.info("コンテンツ保存完了")

    # =========================================================================
    # 画像アップロード
    # =========================================================================
    async def upload_background_image(self, image_path: str):
        """システム設定ページで背景画像をアップロードする。"""
        logger.info(f"背景画像アップロード開始: {image_path}")

        if not Path(image_path).exists():
            raise FileNotFoundError(f"画像ファイルが見つかりません: {image_path}")

        await self.navigate_to_settings()
        await self.page.wait_for_timeout(2000)

        # ページを下にスクロール
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self.page.wait_for_timeout(1000)

        # Streamlitのファイルアップローダー
        # section[aria-label="背景画像をアップロード:"] 内の input[type='file']
        file_input = self.page.locator(
            'section[aria-label*="背景画像"] input[type="file"]'
        )
        if await file_input.count() == 0:
            # フォールバック: 任意のfile input
            file_input = self.page.locator('input[type="file"]')

        if await file_input.count() > 0:
            await file_input.first.set_input_files(image_path)
            await self.page.wait_for_timeout(3000)
            await self._wait_for_streamlit_load()
            logger.info(f"背景画像アップロード完了: {image_path}")
        else:
            logger.warning("ファイルアップロード欄が見つかりません")

    # =========================================================================
    # フロントエンドアプリURL取得
    # =========================================================================
    async def get_frontend_app_url(self) -> str:
        """候補者面談ページから「フロントエンドアプリを開く」のURLを取得する。"""
        logger.info("フロントエンドアプリURL取得開始...")

        await self.navigate_to_candidate_interview()
        await self.page.wait_for_timeout(2000)

        # サイドバー内の「フロントエンドアプリを開く」リンク
        sidebar = self.page.locator("[data-testid='stSidebar']")
        link = sidebar.locator('a:has-text("フロントエンド")')

        if await link.count() > 0:
            url = await link.first.get_attribute("href")
            if url:
                logger.info(f"フロントエンドURL取得成功: {url}")
                return url

        # 代替: ページ全体から検索
        link = self.page.locator('a:has-text("フロントエンド")')
        if await link.count() > 0:
            url = await link.first.get_attribute("href")
            if url:
                logger.info(f"フロントエンドURL取得成功: {url}")
                return url

        # さらに代替: ボタンの場合（新しいタブで開く）
        btn = self.page.locator('button:has-text("フロントエンド")')
        if await btn.count() > 0:
            async with self.page.context.expect_page() as new_page_info:
                await btn.first.click()
            new_page = await new_page_info.value
            url = new_page.url
            await new_page.close()
            logger.info(f"フロントエンドURL取得成功(新タブ): {url}")
            return url

        raise RuntimeError("フロントエンドアプリのURLが取得できませんでした")

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
            # 「Page not found」等のモーダル
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
