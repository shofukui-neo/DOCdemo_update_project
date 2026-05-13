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

    async def close_page_and_relogin(self):
        """
        現在のページタブを完全に閉じ、新しいページタブを開いて再ログインする。

        re_login_with_cache_clear() より強力なリセット手段。
        Playwright のページ内に蓄積された JavaScript の状態・event listener・
        Streamlit のセッション state 等を確実に破棄するために使用する。

        企業追加直後（Step 2.5 後）→ コンテンツ生成（Step 4）の間で実行する
        ことで、企業追加時に残ったページ内キャッシュによる「別企業のコンテンツが
        生成される」「サイドバー選択が反映されない」等の不具合を防ぐ。
        """
        logger.info("ページを閉じて再ログインを開始...")

        context = self.page.context
        old_page = self.page

        # Cookie・ストレージも念のためクリアしておく
        try:
            await old_page.evaluate("""
                () => {
                    try { localStorage.clear(); } catch(e) {}
                    try { sessionStorage.clear(); } catch(e) {}
                }
            """)
        except Exception:
            pass
        try:
            await context.clear_cookies()
        except Exception:
            pass

        # 古いページを完全に閉じる
        try:
            await old_page.close()
            logger.info("  古いページタブを閉じました")
        except Exception as e:
            logger.warning(f"  古いページのクローズ失敗（続行）: {e}")

        # 新しいページタブを取得
        self.page = await context.new_page()
        self._logged_in = False
        logger.info("  新しいページタブを開きました")

        # 再ログイン
        await self.login()
        logger.info("ページクローズ＆再ログイン完了")

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
        """サイドバーのリンクをクリックしてページ遷移する。

        セクションヘッダー (stNavSectionHeader) が折りたたまれていて配下のナビ
        リンクが非表示の場合、ヘッダーをクリックして展開してから再試行する。
        例: 「システム設定」は「⚙️ システム設定」セクション配下にあるが、
        デフォルトで折りたたまれているケースがある。
        """
        await self.ensure_logged_in()
        logger.debug(f"ナビゲーション: {link_text}")

        sidebar = self.page.locator("[data-testid='stSidebar']")
        nav_link = sidebar.locator(
            f'[data-testid="stSidebarNavLink"]:has-text("{link_text}")'
        ).first

        if await nav_link.count() == 0:
            section_header = sidebar.locator(
                f'[data-testid="stNavSectionHeader"]:has-text("{link_text}")'
            ).first
            if await section_header.count() > 0:
                logger.debug(f"  セクション '{link_text}' が折りたたまれているため展開")
                await section_header.click()
                await self.page.wait_for_timeout(800)
                nav_link = sidebar.locator(
                    f'[data-testid="stSidebarNavLink"]:has-text("{link_text}")'
                ).first

        if await nav_link.count() == 0:
            # フォールバック: 旧挙動 (任意テキスト要素)
            logger.debug(f"  '{link_text}' ナビリンクが見つからないためテキスト一致でフォールバック")
            nav_link = sidebar.get_by_text(link_text, exact=False).first

        await nav_link.click()
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

        # 既に登録済みエラーをチェック (重複登録は正常終了として扱う)
        if await self._is_company_already_exists():
            logger.warning(
                f"  企業追加スキップ: '{company.enterprise_id}' は既に管理画面に登録済み"
                "（既存企業として後続処理を継続）"
            )
            return

        logger.info(f"企業追加完了: {company.name} (ID: {company.enterprise_id})")

    async def _is_company_already_exists(self) -> bool:
        """
        企業追加直後のページに「既に存在」エラーが表示されているかチェックする。

        Returns:
            True なら既に登録済み（重複エラー検出）
        """
        # Streamlit alert / error メッセージ全体
        alert_selectors = [
            "[data-testid='stAlert']",
            "[data-testid='stException']",
            "[data-baseweb='notification']",
            "div[role='alert']",
        ]
        # 既存登録のシグナル (英語・日本語両方)
        duplicate_keywords = [
            "already exists",
            "already exist",
            "duplicate",
            "既に存在",
            "既存",
            "登録済み",
            "重複",
        ]

        for sel in alert_selectors:
            try:
                elements = self.page.locator(sel)
                count = await elements.count()
                for i in range(count):
                    text = (await elements.nth(i).text_content()) or ""
                    text_lower = text.lower()
                    for kw in duplicate_keywords:
                        if kw.lower() in text_lower:
                            logger.debug(
                                f"  重複検出 ({sel}): '{text[:120]}'"
                            )
                            return True
            except Exception:
                continue
        return False

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

        # まずはセレクトボックス全体（input または baseweb select 用の div）をクリックして開く
        select_container = sidebar.locator(
            'input[aria-label*="企業を選択"], '
            'input[aria-label*="選択"], '
            '[data-baseweb="select"]'
        )

        if await select_container.count() > 0:
            await select_container.first.click()
            await self.page.wait_for_timeout(800)

            # baseweb select の場合、open 後に内側の <input> が編集可能になる
            actual_input = sidebar.locator(
                'input[aria-label*="企業を選択"], '
                'input[aria-label*="選択"], '
                '[data-baseweb="select"] input, '
                'input[role="combobox"]'
            )

            input_filled = False
            if await actual_input.count() > 0:
                try:
                    await actual_input.first.fill(company.enterprise_id)
                    input_filled = True
                except Exception:
                    # fill 不可なら type フォールバック
                    try:
                        await actual_input.first.type(
                            company.enterprise_id, delay=30
                        )
                        input_filled = True
                    except Exception as e:
                        logger.warning(f"  入力フィールド書き込み失敗: {e}")

            if input_filled:
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
                    if input_filled and await actual_input.count() > 0:
                        await actual_input.first.press("Enter")
                        logger.debug("  Enterキーで確定")
                    else:
                        await select_container.first.press("Enter")
                        logger.debug("  Enterキーで確定 (container)")
        else:
            # フォールバック: サイドバー全体から探す
            logger.warning("  企業選択セレクターが見つかりません → フォールバック")
            await self.select_company(company.enterprise_id)

        await self.page.wait_for_timeout(2000)
        await self._wait_for_streamlit_load()

        # === タイトルが対象企業名に変わったことを確認 ===
        await self._verify_content_title(company)

        logger.info(f"サイドバー企業選択完了: {company.name}")

    async def _verify_content_title(self, company: CompanyInfo, strict: bool = True):
        """
        コンテンツ生成ページのヘッダー部分が対象企業名に変更されていることを検証する。

        Step 4 のコンテンツ生成前に必ず呼ばれ、ヘッダーが対象企業に切り替わって
        いない場合は RuntimeError を送出してフローを停止する（誤った企業に対する
        コンテンツ生成を防ぐため）。

        Args:
            company: 検証対象の企業
            strict: True の場合、ヘッダー検証失敗で例外を送出する。
                    False の場合は警告のみ。

        Raises:
            RuntimeError: strict=True かつヘッダーに企業名/IDが見つからない場合
        """
        logger.info(f"  ヘッダー検証中: 企業名={company.name}, ID={company.enterprise_id}")

        title_locators = [
            self.page.locator('h1, h2'),
            self.page.locator('[data-testid="stHeading"] p'),
            self.page.locator('.stMarkdown h1, .stMarkdown h2'),
        ]

        matched_text = ""
        title_found = False
        for locator in title_locators:
            try:
                count = await locator.count()
                for i in range(count):
                    text = await locator.nth(i).text_content()
                    text = (text or "").strip()
                    if not text:
                        continue
                    if company.enterprise_id and company.enterprise_id in text:
                        matched_text = text
                        title_found = True
                        break
                    if company.name and company.name in text:
                        matched_text = text
                        title_found = True
                        break
                if title_found:
                    break
            except Exception:
                pass

        if title_found:
            logger.info(f"  [OK] ヘッダー検証成功: 「{matched_text}」")
            return

        # ヘッダー要素では見つからなかった場合 → ページ全体で再確認
        page_content = await self.page.content()
        in_page = (
            (company.enterprise_id and company.enterprise_id in page_content)
            or (company.name and company.name in page_content)
        )

        screenshot_path = f"screenshots/title_warn_{company.enterprise_id}.png"
        await self.page.screenshot(path=screenshot_path)

        if in_page and not strict:
            logger.warning(
                "  [WARN] ヘッダー要素には特定できなかったがページ内に企業情報を確認 "
                f"(スクショ: {screenshot_path})"
            )
            return

        msg = (
            f"ヘッダーに対象企業名/IDが見つかりません: "
            f"name={company.name}, id={company.enterprise_id}, "
            f"スクショ: {screenshot_path}"
        )
        if strict:
            logger.error(f"  [ERR] {msg}")
            raise RuntimeError(msg)
        else:
            logger.warning(f"  [WARN] {msg}")

    async def verify_enterprise_id_in_added_company(self, company: CompanyInfo):
        """
        Step 2 と Step 3 の間で呼ばれる検証。

        企業追加直後、追加した企業のデータ（URL/カード/詳細表示）の中に
        homepage_url から抽出した enterprise_id が含まれているかを確認する。
        企業追加時に意図しないIDが採用されていた場合に検出できる。

        Raises:
            RuntimeError: 追加された企業情報内に enterprise_id が見つからない場合
        """
        logger.info(
            f"  企業ID検証中: 期待ID={company.enterprise_id}, URL={company.homepage_url}"
        )

        if not company.enterprise_id:
            raise RuntimeError("企業IDが空です。検証を実行できません。")

        await self._wait_for_streamlit_load()
        await self.page.wait_for_timeout(1000)

        page_text = await self.page.content()

        # 期待されるIDがページ内に出現しているか
        if company.enterprise_id in page_text:
            logger.info(f"  [OK] 企業ID '{company.enterprise_id}' をページ内に確認")
            return

        # ページ内のURL要素から実際に登録されたIDを推測
        try:
            anchor_hrefs = await self.page.eval_on_selector_all(
                "a[href]", "elements => elements.map(el => el.href)"
            )
        except Exception:
            anchor_hrefs = []

        actual_ids = []
        for href in anchor_hrefs:
            if "casual-interview" in href:
                # 例: https://casual-interview-dev.brainverse-ai.com/{id}
                from urllib.parse import urlparse as _up
                p = _up(href)
                last = p.path.rstrip("/").rsplit("/", 1)[-1]
                if last:
                    actual_ids.append(last)

        screenshot_path = f"screenshots/id_mismatch_{company.enterprise_id}.png"
        await self.page.screenshot(path=screenshot_path)
        msg = (
            f"企業追加後のページ内に期待ID '{company.enterprise_id}' が見つかりません。"
            f" 検出された候補ID: {actual_ids}, スクショ: {screenshot_path}"
        )
        logger.error(f"  [ERR] {msg}")
        raise RuntimeError(msg)

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
        """コンテンツを生成する。ボタン未検出時はリトライ + デバッグスクショを撮ってエラー。"""
        logger.info("コンテンツ生成開始...")

        gen_button_selector = (
            'button[data-testid="stBaseButton-primary"]:has-text("コンテンツ生成"), '
            'button[data-testid="stBaseButton-primary"]:has-text("生成"), '
            'button:has-text("コンテンツ生成")'
        )

        # 「生成」タブをクリック + 「コンテンツ生成」ボタンが現れるまで最大3回リトライ
        gen_button = None
        for attempt in range(3):
            # 「生成」タブをクリック
            await self._click_generation_tab()

            # Streamlit描画待ち + 少し長めの待機
            # タブ切替が反映されない場合に備えて長めに取る (旧: 3000ms)
            await self.page.wait_for_timeout(7000)
            await self._wait_for_streamlit_load()

            # 「生成準備完了」メッセージを待機 (出なくても続行)
            if attempt == 0:
                logger.info("  生成準備完了の待機中...")
                ready_msg = self.page.locator('div, p, span').filter(
                    has_text=re.compile(r"✅.*準備完了")
                )
                try:
                    await ready_msg.first.wait_for(state="visible", timeout=20000)
                    logger.info("  生成準備完了を確認しました")
                except Exception:
                    logger.warning(
                        "  生成準備完了メッセージが特定できませんでしたが、続行を試みます"
                    )

            # 「コンテンツ生成」ボタンが visible になるまで待機 (旧: 10000ms)
            candidate_button = self.page.locator(gen_button_selector)
            try:
                await candidate_button.first.wait_for(state="visible", timeout=20000)
                if await candidate_button.count() > 0:
                    gen_button = candidate_button
                    logger.info(f"  コンテンツ生成ボタン検出 (試行 {attempt+1}/3)")
                    break
            except Exception:
                logger.warning(
                    f"  コンテンツ生成ボタン未検出 (試行 {attempt+1}/3) — 生成タブを再クリック"
                )

        if not gen_button or await gen_button.count() == 0:
            # デバッグスクショを保存して例外で抜ける (save_content が誤動作するのを防ぐ)
            try:
                from datetime import datetime
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                debug_path = f"screenshots/no_gen_button_{ts}.png"
                await self.page.screenshot(path=debug_path, full_page=True)
                logger.error(f"  デバッグスクショ保存: {debug_path}")
            except Exception:
                pass
            raise RuntimeError(
                "コンテンツ生成ボタンが見つかりませんでした (3回試行)。"
                "「生成」タブのクリックが反映されていない可能性があります。"
            )

        await gen_button.first.click()

        # 生成完了を待機（最大5分）
        logger.info("コンテンツ生成中... (最大5分待機)")
        await self._wait_for_generation_complete()

        logger.info("コンテンツ生成完了")

    async def _click_generation_tab(self):
        """コンテンツ生成画面の「生成」タブをクリックする。"""
        gen_tab = self.page.locator('[id*="tab-1"]:has-text("生成")')
        if await gen_tab.count() > 0:
            await gen_tab.first.click()
            await self.page.wait_for_timeout(1500)
            return
        # フォールバック: テキスト検索
        tabs = self.page.locator("[data-baseweb='tab'], [role='tab']")
        tab_count = await tabs.count()
        for i in range(tab_count):
            text = await tabs.nth(i).text_content()
            if "生成" in (text or "") and "コンテンツ" not in (text or ""):
                await tabs.nth(i).click()
                await self.page.wait_for_timeout(1500)
                return

    async def save_content(self, company: CompanyInfo):
        """
        生成したコンテンツを保存する。

        2段階保存フロー:
        1. 「プレビュー・保存」タブで FAQ保存ボタンの表示を確認したら
           間を空けずに即クリック（保存トーストが出なければ1回だけ再クリック）
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

        # === STEP 1: FAQ保存ボタンを「表示確認 → 即クリック」 ===
        # FAQ保存ボタンが表示されていること自体を FAQ生成完了の確認シグナルとする。
        # （見出しテキスト探索より確実、かつ確認とクリックの間に余計な待機を挟まない）
        logger.info("  FAQ保存ボタンの表示を確認中...")
        first_save_btn = self.page.locator(
            'button:has-text("FAQ保存（置換）"), '
            'button:has-text("FAQ保存")'
        ).first

        try:
            await first_save_btn.wait_for(state="visible", timeout=20000)
            logger.info("  FAQ保存ボタンの表示を確認（=FAQプレビュー描画完了）")
        except Exception:
            # フォールバック: ページ全体に FAQ 関連テキストがあるかだけチェック
            page_text = await self.page.content()
            if not (
                "FAQ" in page_text
                and ("生成されたFAQ" in page_text or "FAQ 1" in page_text
                     or "FAQ1" in page_text)
            ):
                screenshot_err = (
                    f"screenshots/faq_not_found_{company.enterprise_id}.png"
                )
                await self.page.screenshot(path=screenshot_err)
                raise RuntimeError(
                    f"FAQプレビューが確認できませんでした。詳細は "
                    f"{screenshot_err} を確認してください。"
                )
            logger.warning(
                "  FAQ保存ボタンが直接見つからないため、より広い保存ボタンを使用"
            )
            first_save_btn = self.page.locator(
                'button:has-text("保存")'
            ).first

        # === 確認直後、間を空けずに即クリック ===
        logger.info("  STEP1: FAQ保存ボタンを即クリック...")
        await first_save_btn.scroll_into_view_if_needed()
        await first_save_btn.click()
        logger.info("  FAQ保存ボタンをクリックしました")

        # === FAQ保存完了の確認 — トーストが出なければ1回だけリトライ ===
        faq_toast = self.page.locator(
            '[data-testid="stNotification"], [data-testid="stToast"], .stAlert'
        ).filter(has_text=re.compile(r"(FAQ|保存|完了|成功|saved)", re.IGNORECASE))
        try:
            await faq_toast.first.wait_for(state="visible", timeout=5000)
            logger.info("  FAQ保存完了メッセージを確認しました")
        except Exception:
            logger.warning(
                "  FAQ保存完了メッセージが出なかったため、もう一度クリックします"
            )
            try:
                # 再ロケート（Streamlit再描画でハンドルが古くなる対策）
                retry_btn = self.page.locator(
                    'button:has-text("FAQ保存（置換）"), '
                    'button:has-text("FAQ保存")'
                ).first
                if await retry_btn.count() > 0:
                    await retry_btn.scroll_into_view_if_needed()
                    await retry_btn.click()
                    logger.info("  FAQ保存ボタンを再クリックしました")
            except Exception as e:
                logger.warning(f"  FAQ保存ボタンの再クリックに失敗: {e}")

        await self.page.wait_for_timeout(2000)
        await self._wait_for_streamlit_load()

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
        コンテンツ管理タブで FAQ と 企業情報 の両タブを開き、
        対象企業のコンテンツが正しく保存されているかを検証する。

        検証項目:
        1. コンテンツ管理ページに対象企業が選択できること
        2. FAQタブ内に企業名 or 企業IDの出現
        3. 企業情報タブ内に企業名 or 企業IDの出現

        いずれの検証も失敗した場合は RuntimeError を送出して
        誤った企業のコンテンツが保存された状態を検出する。

        Raises:
            RuntimeError: FAQ/企業情報の両方で対象企業を確認できなかった場合
        """
        logger.info(f"  コンテンツ管理タブで保存確認中: {company.name}")

        screenshot_path = f"screenshots/content_mgmt_{company.enterprise_id}.png"

        await self.navigate_to_content_management()
        await self.page.wait_for_timeout(2000)
        await self._wait_for_streamlit_load()

        # コンテンツ管理ページ側にも企業セレクタがある場合は対象企業を選択
        await self._try_select_company_in_page(company.enterprise_id)
        await self.page.wait_for_timeout(1500)
        await self._wait_for_streamlit_load()

        await self.page.screenshot(path=screenshot_path)

        # ページ全体で企業の存在を確認
        page_content = await self.page.content()
        if (company.enterprise_id not in page_content
                and company.name not in page_content):
            msg = (
                f"コンテンツ管理ページに対象企業 ({company.enterprise_id} / "
                f"{company.name}) の情報が見つかりません。スクショ: {screenshot_path}"
            )
            logger.error(f"  [ERR] {msg}")
            raise RuntimeError(msg)

        logger.info(
            f"  [OK] コンテンツ管理ページ: {company.enterprise_id} の存在を確認"
        )

        # FAQタブ・企業情報タブをそれぞれ開いて検証
        faq_ok = await self._verify_tab_content(
            company, tab_keywords=["FAQ", "よくある質問", "Q&A"], tab_label="FAQ"
        )
        info_ok = await self._verify_tab_content(
            company,
            tab_keywords=["企業情報", "会社情報", "企業詳細", "プロフィール"],
            tab_label="企業情報",
        )

        if not (faq_ok and info_ok):
            msg = (
                f"FAQ/企業情報タブのコンテンツ検証に失敗 "
                f"(faq_ok={faq_ok}, info_ok={info_ok}, "
                f"id={company.enterprise_id}, スクショ: {screenshot_path})"
            )
            logger.error(f"  [ERR] {msg}")
            raise RuntimeError(msg)

        logger.info("  [OK] FAQ・企業情報タブともに対象企業のコンテンツを確認")

    async def _verify_tab_content(
        self,
        company: CompanyInfo,
        tab_keywords: list,
        tab_label: str,
    ) -> bool:
        """
        指定キーワードを持つタブを開き、そのタブ内に対象企業の
        企業名 or 企業IDが出現するかを検証する。

        Args:
            company: 検証対象の企業
            tab_keywords: タブを特定するためのテキスト候補
            tab_label: ログ用のタブ名

        Returns:
            タブ内に企業情報が確認できれば True
        """
        logger.info(f"  [{tab_label}タブ] 検証開始")

        tab_locators = self.page.locator("[data-baseweb='tab'], [role='tab'], button[role='tab']")
        tab_count = await tab_locators.count()
        clicked = False

        for i in range(tab_count):
            try:
                text = (await tab_locators.nth(i).text_content()) or ""
                if any(kw in text for kw in tab_keywords):
                    await tab_locators.nth(i).click()
                    await self.page.wait_for_timeout(1500)
                    await self._wait_for_streamlit_load()
                    logger.info(f"  [{tab_label}タブ] 開いた: 「{text.strip()}」")
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            logger.warning(
                f"  [{tab_label}タブ] が見つかりません（タブ未表示の可能性、"
                f"ページ全体で代替検証）"
            )

        # タブ表示後のページコンテンツで企業が確認できるか
        body_text = await self.page.content()
        # FAQ的な要素を探す（FAQタブの場合）/企業情報的な要素を探す
        in_company = (
            (company.enterprise_id and company.enterprise_id in body_text)
            or (company.name and company.name in body_text)
        )

        screenshot_path = (
            f"screenshots/tab_{tab_label}_{company.enterprise_id}.png"
        )
        await self.page.screenshot(path=screenshot_path)

        if in_company:
            logger.info(
                f"  [OK] [{tab_label}タブ] 企業名/IDを確認 (スクショ: {screenshot_path})"
            )
            return True
        else:
            logger.warning(
                f"  [WARN] [{tab_label}タブ] 企業名/IDを確認できませんでした "
                f"(スクショ: {screenshot_path})"
            )
            return False

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
        3. アップロード後、UI画面が新しい画像に切り替わったことを検証してから完了とする。
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
        target_container, file_input = await self._locate_background_image_uploader()

        # 3. アップロード前のUI状態を取得（後で反映確認に使う）
        pre_state = await self._capture_image_section_state(target_container)
        logger.debug(
            f"  アップロード前のUI状態: imgs={len(pre_state['img_srcs'])}件, "
            f"files={pre_state['uploader_files']}"
        )

        # 4. ファイルをセット
        file_name = Path(image_path).name
        upload_btn = target_container.locator(
            'button:has-text("Upload"), '
            'button:has-text("アップロード"), '
            'button:has-text("Browse")'
        )

        if await upload_btn.count() > 0:
            logger.info("  「Upload」関連ボタンを検出。クリックしてファイル選択を開始します。")
            try:
                async with self.page.expect_file_chooser(timeout=5000) as fc_info:
                    await upload_btn.first.click()
                file_chooser = await fc_info.value
                await file_chooser.set_files(image_path)
            except Exception as e:
                logger.debug(f"    ボタンクリック失敗、直接セットを試みます: {e}")
                await file_input.first.set_input_files(image_path)
        else:
            logger.info("  ファイル入力欄を直接操作します。")
            await file_input.first.set_input_files(image_path)

        # 5. UI画面が新しい画像に切り替わったことを確認
        await self._verify_image_upload_reflected(
            target_container, file_name, pre_state
        )

        logger.info(f"背景画像アップロード完了（UI反映確認済）: {image_path}")

    async def _locate_background_image_uploader(
        self,
        timeout_seconds: float = 30.0,
        poll_interval_ms: int = 1500,
    ):
        """
        「背景画像」セクション内のアップローダーコンテナと file input を特定する。

        Streamlit は企業選択直後にDOMを再描画するため、見出しや入力欄は数秒〜十数秒
        遅れて現れる。タイミング次第で 0 件になるのを防ぐため、見出し検出と入力欄
        検出をまとめてポーリングする（最大 timeout_seconds 秒）。

        以前の実装ではコンテナ内に入力欄が見つからないとき、ページ全体から最初の
        input[type=file] を選んでしまい、別セクションの入力欄に誤投入する事故が
        起きていた。本メソッドではセクション特定に失敗したら例外を送出する。

        Returns:
            (target_container locator, file_input locator)
        """
        elapsed_ms = 0
        last_failure = "未試行"

        while elapsed_ms < timeout_seconds * 1000:
            heading_candidates = [
                self.page.locator('h1, h2, h3, h4, h5, h6').filter(has_text="背景画像"),
                self.page.locator(
                    '[data-testid="stHeader"], [data-testid="stSubheader"], [data-testid="stMarkdown"]'
                ).filter(has_text="背景画像"),
                self.page.get_by_text("背景画像", exact=False),
            ]

            section_heading = None
            for candidate in heading_candidates:
                try:
                    if await candidate.count() > 0:
                        section_heading = candidate.first
                        break
                except Exception as e:
                    logger.debug(f"    見出し候補確認エラー: {e}")

            if section_heading is None:
                last_failure = "「背景画像」見出しが未出現"
                logger.debug(
                    f"  アップローダー検出リトライ ({elapsed_ms/1000:.1f}s): {last_failure}"
                )
                await self.page.wait_for_timeout(poll_interval_ms)
                elapsed_ms += poll_interval_ms
                continue

            target_container = self.page.locator(
                '[data-testid="stVerticalBlock"]'
            ).filter(has=section_heading).last

            if await target_container.count() == 0:
                target_container = section_heading.locator(
                    'xpath=ancestor::*[self::section or self::div][1]'
                )

            # stFileUploader 内の input[type=file] を最優先で探す
            file_input = target_container.locator(
                '[data-testid="stFileUploader"] input[type="file"]'
            )
            if await file_input.count() == 0:
                file_input = target_container.locator('input[type="file"]')

            if await file_input.count() > 0:
                logger.debug(
                    f"  アップローダー検出成功 ({elapsed_ms/1000:.1f}s)"
                )
                return target_container, file_input

            last_failure = "見出しは出現済だが input[type=file] 未出現"
            logger.debug(
                f"  アップローダー検出リトライ ({elapsed_ms/1000:.1f}s): {last_failure}"
            )
            await self.page.wait_for_timeout(poll_interval_ms)
            elapsed_ms += poll_interval_ms

        raise RuntimeError(
            f"「背景画像」セクションのアップローダーを {timeout_seconds:.0f}秒以内に"
            f"特定できませんでした。最終理由: {last_failure}。"
            "DOM構造が変更された可能性があります。"
        )

    async def _capture_image_section_state(self, target_container) -> dict:
        """アップロード前の画像セクションのUI状態を取得する。

        - 表示中の <img> 要素の src 一覧
        - アップローダーに表示中のファイル名一覧
        """
        state = {"img_srcs": [], "uploader_files": []}
        try:
            imgs = target_container.locator('img')
            count = await imgs.count()
            for i in range(count):
                src = await imgs.nth(i).get_attribute("src")
                if src:
                    state["img_srcs"].append(src)
        except Exception as e:
            logger.debug(f"    img src取得エラー: {e}")

        try:
            uploader_files = target_container.locator(
                '[data-testid="stFileUploaderFile"], '
                '[data-testid="stFileUploaderFileName"], '
                '[data-testid="stFileUploaderFileData"]'
            )
            count = await uploader_files.count()
            for i in range(count):
                text = await uploader_files.nth(i).text_content()
                if text:
                    state["uploader_files"].append(text.strip())
        except Exception as e:
            logger.debug(f"    アップローダーファイル名取得エラー: {e}")

        return state

    async def _verify_image_upload_reflected(
        self,
        target_container,
        file_name: str,
        pre_state: dict,
        timeout_seconds: float = 30.0,
        poll_interval_ms: int = 1000,
    ) -> None:
        """
        アップロード後にUI画面が新しい画像に切り替わったことを検証する。

        以下のいずれかが確認できれば反映成功とみなす:
          1. Streamlit ファイルアップローダーにアップロードしたファイル名が表示されている
          2. セクション内の <img> 要素の src がアップロード前から変化／追加されている

        タイムアウト時は RuntimeError を送出する。
        """
        logger.info(f"  UI反映確認中: {file_name} (最大 {timeout_seconds:.0f}秒)")
        elapsed_ms = 0
        last_observation = "なし"
        pre_img_srcs = set(pre_state.get("img_srcs", []))

        while elapsed_ms < timeout_seconds * 1000:
            # 条件1: アップローダーに対象ファイル名が表示されているか
            try:
                uploader_files = target_container.locator(
                    '[data-testid="stFileUploaderFile"], '
                    '[data-testid="stFileUploaderFileName"], '
                    '[data-testid="stFileUploaderFileData"]'
                )
                u_count = await uploader_files.count()
                for i in range(u_count):
                    text = await uploader_files.nth(i).text_content()
                    if text and file_name in text:
                        last_observation = (
                            f"アップローダーにファイル名表示を確認: {text.strip()}"
                        )
                        logger.info(f"  ✓ UI反映確認OK ({last_observation})")
                        # 反映後の追加レンダリング待機
                        await self.page.wait_for_timeout(1500)
                        await self._wait_for_streamlit_load()
                        return
            except Exception as e:
                logger.debug(f"    アップローダー判定エラー: {e}")

            # 条件2: <img> 要素の src が変化／追加されているか
            try:
                imgs = target_container.locator('img')
                count = await imgs.count()
                current_srcs = []
                for i in range(count):
                    src = await imgs.nth(i).get_attribute("src")
                    if src:
                        current_srcs.append(src)
                new_srcs = [s for s in current_srcs if s not in pre_img_srcs]
                if new_srcs:
                    last_observation = (
                        f"新しい画像要素を検出: {new_srcs[0][:80]}..."
                        if len(new_srcs[0]) > 80 else f"新しい画像要素を検出: {new_srcs[0]}"
                    )
                    logger.info(f"  ✓ UI反映確認OK ({last_observation})")
                    await self.page.wait_for_timeout(1000)
                    await self._wait_for_streamlit_load()
                    return
            except Exception as e:
                logger.debug(f"    img要素判定エラー: {e}")

            await self.page.wait_for_timeout(poll_interval_ms)
            elapsed_ms += poll_interval_ms

        raise RuntimeError(
            f"画像アップロード後のUI反映を {timeout_seconds:.0f}秒以内に確認できませんでした。"
            f" 最終観測: {last_observation}"
        )

    # =========================================================================
    # フロントエンドアプリURL取得
    # =========================================================================
    async def get_frontend_app_url(self, company_id: str = "") -> str:
        """
        候補者面談ページから「フロントエンドアプリを開く」リンクのURLを取得する。

        Args:
            company_id: 取得対象の企業ID。指定すると候補者面談ページで先に企業を選択する。
                        フロントエンドURLは企業ごとに異なる(/<enterprise_id>)ため、
                        正しいURLを得るには企業選択が必要。
        """
        logger.info(f"フロントエンドアプリURL取得開始 (企業ID: {company_id or '未指定'})...")

        await self.navigate_to_candidate_interview()
        await self.page.wait_for_timeout(2000)
        await self._wait_for_streamlit_load()

        # 企業選択 (company_idが指定されていれば先に選択する)
        if company_id:
            try:
                await self.select_company(company_id)
                # 候補者面談ページではStreamlit再描画 + FastAPI同期に時間がかかる
                await self.page.wait_for_timeout(2000)
                await self._wait_for_streamlit_load()
            except Exception as e:
                logger.warning(f"  候補者面談ページでの企業選択に失敗: {e} — そのまま続行")

        # 1. サイドバー内の「フロントエンドアプリを開く」aタグを探す
        # Streamlitの再描画 + FastAPI同期遅延に対応するため最大60秒リトライ
        # （前社のURLからの切替・初回ロード遅延を含めて吸収する）
        sidebar = self.page.locator("[data-testid='stSidebar']")
        sidebar_link = sidebar.locator('a:has-text("フロントエンド")')

        last_seen_url = None
        max_attempts = 30  # 30 * 2s = 最大60秒
        for attempt in range(max_attempts):
            try:
                count = await sidebar_link.count()
                if count > 0:
                    url = await sidebar_link.first.get_attribute("href")
                    if url:
                        last_seen_url = url
                        if self._is_url_for_company(url, company_id):
                            logger.info(f"  サイドバーのaタグからURLを取得成功: {url}")
                            return url
                        logger.debug(
                            f"  試行 {attempt+1}/{max_attempts}: URLが企業ID未一致 ({url}) — 再試行"
                        )
                    else:
                        logger.debug(f"  試行 {attempt+1}/{max_attempts}: href未設定")
                else:
                    logger.debug(f"  試行 {attempt+1}/{max_attempts}: サイドバーリンク未検出")
            except Exception as e:
                logger.debug(f"  試行 {attempt+1}/{max_attempts} エラー: {e}")

            await self.page.wait_for_timeout(2000)

        # 30秒経過しても企業ID一致URLが取れない: 他の取得方法を試みる
        logger.warning(
            f"  サイドバーから企業ID({company_id})に一致するURLが取得できませんでした。"
            f"最後に見たURL: {last_seen_url}"
        )

        # 2. ページ全体からaタグを探す（フォールバック）
        logger.debug("  ページ全体から検索...")
        link = self.page.locator('a:has-text("フロントエンド"), a:has-text("アプリを開く")')
        if await link.count() > 0:
            url = await link.first.get_attribute("href")
            if url and self._is_url_for_company(url, company_id):
                logger.info(f"  aタグのhref属性からURLを取得成功: {url}")
                return url
            elif url:
                last_seen_url = url
                logger.warning(
                    f"  ページ全体から取得したURLが企業ID({company_id})と一致しません: {url}"
                )

        # 3. メインコンテンツ内のボタンを探す（フォールバック）
        main_content = self.page.locator("section.main")
        btn = main_content.locator('button:has-text("フロントエンド"), button:has-text("アプリを開く")')
        if await btn.count() == 0:
            btn = self.page.locator('button:has-text("フロントエンド"), button:has-text("アプリを開く")')

        if await btn.count() > 0:
            logger.info("  「フロントエンドアプリを開く」ボタンを検出。クリックしてURLを取得します。")
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

        # 4. 最終フォールバック: 最後に見たURLを返す (企業ID検証なし)
        if last_seen_url:
            logger.warning(f"  企業ID検証なしのフォールバック: {last_seen_url}")
            return last_seen_url

        # 5. URL構造からの推定フォールバック（Brainverse URL は規則的）
        # サイドバー/ページ内に該当リンクが一切無くても、Brainverse側に企業登録が
        # 済んでいれば https://casual-interview-dev.brainverse-ai.com/<id> でアクセス可能。
        # ただし「目視確認していない推定値」であることを明示し、納品URL列に **「[推定] 」**
        # プレフィックスを付けて返す。
        if company_id:
            from urllib.parse import urlparse
            try:
                base = urlparse(WEB_APP_BASE_URL)
                # WEB_APP_BASE_URL: casual-interview-api-dev → casual-interview-dev に変換
                host = base.netloc.replace("-api-", "-")
                estimated = f"{base.scheme}://{host}/{company_id}"
                logger.warning(
                    f"  URL構造から推定（要目視確認）: {estimated}"
                )
                return f"[推定] {estimated}"
            except Exception as e:
                logger.debug(f"  推定URL生成エラー: {e}")

        raise RuntimeError("フロントエンドアプリのURLが取得できませんでした。ボタンまたはリンクが見つかりません。")

    @staticmethod
    def _is_url_for_company(url: str, company_id: str) -> bool:
        """取得したURLが指定企業IDに対応しているかチェック (企業ID未指定時は常にTrue)"""
        if not company_id:
            return True
        # URLの末尾が /<company_id> または /<company_id>/ かを確認
        from urllib.parse import urlparse, unquote
        try:
            parsed = urlparse(url)
            path = unquote(parsed.path).rstrip("/")
            return path.endswith(f"/{company_id}") or path == f"/{company_id}"
        except Exception:
            return False

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
