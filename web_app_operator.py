"""
DOCdemo 自動化フロー — Webアプリ操作モジュール

Playwrightを使って管理画面（カジュアル面談エージェント）の
各種操作（企業追加・コンテンツ生成・画像アップロード・リンク取得）を自動化する。

対象: Streamlit製Webアプリ
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, expect

from config import (
    WEB_APP_BASE_URL,
    LOGIN_EMAIL,
    LOGIN_PASSWORD,
    PAGES,
    PAGE_LOAD_TIMEOUT,
    NAVIGATION_TIMEOUT,
    CONTENT_GENERATION_TIMEOUT,
    ELEMENT_WAIT_TIMEOUT,
    UPLOAD_TIMEOUT,
    BROWSER_SLOW_MO,
    RETRY_COUNT,
    RETRY_DELAY,
)
from models import CompanyInfo

logger = logging.getLogger(__name__)


class WebAppOperator:
    """
    Webアプリ管理画面を自動操作するクラス。

    Streamlitアプリは動的DOMを持つため、各操作後に
    適切な待機処理を行う。
    """

    def __init__(self, page: Page):
        self.page = page
        self._logged_in = False

    # =========================================================================
    # ログイン
    # =========================================================================
    async def login(self):
        """
        管理画面にログインする。

        1. ログインページにアクセス
        2. メールアドレスとパスワードを入力
        3. ログインボタンをクリック
        4. ダッシュボードの表示を確認
        """
        logger.info("管理画面にログイン中...")

        await self.page.goto(
            WEB_APP_BASE_URL + PAGES["login"],
            wait_until="domcontentloaded",
            timeout=PAGE_LOAD_TIMEOUT,
        )
        await self._wait_for_streamlit_load()

        # Page not found ポップアップがあれば閉じる
        await self._dismiss_popup()

        # メールアドレス入力
        email_input = self.page.locator("input[type='text']").first
        await email_input.fill(LOGIN_EMAIL)

        # パスワード入力
        password_input = self.page.locator("input[type='password']").first
        await password_input.fill(LOGIN_PASSWORD)

        # ログインボタンクリック
        login_btn = self.page.get_by_role("button", name="ログイン")
        await login_btn.click()

        # ログイン完了を待機（サイドバーが表示されるまで）
        await self.page.wait_for_timeout(3000)
        await self._wait_for_streamlit_load()

        self._logged_in = True
        logger.info("ログイン成功")

    async def ensure_logged_in(self):
        """ログイン済みか確認し、未ログインなら再ログイン"""
        if not self._logged_in:
            await self.login()
            return

        # セッション切れチェック: ログインフォームが見えたら再ログイン
        try:
            login_btn = self.page.get_by_role("button", name="ログイン")
            if await login_btn.is_visible():
                logger.warning("セッション切れ検出 → 再ログイン")
                self._logged_in = False
                await self.login()
        except Exception:
            pass

    # =========================================================================
    # ナビゲーション
    # =========================================================================
    async def _navigate_sidebar(self, link_text: str):
        """
        サイドバーのリンクをクリックしてページ遷移する。

        Args:
            link_text: サイドバーに表示されるリンクテキスト
        """
        await self.ensure_logged_in()
        logger.debug(f"ナビゲーション: {link_text}")

        # Streamlitサイドバー内のリンクをクリック
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

        Args:
            company: 登録する企業情報

        フロー:
        1. 企業の追加ページに遷移
        2. 企業ID・表示名・URL・メールアドレスを入力
        3. 「作成」ボタンをクリック
        4. 作成完了を確認
        """
        logger.info(f"企業追加開始: {company.name}")

        await self.navigate_to_company_setup()
        await self.page.wait_for_timeout(2000)

        # Streamlitのテキスト入力フィールドを取得
        # 企業の追加ページの入力フィールド: 企業ID, 表示名, WebサイトURL, メールアドレス
        text_inputs = self.page.locator(
            "section.main input[type='text']"
        )

        # 各フィールドに入力
        input_count = await text_inputs.count()
        if input_count >= 4:
            # 企業ID
            await text_inputs.nth(0).fill(company.enterprise_id)
            await self.page.wait_for_timeout(300)

            # 表示名
            await text_inputs.nth(1).fill(company.name)
            await self.page.wait_for_timeout(300)

            # WebサイトURL
            await text_inputs.nth(2).fill(company.homepage_url)
            await self.page.wait_for_timeout(300)

            # 連絡先メールアドレス（空白またはデフォルト）
            await text_inputs.nth(3).fill("")
            await self.page.wait_for_timeout(300)
        else:
            # フィールド数が少ない場合は別のセレクターを試す
            logger.warning(
                f"入力フィールド数不一致: 期待=4, 実際={input_count}. "
                "代替セレクターを試行..."
            )
            all_inputs = self.page.locator("input")
            total = await all_inputs.count()
            logger.debug(f"全input要素数: {total}")

            # ラベル名で特定
            await self._fill_labeled_input("企業ID", company.enterprise_id)
            await self._fill_labeled_input("表示名", company.name)
            await self._fill_labeled_input("WebサイトURL", company.homepage_url)
            # メールアドレスは空でも可

        # 「作成」ボタンをクリック
        # ページ下部までスクロール
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self.page.wait_for_timeout(1000)

        create_btn = self.page.get_by_role("button", name="作成")
        await create_btn.click()
        await self.page.wait_for_timeout(3000)

        logger.info(f"企業追加完了: {company.name} (ID: {company.enterprise_id})")

    # =========================================================================
    # コンテンツ生成
    # =========================================================================
    async def select_company(self, company_name: str):
        """
        サイドバーの企業選択ドロップダウンで企業を選択する。

        Args:
            company_name: 選択する企業名
        """
        logger.info(f"企業選択: {company_name}")

        sidebar = self.page.locator("[data-testid='stSidebar']")

        # Streamlitのselectboxを探す
        # まず「企業を選択」のラベルを持つselectboxを特定
        selectbox = sidebar.locator("div[data-baseweb='select']").first
        await selectbox.click()
        await self.page.wait_for_timeout(500)

        # ドロップダウンメニューから企業名を選択
        option = self.page.locator(
            f"li[role='option']:has-text('{company_name}')"
        ).first
        try:
            await option.click(timeout=5000)
        except Exception:
            # 部分一致で試みる
            logger.debug(f"完全一致で見つからず。部分一致で検索: {company_name}")
            options = self.page.locator("li[role='option']")
            count = await options.count()
            for i in range(count):
                text = await options.nth(i).text_content()
                if company_name in (text or ""):
                    await options.nth(i).click()
                    break
            else:
                raise ValueError(f"企業が見つかりません: {company_name}")

        await self.page.wait_for_timeout(1000)
        logger.info(f"企業選択完了: {company_name}")

    async def input_urls_for_content(self, urls: list):
        """
        コンテンツ生成ページのURL欄にURLリストを入力する。

        Args:
            urls: 入力するURLのリスト

        フロー:
        1. コンテンツ生成ページに遷移
        2. 「📥 コンテンツ入力」タブを選択
        3. URL入力欄にURLを改行区切りで入力
        """
        logger.info(f"URL入力開始: {len(urls)}件")

        await self.navigate_to_content_generator()
        await self.page.wait_for_timeout(2000)

        # 「📥 コンテンツ入力」タブが選択されていることを確認
        content_input_tab = self.page.get_by_text("コンテンツ入力", exact=False).first
        try:
            await content_input_tab.click()
            await self.page.wait_for_timeout(1000)
        except Exception:
            logger.debug("コンテンツ入力タブの直接クリックに失敗。既に選択済みの可能性。")

        # URLをテキストエリアに入力（改行区切り）
        url_text = "\n".join(urls)

        # Streamlitのtextareaを探す
        textarea = self.page.locator("textarea").first
        await textarea.fill(url_text)
        await self.page.wait_for_timeout(1000)

        logger.info(f"URL入力完了: {len(urls)}件")

    async def generate_content(self):
        """
        コンテンツを生成する。

        フロー:
        1. 「🤖 生成」タブをクリック
        2. URLソースの認識を確認
        3. 「生成」ボタンをクリック
        4. 生成完了を待機
        """
        logger.info("コンテンツ生成開始...")

        # 「🤖 生成」タブをクリック
        gen_tab = self.page.get_by_text("生成", exact=False)
        # 「コンテンツ生成」ではなく「🤖 生成」タブを探す
        tabs = self.page.locator("[data-baseweb='tab']")
        tab_count = await tabs.count()
        for i in range(tab_count):
            text = await tabs.nth(i).text_content()
            if "生成" in (text or "") and "コンテンツ" not in (text or ""):
                await tabs.nth(i).click()
                break

        await self.page.wait_for_timeout(2000)

        # 「生成」ボタンをクリック
        gen_button = self.page.get_by_role("button", name="生成")
        if await gen_button.count() > 0:
            await gen_button.first.click()
        else:
            # 代替: テキストで検索
            gen_button = self.page.locator("button:has-text('生成')")
            await gen_button.first.click()

        # 生成完了を待機（最大5分）
        logger.info("コンテンツ生成中... (最大5分待機)")
        await self._wait_for_generation_complete()

        logger.info("コンテンツ生成完了")

    async def save_content(self):
        """
        生成したコンテンツを保存する。

        フロー:
        1. 「👁️ プレビュー・保存」タブをクリック
        2. 「保存」ボタンをクリック
        """
        logger.info("コンテンツ保存開始...")

        # 「プレビュー・保存」タブをクリック
        tabs = self.page.locator("[data-baseweb='tab']")
        tab_count = await tabs.count()
        for i in range(tab_count):
            text = await tabs.nth(i).text_content()
            if "保存" in (text or "") or "プレビュー" in (text or ""):
                await tabs.nth(i).click()
                break

        await self.page.wait_for_timeout(2000)

        # 「保存」ボタンをクリック
        save_button = self.page.get_by_role("button", name="保存")
        if await save_button.count() > 0:
            await save_button.first.click()
        else:
            save_button = self.page.locator("button:has-text('保存')")
            await save_button.first.click()

        await self.page.wait_for_timeout(3000)
        logger.info("コンテンツ保存完了")

    # =========================================================================
    # 画像アップロード
    # =========================================================================
    async def upload_background_image(self, image_path: str):
        """
        システム設定ページで背景画像をアップロードする。

        Args:
            image_path: アップロードする画像ファイルのパス

        フロー:
        1. システム設定ページに遷移
        2. 背景画像のアップロード欄を特定
        3. ファイルを設定
        """
        logger.info(f"背景画像アップロード開始: {image_path}")

        if not Path(image_path).exists():
            raise FileNotFoundError(f"画像ファイルが見つかりません: {image_path}")

        await self.navigate_to_settings()
        await self.page.wait_for_timeout(2000)

        # ページを下にスクロールして背景画像セクションを表示
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self.page.wait_for_timeout(1000)

        # Streamlitのファイルアップローダを探す
        # file_input要素は通常hiddenなので、set_input_filesで直接設定
        file_input = self.page.locator(
            "section.main input[type='file']"
        ).first

        await file_input.set_input_files(image_path)
        await self.page.wait_for_timeout(3000)

        # アップロード完了の確認
        logger.info(f"背景画像アップロード完了: {image_path}")

    # =========================================================================
    # フロントエンドアプリURL取得
    # =========================================================================
    async def get_frontend_app_url(self) -> str:
        """
        候補者面談ページから「フロントエンドアプリを開く」のURLを取得する。

        Returns:
            フロントエンドアプリのURL

        フロー:
        1. 候補者面談ページに遷移
        2. サイドバー下部の「フロントエンドアプリを開く」リンクのhrefを取得
        """
        logger.info("フロントエンドアプリURL取得開始...")

        await self.navigate_to_candidate_interview()
        await self.page.wait_for_timeout(2000)

        # サイドバー内の「フロントエンドアプリを開く」リンクを探す
        sidebar = self.page.locator("[data-testid='stSidebar']")

        # リンクを探す
        link = sidebar.locator("a:has-text('フロントエンド')").first
        try:
            url = await link.get_attribute("href")
            if url:
                logger.info(f"フロントエンドURL取得成功: {url}")
                return url
        except Exception:
            pass

        # 代替: 全リンクを検索
        links = sidebar.locator("a")
        count = await links.count()
        for i in range(count):
            text = await links.nth(i).text_content()
            if "フロントエンド" in (text or "") or "アプリ" in (text or ""):
                url = await links.nth(i).get_attribute("href")
                if url:
                    logger.info(f"フロントエンドURL取得成功: {url}")
                    return url

        # さらに代替: ボタンの場合もある
        buttons = sidebar.locator("button:has-text('フロントエンド')")
        if await buttons.count() > 0:
            # ボタンをクリックして新しいタブのURLを取得
            async with self.page.context.expect_page() as new_page_info:
                await buttons.first.click()
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
        # Streamlitのローディングスピナーが消えるまで待つ
        try:
            spinner = self.page.locator("[data-testid='stSpinner']")
            await spinner.wait_for(state="hidden", timeout=NAVIGATION_TIMEOUT)
        except Exception:
            pass

        # 追加の安定化待機
        await self.page.wait_for_timeout(1500)

    async def _dismiss_popup(self):
        """ポップアップダイアログが表示されていたら閉じる"""
        try:
            close_btn = self.page.locator(
                "button[aria-label='Close'], "
                "button:has-text('×'), "
                "button:has-text('Close'), "
                "[data-dismiss='modal']"
            )
            if await close_btn.count() > 0:
                await close_btn.first.click()
                await self.page.wait_for_timeout(500)
        except Exception:
            pass

    async def _fill_labeled_input(self, label: str, value: str):
        """ラベル名を手がかりにして入力フィールドに値を設定する"""
        try:
            # Streamlitでは label要素の後にinputが来る
            label_el = self.page.get_by_text(label, exact=False).first
            # labelの親要素内のinputを取得
            container = label_el.locator("xpath=ancestor::div[contains(@class, 'stTextInput')]")
            input_el = container.locator("input").first
            await input_el.fill(value)
            await self.page.wait_for_timeout(300)
        except Exception as e:
            logger.warning(f"ラベル '{label}' の入力フィールドが見つかりません: {e}")

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
                    "[data-testid='stSpinner'], "
                    ".stSpinner, "
                    "div:has-text('生成中')"
                )
                spinner_visible = await spinner.is_visible()
            except Exception:
                pass

            if not spinner_visible:
                # 追加確認: エラーメッセージがないか
                try:
                    error = self.page.locator(
                        "[data-testid='stAlert'], "
                        ".stAlert"
                    )
                    if await error.count() > 0:
                        error_text = await error.first.text_content()
                        if "エラー" in (error_text or ""):
                            raise RuntimeError(
                                f"コンテンツ生成エラー: {error_text}"
                            )
                except Exception as e:
                    if "コンテンツ生成エラー" in str(e):
                        raise
                    pass

                logger.info(f"生成完了検出 (経過: {elapsed / 1000}秒)")
                return

            logger.debug(f"生成中... (経過: {elapsed / 1000}秒)")

        raise TimeoutError(
            f"コンテンツ生成がタイムアウトしました "
            f"({CONTENT_GENERATION_TIMEOUT / 1000}秒)"
        )
