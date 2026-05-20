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
    FAQ_VERIFY_TIMEOUT_SECONDS,
    SERVER_DOWN_HTTP_STATUSES,
    SERVER_DOWN_ERROR_PATTERNS,
    SERVER_HEALTH_CHECK_TIMEOUT_SECONDS,
    SERVER_RECOVERY_MAX_WAIT_MINUTES,
    SERVER_RECOVERY_POLL_INTERVAL_SECONDS,
)
from models import CompanyInfo

logger = logging.getLogger(__name__)


class ContentSaveVerificationError(Exception):
    """
    FAQ/企業情報の生成・保存検証に失敗したことを示す例外。

    orchestrator はこれを捕捉して Step 4 (コンテンツ生成) から再試行する。

    Attributes:
        reason_code: 失敗カテゴリ
            - "no_faq_on_any_tab"   : 全タブ走査してもFAQが見つからない (生成失敗の可能性大)
            - "server_error"        : Streamlit の st.error alert が検出された
            - "wrong_company"       : FAQ は出ているが対象企業のデータではない (混入疑い)
            - "tab_unreachable"     : 「プレビュー・保存」タブの DOM 切替が反映されなかった
            - "save_button_missing" : FAQ保存ボタンが特定できない
        diagnostics: 失敗時の追加情報 (検証タブ・パターン一致件数・抜粋テキスト等)
    """

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "unknown",
        diagnostics: Optional[dict] = None,
    ):
        super().__init__(message)
        self.reason_code = reason_code
        self.diagnostics = diagnostics or {}


class ServerDownError(Exception):
    """
    Brainverse 管理画面サーバーが落ちている (5xx / 接続不可) ことを示す例外。

    orchestrator / verify_quality はこれを捕捉して:
        1. 現企業の進捗ステータスは「エラー」に落とさず保持
        2. サーバー復旧を一定時間ポーリング待機 (wait_for_server_recovery)
        3. 復旧したら再ログインして同じ企業から処理再開
        4. 復旧しなければクリーンに中断して残数を報告
    """
    pass


def is_server_down_error(err: BaseException) -> bool:
    """例外メッセージから「サーバー到達不能」サインを検出する。"""
    if err is None:
        return False
    msg = str(err) or ""
    return any(pat in msg for pat in SERVER_DOWN_ERROR_PATTERNS)


def check_server_alive(
    url: str = WEB_APP_BASE_URL,
    timeout: float = SERVER_HEALTH_CHECK_TIMEOUT_SECONDS,
) -> bool:
    """
    管理画面サーバーが生きているかを軽量に確認する (同期・requests版)。

    判定:
        True  : HTTPステータス < 500 を返した (401/403/302 等もログイン必要だが生存)
        False : 接続不可 (ECONNREFUSED等) or 5xx
    """
    try:
        import requests
    except ImportError:
        logger.warning("requests 未インストールのため check_server_alive をスキップ (生存扱い)")
        return True

    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True)
        alive = r.status_code not in SERVER_DOWN_HTTP_STATUSES and r.status_code < 600
        logger.debug(f"check_server_alive({url}) → status={r.status_code} alive={alive}")
        return alive
    except Exception as e:
        logger.debug(f"check_server_alive({url}) → 例外 {type(e).__name__}: {e}")
        return False


async def wait_for_server_recovery(
    url: str = WEB_APP_BASE_URL,
    max_wait_minutes: int = SERVER_RECOVERY_MAX_WAIT_MINUTES,
    poll_interval_seconds: int = SERVER_RECOVERY_POLL_INTERVAL_SECONDS,
) -> bool:
    """
    サーバー復旧をポーリング待機する。

    Returns:
        True  : 復旧を検出
        False : max_wait_minutes を超えても復旧しなかった
    """
    import time

    deadline = time.monotonic() + max_wait_minutes * 60
    attempt = 0
    logger.warning(
        f"[ServerDown] 復旧を待機します (最大 {max_wait_minutes}分、"
        f"{poll_interval_seconds}秒おきにヘルスチェック)"
    )
    # 1回目は即チェック (落ち始めの瞬間に呼ばれた可能性があるため)
    while True:
        attempt += 1
        if check_server_alive(url):
            logger.info(
                f"[ServerDown] サーバー復旧を検出 (ヘルスチェック {attempt} 回目)"
            )
            return True
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            logger.error(
                f"[ServerDown] 最大待機時間 {max_wait_minutes}分 を超過 — 復旧を諦めます"
            )
            return False
        logger.warning(
            f"[ServerDown] サーバー未復旧 (試行 {attempt} 回目、残り {remaining}秒) — "
            f"{poll_interval_seconds}秒後に再確認"
        )
        await asyncio.sleep(min(poll_interval_seconds, max(remaining, 1)))


# 生成完了の正シグナル (画面上の成功メッセージ)。
# 「✅ コンテンツが正常に生成されました！」「31個のFAQを生成しました」
# 「企業情報が正常に生成されました」等が画面に出ていれば、
# たとえ st.status のラベルに「FAQを生成中…」が居残っていても
# 「生成は完了している」と判定するためのオーバーライド用パターン。
GENERATION_COMPLETE_PATTERN = re.compile(
    r"("
    r"コンテンツが正常に生成されました|"
    r"企業情報が正常に生成されました|"
    r"FAQを生成しました|"           # 「31個のFAQを生成しました」等
    r"生成されたFAQ|"
    r"生成された企業情報|"
    r"生成完了|生成が完了|"
    r"保存可能|保存準備|"
    r"プレビューで確認|プレビュー・保存で確認"
    r")"
)


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
        """ログイン画面で認証を実行する。リトライ機能付き。

        ページ遷移後にメール欄が出ない (= 既にログイン済) 場合は
        サイドバーの出現を確認して成功扱いにする。
        """
        logger.info("管理画面にログイン中...")

        email_selector = 'input[aria-label="メールアドレス"]'
        password_selector = 'input[aria-label="パスワード"]'
        sidebar_selector = "[data-testid='stSidebar']"

        for attempt in range(RETRY_COUNT):
            try:
                try:
                    response = await self.page.goto(
                        WEB_APP_BASE_URL,
                        wait_until="networkidle",
                        timeout=PAGE_LOAD_TIMEOUT,
                    )
                except Exception as goto_err:
                    # 接続不可系のエラー (ERR_CONNECTION_REFUSED 等) は即 ServerDownError
                    if is_server_down_error(goto_err):
                        raise ServerDownError(
                            f"管理画面 ({WEB_APP_BASE_URL}) への接続に失敗: {goto_err}"
                        ) from goto_err
                    raise

                # 5xx を返した場合もサーバーダウン扱い
                if response is not None:
                    status = response.status
                    if status in SERVER_DOWN_HTTP_STATUSES or status >= 500:
                        raise ServerDownError(
                            f"管理画面 ({WEB_APP_BASE_URL}) が HTTP {status} を返しました"
                        )

                await self._wait_for_streamlit_load()
                await self._dismiss_popup()

                # === 既にログイン済みかをまず判定 ===
                # メール欄 と サイドバー のどちらが先に visible になるかを競う
                email_field = self.page.locator(email_selector)
                sidebar = self.page.locator(sidebar_selector)

                already_logged_in = False
                try:
                    if await sidebar.first.is_visible() and await email_field.count() == 0:
                        already_logged_in = True
                except Exception:
                    pass

                if already_logged_in:
                    logger.info("既にログイン済みのセッションを検出 → ログイン処理スキップ")
                    self._logged_in = True
                    await self._wait_for_streamlit_load()
                    return

                # ログイン画面の要素を待機
                await self.page.wait_for_selector(
                    email_selector, timeout=ELEMENT_WAIT_TIMEOUT
                )

                # メールアドレス入力
                await self.page.locator(email_selector).fill(LOGIN_EMAIL)

                # パスワード入力
                await self.page.locator(password_selector).fill(LOGIN_PASSWORD)

                # ログインボタンクリック
                login_btn = self.page.locator('button:has-text("ログイン")')
                await login_btn.click()

                # ログイン完了（サイドバーの出現）を待機
                await self.page.wait_for_selector(
                    sidebar_selector, timeout=ELEMENT_WAIT_TIMEOUT
                )
                await self._wait_for_streamlit_load()

                self._logged_in = True
                logger.info("ログイン成功")
                return

            except ServerDownError:
                # サーバー側がダウンしている場合は試行を重ねても無駄 → 即座に伝播
                raise
            except Exception as e:
                # 例外メッセージから「実はサーバーダウン」を救済判定
                if is_server_down_error(e):
                    raise ServerDownError(
                        f"ログイン中に管理画面サーバー到達不能: {e}"
                    ) from e

                # メール欄不在 + サイドバー出現で「既にログイン済み」だった可能性を救済
                try:
                    if await self.page.locator(sidebar_selector).first.is_visible():
                        logger.info(
                            f"ログイン試行 {attempt + 1}/{RETRY_COUNT} 中に例外発生だが "
                            f"サイドバーは表示済み → ログイン済みとして扱う ({e})"
                        )
                        self._logged_in = True
                        return
                except Exception:
                    pass

                logger.warning(f"ログイン試行 {attempt + 1}/{RETRY_COUNT} 失敗: {e}")
                if attempt < RETRY_COUNT - 1:
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    # 最終試行も失敗 → ヘルスチェックで「実はサーバーダウン」を最終救済
                    if not check_server_alive():
                        raise ServerDownError(
                            f"ログイン全試行失敗 + ヘルスチェック陰性 — サーバーダウンと判定: {e}"
                        ) from e
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
        """ログイン済みか確認し、未ログインなら再ログイン。

        セッション切れ判定は誤検出を避けるため強化:
            旧: 「ログイン」テキストを含む button が visible なら切れ判定
                → ページ内に残った別ボタン (例: ヘッダーの「ログイン」、ログアウト
                確認モーダル等) に反応し、ログイン済みなのに再ログインを誘発し、
                10s × 3回のメール欄探索タイムアウトに繋がっていた。
            新: 以下を AND 条件で要求する:
                1. サイドバーが visible でない (= 認証後ページではない)
                2. メールアドレス入力欄 が visible (= ログイン画面に居る)
                3. パスワード入力欄 が visible (= ログインフォーム描画完了)
                これによりログイン画面に戻された場合のみ再ログイン実行する。
        """
        if not self._logged_in:
            await self.login()
            return

        # セッション切れチェック (誤検出回避のため複数条件 AND)
        try:
            sidebar = self.page.locator("[data-testid='stSidebar']").first
            email_field = self.page.locator(
                'input[aria-label="メールアドレス"]'
            ).first
            password_field = self.page.locator(
                'input[aria-label="パスワード"]'
            ).first

            sidebar_visible = False
            try:
                sidebar_visible = await sidebar.is_visible()
            except Exception:
                pass

            # サイドバーが見えていれば認証後ページ → セッション維持と判断
            if sidebar_visible:
                return

            # ログインフォームの「両方」が visible なときだけ切れ判定
            email_visible = False
            password_visible = False
            try:
                email_visible = await email_field.is_visible()
                password_visible = await password_field.is_visible()
            except Exception:
                pass

            if email_visible and password_visible:
                logger.warning(
                    "セッション切れ検出 (ログインフォーム再表示) → 再ログイン"
                )
                self._logged_in = False
                await self.login()
            # それ以外の中間状態 (Streamlit rerun 直後等) は静観
        except Exception as e:
            logger.debug(f"  ensure_logged_in 中の例外を無視: {e}")

    # =========================================================================
    # ナビゲーション
    # =========================================================================
    async def _navigate_sidebar(self, link_text: str):
        """サイドバーのリンクをクリックしてページ遷移する。

        ナビゲーション戦略 (優先度順):
            1. ``stSidebarNavLink:has-text(link_text)`` の visible マッチ
            2. すべての ``stNavSectionHeader`` を順次展開して再試行
            3. ``sidebar.get_by_text(link_text)`` の visible 要素のみ採用

        強化点 (2026-05-19):
            - 旧フォールバック ``sidebar.get_by_text(link_text).first`` は非可視
              要素 (collapsed section 内のスタブ等) を掴み、click が 30s
              タイムアウトしていた → visible 要素のみ採用。
            - サイドバー自体の visible を保証してから探索 (描画前/ログアウト後
              の不毛な探索を省く)。
            - 全戦略失敗時は診断情報付き RuntimeError を上げる
              (旧: 0件 locator に click → 30s タイムアウトで終了)。
            - click タイムアウトを Playwright デフォルト 30s → 10s に短縮。
              (リトライ含めて 90s 浪費していたのを 30s に抑制)
        """
        await self.ensure_logged_in()
        logger.debug(f"ナビゲーション: {link_text}")

        sidebar = self.page.locator("[data-testid='stSidebar']")

        # サイドバー自体の visible 保証
        try:
            await sidebar.first.wait_for(
                state="visible", timeout=ELEMENT_WAIT_TIMEOUT
            )
        except Exception as wait_err:
            if is_server_down_error(wait_err):
                raise ServerDownError(
                    f"サイドバー描画前に管理画面サーバー到達不能: {wait_err}"
                ) from wait_err
            logger.warning(
                f"  サイドバー未表示 ({wait_err}) → セッション復旧を試行"
            )
            self._logged_in = False
            await self.ensure_logged_in()
            await sidebar.first.wait_for(
                state="visible", timeout=ELEMENT_WAIT_TIMEOUT
            )

        async def _find_visible_nav_link():
            link = sidebar.locator(
                f'[data-testid="stSidebarNavLink"]:has-text("{link_text}")'
            ).first
            try:
                if await link.count() > 0 and await link.is_visible():
                    return link
            except Exception:
                pass
            return None

        nav_link = await _find_visible_nav_link()

        # 折りたたまれた可能性のあるセクションヘッダーを順に展開
        if nav_link is None:
            section_headers = sidebar.locator(
                '[data-testid="stNavSectionHeader"]'
            )
            try:
                section_count = await section_headers.count()
            except Exception:
                section_count = 0

            for sh_idx in range(section_count):
                header = section_headers.nth(sh_idx)
                try:
                    if not await header.is_visible():
                        continue
                    header_text = (await header.text_content()) or ""
                except Exception:
                    continue
                logger.debug(
                    f"  セクション '{header_text.strip()}' を展開して "
                    f"'{link_text}' を再探索"
                )
                try:
                    await header.click(timeout=3000)
                    await self.page.wait_for_timeout(600)
                except Exception as click_err:
                    logger.debug(
                        f"  セクション展開クリック失敗 (続行): {click_err}"
                    )
                    continue

                nav_link = await _find_visible_nav_link()
                if nav_link is not None:
                    break

        # フォールバック: テキスト一致 (visible のみ採用)
        if nav_link is None:
            logger.debug(
                f"  '{link_text}' ナビリンクが見つからないためテキスト一致で"
                "フォールバック"
            )
            candidate = sidebar.get_by_text(link_text, exact=False)
            try:
                cand_count = await candidate.count()
            except Exception:
                cand_count = 0
            for ci in range(cand_count):
                el = candidate.nth(ci)
                try:
                    if await el.is_visible():
                        nav_link = el
                        break
                except Exception:
                    continue

        if nav_link is None:
            screenshot_err = (
                f"screenshots/sidebar_nav_not_found_{link_text}.png"
            )
            try:
                await self.page.screenshot(path=screenshot_err, full_page=True)
            except Exception:
                screenshot_err = "<screenshot 取得失敗>"
            try:
                sidebar_text_excerpt = (
                    (await sidebar.first.inner_text(timeout=2000))[:300]
                )
            except Exception:
                sidebar_text_excerpt = "<inner_text 取得失敗>"
            raise RuntimeError(
                f"サイドバーに '{link_text}' ナビリンクが見つかりません。"
                f" スクショ: {screenshot_err} / サイドバー抜粋: "
                f"{sidebar_text_excerpt!r}"
            )

        # クリック直前に visible 再確認 (DOM rerun でフェードアウト中の可能性)
        try:
            await nav_link.wait_for(state="visible", timeout=5000)
        except Exception:
            pass

        # クリックタイムアウトを 30s → 10s に短縮
        try:
            await nav_link.click(timeout=ELEMENT_WAIT_TIMEOUT)
        except Exception as click_err:
            screenshot_err = (
                f"screenshots/sidebar_nav_click_fail_{link_text}.png"
            )
            try:
                await self.page.screenshot(path=screenshot_err, full_page=True)
            except Exception:
                screenshot_err = "<screenshot 取得失敗>"
            if is_server_down_error(click_err):
                raise ServerDownError(
                    f"サイドバー '{link_text}' クリック中に管理画面サーバー"
                    f"到達不能: {click_err}"
                ) from click_err
            raise RuntimeError(
                f"サイドバー '{link_text}' クリック失敗: {click_err} / "
                f"スクショ: {screenshot_err}"
            ) from click_err

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
        aria-label でヒットしない場合は label テキスト近傍 → stTextInput順 と
        フォールバックを重ねる。すべて失敗した場合は RuntimeError を送出して
        「空のフォームのまま送信される」事故を防ぐ。
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

        # フォームが描画されるのを明示的に待機 (少なくとも 1 つの text input)
        try:
            await self.page.wait_for_selector(
                '[data-testid="stTextInput"] input, input[type="text"]',
                timeout=ELEMENT_WAIT_TIMEOUT,
            )
        except Exception:
            logger.warning("  企業追加フォームの入力欄出現を確認できませんでした (続行)")

        # --- 各フィールドに入力 (複数のセレクタ戦略でフォールバック) ---
        filled = {
            "enterprise_id": await self._fill_company_form_field(
                value=company.enterprise_id,
                aria_label_keywords=["企業ID", "Enterprise ID", "enterprise_id", "ID"],
                label_keywords=["企業ID", "Enterprise ID"],
                fallback_index=0,
                field_name="企業ID",
            ),
            "display_name": await self._fill_company_form_field(
                value=company.name,
                aria_label_keywords=["表示名", "音声読み上げ", "Display", "Company Name", "名"],
                label_keywords=["表示名", "音声読み上げ", "Display Name"],
                fallback_index=1,
                field_name="表示名",
            ),
            "homepage_url": await self._fill_company_form_field(
                value=company.homepage_url,
                aria_label_keywords=["WebサイトURL", "Website", "URL"],
                label_keywords=["WebサイトURL", "Website URL", "URL"],
                fallback_index=2,
                field_name="WebサイトURL",
            ),
        }

        if not all(filled.values()):
            missing = [k for k, v in filled.items() if not v]
            ts_id = company.enterprise_id or "unknown"
            screenshot_path = f"screenshots/form_field_missing_{ts_id}.png"
            try:
                await self.page.screenshot(path=screenshot_path, full_page=True)
            except Exception:
                pass
            raise RuntimeError(
                f"企業追加フォームの入力欄が特定できませんでした: 未入力={missing}, "
                f"スクショ: {screenshot_path}"
            )

        # ページ下部までスクロールしてボタンを表示
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self.page.wait_for_timeout(1000)

        # 「Create Company」ボタンをクリック（英語名）
        create_btn = self.page.locator('button:has-text("Create Company")')
        if await create_btn.count() == 0:
            # フォールバック: 日本語ボタン名
            create_btn = self.page.locator(
                'button:has-text("企業を作成"), button:has-text("作成"), button:has-text("create")'
            )
        if await create_btn.count() == 0:
            raise RuntimeError("「Create Company」ボタンが見つかりません")
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

    async def _fill_company_form_field(
        self,
        value: str,
        aria_label_keywords: list,
        label_keywords: list,
        fallback_index: int,
        field_name: str,
    ) -> bool:
        """
        企業追加フォームの1フィールドに値を入力する。複数のセレクタ戦略を試す。

        戦略順:
            1. aria-label 部分一致 (現行UIで主に使用)
            2. label テキスト近傍 (Streamlit の <label>...<input>) — XPath
            3. stTextInput コンポーネントの順序 (fallback_index 番目)

        Returns:
            True なら入力成功、False なら全戦略で要素が見つからなかった
        """
        # 戦略1: aria-label 部分一致
        for kw in aria_label_keywords:
            try:
                loc = self.page.locator(f'input[aria-label*="{kw}"]')
                if await loc.count() > 0:
                    await loc.first.fill(value)
                    await self.page.wait_for_timeout(200)
                    logger.debug(f"  {field_name}入力 (aria-label='{kw}'): {value}")
                    return True
            except Exception:
                continue

        # 戦略2: label テキスト近傍 (XPath)
        for kw in label_keywords:
            try:
                xpath = (
                    f'xpath=//label[contains(., "{kw}")]'
                    '/following::input[1]'
                )
                loc = self.page.locator(xpath)
                if await loc.count() > 0:
                    await loc.first.fill(value)
                    await self.page.wait_for_timeout(200)
                    logger.debug(f"  {field_name}入力 (label='{kw}'近傍): {value}")
                    return True
            except Exception:
                continue

        # 戦略3: stTextInput の順序フォールバック
        try:
            inputs = self.page.locator('[data-testid="stTextInput"] input')
            count = await inputs.count()
            if count > fallback_index:
                await inputs.nth(fallback_index).fill(value)
                await self.page.wait_for_timeout(200)
                logger.debug(
                    f"  {field_name}入力 (stTextInput順 [{fallback_index}]): {value}"
                )
                return True
        except Exception:
            pass

        logger.warning(f"  {field_name}フィールドが見つかりません")
        return False

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

        最大 3 回までリトライし、選択操作直後にヘッダー切替が反映されない場合は
        サイドバーを開き直して再選択する。
        """
        logger.info(f"サイドバーから企業選択開始: {company.name}")

        MAX_SELECT_ATTEMPTS = 3
        last_err: Optional[Exception] = None
        for attempt in range(1, MAX_SELECT_ATTEMPTS + 1):
            try:
                await self._do_select_company_from_sidebar(company)
                # 反映猶予 (Streamlit rerun) を長めに取る
                await self.page.wait_for_timeout(2500)
                await self._wait_for_streamlit_load()

                # ヘッダー検証: 失敗時は次の試行へ
                try:
                    await self._verify_content_title(company, strict=True)
                except RuntimeError as ve:
                    if attempt < MAX_SELECT_ATTEMPTS:
                        logger.warning(
                            f"  ヘッダー検証失敗 (試行 {attempt}/{MAX_SELECT_ATTEMPTS}): {ve} → 再選択"
                        )
                        last_err = ve
                        # ページを少しスクロールして再描画を促す
                        try:
                            await self.page.evaluate("window.scrollTo(0, 0)")
                        except Exception:
                            pass
                        await self.page.wait_for_timeout(1500)
                        continue
                    raise

                logger.info(f"サイドバー企業選択完了: {company.name}")
                return
            except RuntimeError:
                raise
            except Exception as e:
                last_err = e
                if attempt < MAX_SELECT_ATTEMPTS:
                    logger.warning(
                        f"  サイドバー選択エラー (試行 {attempt}/{MAX_SELECT_ATTEMPTS}): {e} → 再試行"
                    )
                    await self.page.wait_for_timeout(1500)
                    continue
                raise

        if last_err:
            raise last_err

    async def _do_select_company_from_sidebar(self, company: CompanyInfo):
        """サイドバーから企業を1回選択する（リトライなし）。"""
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
                await self.page.wait_for_timeout(1200)

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

        # ヘッダー要素では見つからなかった場合 → ページ全体 + URL で再確認
        page_content = await self.page.content()
        try:
            current_url = self.page.url or ""
        except Exception:
            current_url = ""

        in_page = (
            (company.enterprise_id and company.enterprise_id in page_content)
            or (company.name and company.name in page_content)
        )
        in_url = bool(company.enterprise_id and company.enterprise_id in current_url)

        screenshot_path = f"screenshots/title_warn_{company.enterprise_id}.png"
        try:
            await self.page.screenshot(path=screenshot_path)
        except Exception:
            pass

        # ヘッダーに無くてもページ本文 or URL に企業情報があれば許容 (warn のみ)
        # 厳格モードでもこの分岐は通す: ヘッダーは描画タイミングで未反映でも
        # 実際には対象企業が選択されているケースが多々あるため。
        if in_page or in_url:
            logger.warning(
                "  [WARN] ヘッダー要素には特定できなかったが "
                f"{'URL' if in_url else 'ページ内'}に企業情報を確認 "
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

        検出順:
            1. ページ全体テキストに enterprise_id が出現していれば OK
            2. Streamlit の成功 alert (st.success / ✅) に enterprise_id が
               含まれていれば OK
            3. 配信URL (casual-interview-*.brainverse-ai.com/{id} で
               管理画面の既知ページ以外) のリストに enterprise_id があれば OK
            4. 上記いずれにもヒットしなければ RuntimeError

        Raises:
            RuntimeError: 追加された企業情報内に enterprise_id が見つからない場合
        """
        logger.info(
            f"  企業ID検証中: 期待ID={company.enterprise_id}, URL={company.homepage_url}"
        )

        if not company.enterprise_id:
            raise RuntimeError("企業IDが空です。検証を実行できません。")

        await self._wait_for_streamlit_load()
        # Streamlit の rerun + 成功通知が出るまでの猶予を長めに取る (旧: 1s)
        await self.page.wait_for_timeout(2500)

        # === 検出1: ページ全体テキスト ===
        page_text = await self.page.content()
        if company.enterprise_id in page_text:
            logger.info(f"  [OK] 企業ID '{company.enterprise_id}' をページ内に確認")
            return

        # === 検出2: 成功 alert / toast / st.success ===
        success_selectors = [
            "[data-testid='stAlert']",
            "[data-testid='stNotification']",
            "[data-baseweb='notification']",
            "[data-testid='stToast']",
            "div[role='alert']",
            "div[role='status']",
        ]
        success_keywords = [
            "created", "added", "登録", "作成", "successfully", "✅", "成功",
        ]
        for sel in success_selectors:
            try:
                elements = self.page.locator(sel)
                cnt = await elements.count()
                for i in range(cnt):
                    text = (await elements.nth(i).text_content()) or ""
                    text_l = text.lower()
                    if company.enterprise_id.lower() in text_l:
                        logger.info(
                            f"  [OK] 成功通知に企業ID '{company.enterprise_id}' を確認: "
                            f"'{text[:120]}'"
                        )
                        return
                    if any(k.lower() in text_l for k in success_keywords) and (
                        company.name in text or company.enterprise_id in text
                    ):
                        logger.info(
                            f"  [OK] 成功通知を検出 (ID/名前一致): '{text[:120]}'"
                        )
                        return
            except Exception:
                continue

        # === 検出3: 配信URL anchor の最終セグメント比較 ===
        # 管理画面のページ名 (settings / company_setup 等) はノイズとして除外する
        try:
            anchor_hrefs = await self.page.eval_on_selector_all(
                "a[href]", "elements => elements.map(el => el.href)"
            )
        except Exception:
            anchor_hrefs = []

        from urllib.parse import urlparse as _up
        MANAGEMENT_PAGE_SEGMENTS = {
            "settings", "candidate_interview", "dashboard",
            "content_manager", "content_generator", "chat_curator",
            "user_management", "company_setup", "login", "logout",
            "api", "docs",
        }
        actual_ids = []
        for href in anchor_hrefs:
            if "brainverse-ai.com" not in href and "casual-interview" not in href:
                continue
            try:
                p = _up(href)
            except Exception:
                continue
            last = p.path.rstrip("/").rsplit("/", 1)[-1]
            if not last or last in MANAGEMENT_PAGE_SEGMENTS:
                continue
            actual_ids.append(last)

        # 管理画面の既知ページを除外したリストに enterprise_id があれば OK
        if company.enterprise_id in actual_ids:
            logger.info(
                f"  [OK] 配信URL anchor に企業ID '{company.enterprise_id}' を確認"
            )
            return

        # === 検出4: 失敗 ===
        screenshot_path = f"screenshots/id_mismatch_{company.enterprise_id}.png"
        try:
            await self.page.screenshot(path=screenshot_path)
        except Exception:
            pass
        msg = (
            f"企業追加後のページ内に期待ID '{company.enterprise_id}' が見つかりません。"
            f" 検出された配信ID候補(管理ページ除外後): {actual_ids[:10]}, "
            f"スクショ: {screenshot_path}"
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

    async def generate_content(self, company: Optional[CompanyInfo] = None):
        """
        コンテンツを生成する。ボタン未検出時はリトライ + デバッグスクショを撮ってエラー。

        Args:
            company: 生成対象の企業 (生成後のFAQ実体検証で企業マッチングに使用)
        """
        logger.info("コンテンツ生成開始...")

        gen_button_selector = (
            'button[data-testid="stBaseButton-primary"]:has-text("コンテンツ生成"), '
            'button[data-testid="stBaseButton-primary"]:has-text("生成"), '
            'button:has-text("コンテンツ生成")'
        )

        # 「生成」タブをクリック + 「コンテンツ生成」ボタンが現れるまでリトライ
        # 過去ログから、URL処理が重い企業 (40件以上の参考資料を持つ等) では
        # 90秒 (3試行) では不十分で、120〜180秒待機が必要なケースあり。
        # YKT のように新規作成直後でサーバーキャッシュが冷えている企業も同様。
        MAX_GEN_ATTEMPTS = 6
        gen_button = None
        for attempt in range(MAX_GEN_ATTEMPTS):
            # 「生成」タブをクリック
            await self._click_generation_tab()

            # Streamlit描画待ち + 少し長めの待機
            # タブ切替が反映されない場合に備えて長めに取る (旧: 3000ms)
            await self.page.wait_for_timeout(7000)
            await self._wait_for_streamlit_load()

            # 「生成準備完了」メッセージを待機 (出なくても続行)
            # 初回は 40秒まで延長 (新規作成企業の URL 処理は重い)
            if attempt == 0:
                logger.info("  生成準備完了の待機中...")
                ready_msg = self.page.locator('div, p, span').filter(
                    has_text=re.compile(r"✅.*準備完了")
                )
                try:
                    await ready_msg.first.wait_for(state="visible", timeout=40000)
                    logger.info("  生成準備完了を確認しました")
                except Exception:
                    logger.warning(
                        "  生成準備完了メッセージが特定できませんでしたが、続行を試みます"
                    )

            # 「コンテンツ生成」ボタンが visible になるまで待機
            candidate_button = self.page.locator(gen_button_selector)
            try:
                await candidate_button.first.wait_for(state="visible", timeout=20000)
                if await candidate_button.count() > 0:
                    gen_button = candidate_button
                    logger.info(
                        f"  コンテンツ生成ボタン検出 (試行 {attempt+1}/{MAX_GEN_ATTEMPTS})"
                    )
                    break
            except Exception:
                logger.warning(
                    f"  コンテンツ生成ボタン未検出 (試行 {attempt+1}/{MAX_GEN_ATTEMPTS}) — 生成タブを再クリック"
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
                f"コンテンツ生成ボタンが見つかりませんでした ({MAX_GEN_ATTEMPTS}回試行)。"
                "「生成」タブのクリックが反映されていない可能性があります。"
            )

        await gen_button.first.click()

        # 生成完了を待機（最大5分）
        logger.info("コンテンツ生成中... (最大5分待機)")
        await self._wait_for_generation_complete()

        # === 生成実体検証: FAQ がページに現れたことを確認 ===
        # スピナー消失だけでは「生成完了」と誤判定する場合がある
        # (前回ページの残骸 / クリック直後にスピナーが現れる前のタイミング 等)。
        # 生成結果(FAQ項目+対象企業)が実際にDOM上に現れるまで明示的にポーリング。
        await self._verify_faq_generation(company)

        logger.info("コンテンツ生成完了")

    async def _verify_faq_generation(
        self,
        company: Optional[CompanyInfo] = None,
        max_wait_seconds: int = FAQ_VERIFY_TIMEOUT_SECONDS,
    ):
        """
        コンテンツ生成完了後に、FAQ実体がページに反映されたかを検証する。

        検出条件:
            - FAQ項目パターン (FAQ N / Q1: / 質問N / Q&A / よくある質問) が
              メイン領域に 2件以上存在
            - (company指定時) 対象企業の名前 or 企業IDも同時に存在

        改善 (2026-05-19):
            - 「FAQを生成中…」「企業情報を生成中…」が画面に残っている間は
              生成は継続中とみなして検証タイマーをリセットし、
              info ボックスが消えてから FAQ_VERIFY_TIMEOUT_SECONDS の純粋な
              検証ウィンドウを開始する。
              (旧実装は単純に時間経過のみで失敗判定し、生成完了前にタイムアウト
               する不安定さがあった)
            - 「生成中」が残ったままの最大追加待機 (= CONTENT_GENERATION_TIMEOUT)
              を超えたら、生成本体が固まったと判断して失敗を出す。

        失敗時は ContentSaveVerificationError を送出し、orchestrator が
        Step 4 から再試行できるようにする。
        """
        target_label = (
            f"{company.name} (id={company.enterprise_id})" if company else "(全体)"
        )
        logger.info(f"  [検証] 生成結果のFAQ実体を確認: {target_label}")

        faq_patterns = [
            re.compile(r"FAQ\s*\d+"),
            re.compile(r"Q\s*\d+\s*[:：.\)]"),
            re.compile(r"質問\s*\d+"),
            re.compile(r"^\s*Q\s*[:：]\s*", re.MULTILINE),
            re.compile(r"よくある質問"),
        ]
        in_progress_pattern = re.compile(
            r"(FAQ.{0,3}生成中|企業情報.{0,3}生成中|生成中\.{2,}|生成しています)"
        )

        poll_interval_ms = 2500
        max_ms = max_wait_seconds * 1000
        GEN_TAIL_MAX_MS = CONTENT_GENERATION_TIMEOUT
        verify_elapsed_ms = 0    # 「生成中」消失後の純粋な検証経過時間
        gen_tail_elapsed_ms = 0  # 「生成中」表示中の累計待機時間
        last_match_count = 0
        last_progress_log_ms = -15000

        # 修正 (2026-05-20):
        #   FAQ 実体は「生成」タブには現れず、「プレビュー・保存」タブに描画される。
        #   旧実装は max_ms 待ってからフォールバックでタブ切替していたため、
        #   毎回 max_ms (= 180s) を無駄にしていた。
        #   → 完了シグナル検出時点で即タブ切替する。
        preview_switched = False
        completion_signal_logged = False

        while True:
            try:
                main = self.page.locator(
                    "main, [data-testid='stMain'], section[role='main']"
                )
                if await main.count() > 0:
                    page_text = await main.first.inner_text(timeout=3000)
                else:
                    page_text = await self.page.locator("body").inner_text(timeout=3000)
            except Exception:
                page_text = ""

            # まだ「生成中…」が画面に残っている → 生成継続。検証タイマーリセット
            in_progress_still = bool(
                page_text and in_progress_pattern.search(page_text)
            )

            # オーバーライド (2026-05-20):
            #   st.status(state="complete") はラベル文字列を維持するため、
            #   画面に「FAQを生成中」等が残っても完了済みのケースがある。
            #   完了正シグナル ("✅ コンテンツが正常に生成されました" 等) が
            #   本文にあれば、in_progress 居残りを無視して検証に進む。
            completion_present = bool(
                page_text and GENERATION_COMPLETE_PATTERN.search(page_text)
            )
            if in_progress_still and completion_present:
                if not completion_signal_logged:
                    logger.info(
                        "  [検証] 完了シグナルを検出 — in_progress 居残りを無視"
                    )
                    completion_signal_logged = True
                in_progress_still = False

            # 完了シグナル検出 → 即「プレビュー・保存」タブに切替
            # (FAQ 実体は preview タブで描画されるため、待っても無駄)
            if completion_present and not preview_switched:
                try:
                    await self._click_tab_by_text("プレビュー", "保存")
                    await self.page.wait_for_timeout(2000)
                    await self._wait_for_streamlit_load()
                    preview_switched = True
                    logger.info(
                        "  [検証] 完了シグナル検出 → 「プレビュー・保存」タブに切替"
                    )
                    # 切替後のテキストを再取得
                    try:
                        if await main.count() > 0:
                            page_text = await main.first.inner_text(timeout=3000)
                        else:
                            page_text = await self.page.locator("body").inner_text(
                                timeout=3000
                            )
                    except Exception:
                        pass
                except Exception as e:
                    logger.debug(f"  プレビュータブ切替で例外 (続行): {e}")

            if in_progress_still:
                if gen_tail_elapsed_ms - last_progress_log_ms >= 15000:
                    logger.info(
                        f"  [検証] 「生成中…」表示が継続中 — 検証タイマーは"
                        f"開始しません (生成尾部待機 {gen_tail_elapsed_ms/1000:.0f}s)"
                    )
                    last_progress_log_ms = gen_tail_elapsed_ms
                if gen_tail_elapsed_ms >= GEN_TAIL_MAX_MS:
                    ts = company.enterprise_id if company else "unknown"
                    screenshot = f"screenshots/gen_verify_fail_{ts}.png"
                    try:
                        await self.page.screenshot(path=screenshot, full_page=True)
                    except Exception:
                        pass
                    raise ContentSaveVerificationError(
                        f"「生成中…」表示が {GEN_TAIL_MAX_MS/1000:.0f}秒経過しても消えません。"
                        f"生成本体が停止/異常終了の疑い。スクショ: {screenshot}",
                        reason_code="generation_stuck",
                        diagnostics={
                            "gen_tail_elapsed_seconds": gen_tail_elapsed_ms / 1000,
                            "screenshot": screenshot,
                        },
                    )
                await self.page.wait_for_timeout(poll_interval_ms)
                gen_tail_elapsed_ms += poll_interval_ms
                verify_elapsed_ms = 0  # 検証タイマーリセット
                last_match_count = 0
                continue

            # 生成中表示は消えた → FAQ 実体検証フェーズ
            match_count = sum(1 for p in faq_patterns if p.search(page_text))
            company_ok = True
            if company:
                company_ok = (
                    (company.enterprise_id and company.enterprise_id in page_text)
                    or (company.name and company.name in page_text)
                )

            if match_count >= 2 and company_ok:
                logger.info(
                    f"  [OK] FAQ実体を確認 (パターン一致: {match_count}件, "
                    f"検証経過: {verify_elapsed_ms/1000:.0f}s, "
                    f"生成尾部待機: {gen_tail_elapsed_ms/1000:.0f}s)"
                )
                return

            last_match_count = max(last_match_count, match_count)

            if verify_elapsed_ms >= max_ms:
                break

            await self.page.wait_for_timeout(poll_interval_ms)
            verify_elapsed_ms += poll_interval_ms

        # === 検証失敗 — 失敗原因の分類 ===
        ts = company.enterprise_id if company else "unknown"
        screenshot = f"screenshots/gen_verify_fail_{ts}.png"
        try:
            await self.page.screenshot(path=screenshot, full_page=True)
        except Exception:
            pass

        # 1) Streamlit エラー alert の有無を診断 (server-side 生成失敗の可能性)
        server_error_text = ""
        try:
            error_alerts = self.page.locator(
                "[data-testid='stAlert'], [data-baseweb='notification']"
            )
            alert_count = await error_alerts.count()
            for ai in range(alert_count):
                try:
                    txt = (await error_alerts.nth(ai).text_content()) or ""
                except Exception:
                    continue
                if any(
                    kw in txt for kw in ("エラー", "Error", "失敗", "Failed", "例外")
                ):
                    server_error_text = txt.strip()[:300]
                    break
        except Exception:
            pass

        # 2) 「プレビュー・保存」タブを開いて再検証 (タブ未切替が原因の可能性)
        #    現在「生成」タブのままだと FAQ プレビューが DOM に乗らないケースに対応
        try:
            await self._click_tab_by_text("プレビュー", "保存")
            await self.page.wait_for_timeout(3000)
            await self._wait_for_streamlit_load()
            try:
                main = self.page.locator(
                    "main, [data-testid='stMain'], section[role='main']"
                )
                if await main.count() > 0:
                    retry_text = await main.first.inner_text(timeout=3000)
                else:
                    retry_text = await self.page.locator("body").inner_text(
                        timeout=3000
                    )
            except Exception:
                retry_text = ""
            retry_match = sum(
                1 for p in faq_patterns if p.search(retry_text or "")
            )
            retry_company_ok = True
            if company:
                retry_company_ok = (
                    (company.enterprise_id and company.enterprise_id in retry_text)
                    or (company.name and company.name in retry_text)
                )
            if retry_match >= 2 and retry_company_ok:
                logger.info(
                    f"  [OK] 「プレビュー・保存」タブ切替後にFAQ実体を確認 "
                    f"(パターン一致: {retry_match}件)"
                )
                return
            # タブ切替後も見つからなければ最終失敗確定
            last_match_count = max(last_match_count, retry_match)
        except Exception as tab_err:
            logger.debug(f"  プレビュータブ切替再検証中の例外: {tab_err}")

        # 失敗カテゴリの推定
        if server_error_text:
            reason_code = "server_error"
            msg = (
                f"生成中にサーバー側エラーを検出: '{server_error_text}'. "
                f"スクショ: {screenshot}"
            )
        elif company and last_match_count >= 2:
            # FAQ パターンは見えたが対象企業のデータではない
            reason_code = "wrong_company"
            msg = (
                f"FAQ実体は検出 (パターン一致: {last_match_count}件) ですが、"
                f"対象企業 {company.name}({company.enterprise_id}) のデータが"
                f"含まれていません。混入の疑い。スクショ: {screenshot}"
            )
        else:
            reason_code = "no_faq_on_any_tab"
            msg = (
                f"生成完了検出後、FAQ実体がページに現れません "
                f"(パターン一致: {last_match_count}件、検証経過 {max_wait_seconds}s、"
                f"生成尾部待機 {gen_tail_elapsed_ms/1000:.0f}s)。"
                f"生成失敗 or 別企業のデータ残存疑い。スクショ: {screenshot}"
            )

        raise ContentSaveVerificationError(
            msg,
            reason_code=reason_code,
            diagnostics={
                "match_count": last_match_count,
                "verify_elapsed_seconds": max_wait_seconds,
                "gen_tail_elapsed_seconds": gen_tail_elapsed_ms / 1000,
                "server_error_text": server_error_text,
                "screenshot": screenshot,
                "company_name": company.name if company else None,
                "enterprise_id": company.enterprise_id if company else None,
            },
        )

    async def _click_tab_by_text(self, *keywords: str):
        """指定キーワードのいずれかを含むタブをクリックする (visible なもののみ)。

        Args:
            *keywords: タブのテキストに含まれるキーワード (OR 条件)

        Returns:
            True : クリックに成功
            False: 該当タブが見つからない or クリック失敗
        """
        # まずは id 属性パターンマッチ (Streamlit のタブは tab-N の id を持つ)
        for keyword in keywords:
            try:
                sel = f'[id*="tab-"]:has-text("{keyword}")'
                tab = self.page.locator(sel).first
                if await tab.count() > 0 and await tab.is_visible():
                    await tab.click(timeout=5000)
                    await self.page.wait_for_timeout(800)
                    return True
            except Exception:
                continue

        # フォールバック: data-baseweb='tab' を走査
        try:
            tabs = self.page.locator("[data-baseweb='tab'], [role='tab']")
            tab_count = await tabs.count()
            for i in range(tab_count):
                try:
                    text = (await tabs.nth(i).text_content()) or ""
                    if not text.strip():
                        continue
                    if any(kw in text for kw in keywords):
                        if not await tabs.nth(i).is_visible():
                            continue
                        await tabs.nth(i).click(timeout=5000)
                        await self.page.wait_for_timeout(800)
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    async def _find_action_button(
        self,
        require_all: list,
        exclude_any: Optional[list] = None,
    ):
        """
        ページ内の <button> を全走査し、テキストが
            - require_all 全てを含む (AND条件)
            - exclude_any のいずれも含まない (OR除外)
        最初の可視ボタン Locator を返す。見つからなければ None。

        Playwright の `locator(...).first` は CSS セレクタ記載順ではなく
        DOM 順で最初のマッチを返すため、「保存」のような頻出語を含む CSS
        セレクタ + `.first` だと意図しないタブボタン等を掴むことがある。
        この関数はテキスト一致を正確に判定するためのフォールバック手段。
        """
        exclude_any = exclude_any or []
        try:
            all_buttons = self.page.locator("button")
            btn_count = await all_buttons.count()
        except Exception:
            return None

        for i in range(btn_count):
            try:
                text = (await all_buttons.nth(i).text_content()) or ""
            except Exception:
                continue
            t = text.strip()
            if not t:
                continue
            if not all(kw in t for kw in require_all):
                continue
            if any(kw in t for kw in exclude_any):
                continue
            try:
                if not await all_buttons.nth(i).is_visible():
                    continue
            except Exception:
                pass
            return all_buttons.nth(i)
        return None

    async def _wait_for_in_progress_to_clear(
        self,
        max_wait_ms: int = 300000,
        source: str = "unknown",
    ):
        """
        画面に「FAQを生成中…」「企業情報を生成中…」「生成中..」等が表示されて
        いる間、最大 max_wait_ms まで消失をポーリングする。

        Streamlit の生成処理は spinner ではなく st.info()/st.empty() で
        生成中表示を出すため、これらが消えるまでが本当の生成完了。
        save_content() の冒頭などに念のための最終ガードとして使う。

        Args:
            max_wait_ms: 最大待機ミリ秒
            source:      ログ識別子
        """
        in_progress_pattern = re.compile(
            r"(FAQ.{0,3}生成中|企業情報.{0,3}生成中|生成中\.{2,}|生成しています)"
        )
        poll_interval_ms = 2500
        elapsed_ms = 0
        last_log_ms = -15000

        while elapsed_ms < max_wait_ms:
            try:
                main = self.page.locator(
                    "main, [data-testid='stMain'], section[role='main']"
                )
                if await main.count() > 0:
                    page_text = await main.first.inner_text(timeout=3000)
                else:
                    page_text = await self.page.locator("body").inner_text(
                        timeout=3000
                    )
            except Exception:
                page_text = ""

            still_running = bool(
                page_text and in_progress_pattern.search(page_text)
            )

            # オーバーライド (2026-05-20):
            #   完了正シグナルが本文にあれば、in_progress 居残りを無視。
            #   st.status(state="complete") のラベル残存で 300秒待ちにならないように。
            if still_running and page_text and GENERATION_COMPLETE_PATTERN.search(page_text):
                logger.info(
                    f"  [{source}] 完了シグナルを検出 — 居残りを無視して継続"
                )
                still_running = False

            if not still_running:
                if elapsed_ms > 0:
                    logger.info(
                        f"  [{source}] 「生成中…」表示は消えました "
                        f"(待機 {elapsed_ms/1000:.0f}s)"
                    )
                return

            if elapsed_ms - last_log_ms >= 15000:
                logger.info(
                    f"  [{source}] 「生成中…」継続中 → 待機 "
                    f"({elapsed_ms/1000:.0f}s / 上限 {max_wait_ms/1000:.0f}s)"
                )
                last_log_ms = elapsed_ms

            await self.page.wait_for_timeout(poll_interval_ms)
            elapsed_ms += poll_interval_ms

        # 上限到達 — 警告のみ (呼び出し元に判断を委ねる)
        logger.warning(
            f"  [{source}] 「生成中…」表示が {max_wait_ms/1000:.0f}秒経過しても"
            "消えませんでした (後続処理は失敗する可能性あり)"
        )

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

        改善 (2026-05-19):
            - 保存タブに遷移する前に「FAQを生成中…」「企業情報を生成中…」が
              残っていないか念のため確認。残っていれば最大 5分まで消失を待つ。
              ボタン表示前の generation-tail で誤クリックする事故を防ぐ。
        """
        logger.info("コンテンツ保存開始...")

        # === 念のため最終生成中ガード ===
        try:
            await self._wait_for_in_progress_to_clear(
                max_wait_ms=CONTENT_GENERATION_TIMEOUT,
                source="save_content",
            )
        except Exception as e:
            logger.warning(f"  生成中ガード待機中の例外 (続行): {e}")

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
        # 重要 (2026-05-13): `.first` は CSS セレクタ記載順ではなく DOM 順で
        # 最初のマッチを返すため、「保存」のような頻出語を含む CSS セレクタ +
        # `.first` だと上部の「👁️ プレビュー・保存」タブを掴むことがある。
        # → _find_action_button で「FAQ」+「保存」両方含み「プレビュー」を含まない
        #   ボタンに厳密に限定する。
        logger.info("  FAQ保存ボタンの表示を確認中...")

        # 表示出現待ち (ボタンが現れていなければ最大20秒待つ)
        faq_visible_locator = self.page.locator(
            'button:has-text("FAQ保存（置換）"), '
            'button:has-text("FAQ保存")'
        ).first
        try:
            await faq_visible_locator.wait_for(state="visible", timeout=20000)
            logger.info("  FAQ保存ボタンの表示を確認（=FAQプレビュー描画完了）")
        except Exception:
            screenshot_err = (
                f"screenshots/faq_not_found_{company.enterprise_id}.png"
            )
            try:
                await self.page.screenshot(path=screenshot_err)
            except Exception:
                pass
            raise ContentSaveVerificationError(
                f"FAQ保存ボタンが表示されません (FAQプレビュー未生成 or "
                f"対象企業のページ未表示)。スクショ: {screenshot_err}",
                reason_code="save_button_missing",
                diagnostics={
                    "stage": "wait_visible",
                    "enterprise_id": company.enterprise_id,
                    "screenshot": screenshot_err,
                },
            )

        # 厳密にテキスト一致するボタンを取得 (タブの「プレビュー・保存」を除外)
        first_save_btn = await self._find_action_button(
            require_all=["FAQ", "保存"],
            exclude_any=["プレビュー"],
        )
        if first_save_btn is None:
            screenshot_err = (
                f"screenshots/faq_save_btn_not_found_{company.enterprise_id}.png"
            )
            try:
                await self.page.screenshot(path=screenshot_err)
            except Exception:
                pass
            raise ContentSaveVerificationError(
                f"FAQ保存ボタンが特定できません (見えるボタンの中に「FAQ」+「保存」"
                f"を含むものなし)。スクショ: {screenshot_err}",
                reason_code="save_button_missing",
                diagnostics={
                    "stage": "find_action_button",
                    "enterprise_id": company.enterprise_id,
                    "screenshot": screenshot_err,
                },
            )

        # === 確認直後、間を空けずに即クリック ===
        faq_btn_text = (await first_save_btn.text_content() or "").strip()
        logger.info(f"  STEP1: FAQ保存ボタンをクリック: [{faq_btn_text}]")
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
                retry_btn = await self._find_action_button(
                    require_all=["FAQ", "保存"],
                    exclude_any=["プレビュー"],
                )
                if retry_btn is not None:
                    await retry_btn.scroll_into_view_if_needed()
                    await retry_btn.click()
                    logger.info("  FAQ保存ボタンを再クリックしました")
            except Exception as e:
                logger.warning(f"  FAQ保存ボタンの再クリックに失敗: {e}")

        await self.page.wait_for_timeout(2000)
        await self._wait_for_streamlit_load()

        # === STEP 2: ページを下にスクロールして「企業情報保存」ボタンを探す ===
        # 「企業情報保存」「企業情報を保存」どちらの表記でも対応するため、
        # _find_action_button で「企業情報」+「保存」両方含むものに限定。
        # 「プレビュー」「FAQ」を含むものは除外 (タブ/FAQボタンとの混同回避)。
        logger.info("  STEP2: 企業情報保存ボタンを探してクリック...")

        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self.page.wait_for_timeout(1500)

        red_save_target = await self._find_action_button(
            require_all=["企業情報", "保存"],
            exclude_any=["プレビュー", "FAQ"],
        )

        if red_save_target is None:
            # 失敗時、デバッグ用に保存関連ボタン一覧をログ出力
            logger.warning(
                "  「企業情報保存」ボタンが見つかりません。ページ内ボタン一覧を出力します..."
            )
            try:
                all_buttons = self.page.locator("button")
                btn_count = await all_buttons.count()
                for i in range(btn_count):
                    btn_text = await all_buttons.nth(i).text_content()
                    if btn_text and "保存" in btn_text:
                        logger.warning(f"    保存関連ボタン発見: [{btn_text}]")
            except Exception:
                pass
            screenshot_path = (
                f"screenshots/save_btn_not_found_{company.enterprise_id}.png"
            )
            try:
                await self.page.screenshot(path=screenshot_path)
            except Exception:
                pass
            raise ContentSaveVerificationError(
                f"「企業情報保存」ボタンが見つかりませんでした。"
                f"スクリーンショット: {screenshot_path}",
                reason_code="save_button_missing",
                diagnostics={
                    "stage": "company_info_save_button",
                    "enterprise_id": company.enterprise_id,
                    "screenshot": screenshot_path,
                },
            )

        btn_text = (await red_save_target.text_content() or "").strip()
        logger.info(f"  企業情報保存ボタンをクリック: [{btn_text}]")

        await red_save_target.scroll_into_view_if_needed()
        await self.page.wait_for_timeout(500)
        await red_save_target.click()

        await self.page.wait_for_timeout(3000)
        await self._wait_for_streamlit_load()
        logger.info("  企業情報保存ボタンのクリック完了")

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
        対象企業のコンテンツが正しく保存されているかを厳格検証する。

        検証項目:
        1. コンテンツ管理ページに対象企業が選択できること
        2. FAQタブ内に企業名 or 企業IDの出現 + FAQ項目の実体存在
        3. 企業情報タブ内に企業名 or 企業IDの出現 + 本文の実体存在

        失敗時は ContentSaveVerificationError を送出し、orchestrator が
        Step 4 (コンテンツ生成) から再試行できるようにする。

        Raises:
            ContentSaveVerificationError:
                - 未保存検出 (FAQ/企業情報タブが空)
                - 対象企業以外のデータの混入検出
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
                f"{company.name}) の情報が見つかりません (未保存疑い)。"
                f"スクショ: {screenshot_path}"
            )
            logger.error(f"  [ERR] {msg}")
            raise ContentSaveVerificationError(
                msg,
                reason_code="content_mgmt_missing",
                diagnostics={
                    "stage": "content_management_page",
                    "enterprise_id": company.enterprise_id,
                    "company_name": company.name,
                    "screenshot": screenshot_path,
                },
            )

        logger.info(
            f"  [OK] コンテンツ管理ページ: {company.enterprise_id} の存在を確認"
        )

        # FAQタブ・企業情報タブをそれぞれ開いて検証
        faq_ok, faq_reason = await self._verify_tab_content(
            company, tab_keywords=["FAQ", "よくある質問", "Q&A"], tab_label="FAQ"
        )
        info_ok, info_reason = await self._verify_tab_content(
            company,
            tab_keywords=["企業情報", "会社情報", "企業詳細", "プロフィール"],
            tab_label="企業情報",
        )

        if not (faq_ok and info_ok):
            msg = (
                f"FAQ/企業情報タブのコンテンツ検証に失敗 "
                f"(faq_ok={faq_ok}: {faq_reason} / "
                f"info_ok={info_ok}: {info_reason}, "
                f"id={company.enterprise_id}, スクショ: {screenshot_path})"
            )
            logger.error(f"  [ERR] {msg}")
            raise ContentSaveVerificationError(
                msg,
                reason_code="content_mgmt_tab_failed",
                diagnostics={
                    "stage": "content_management_tabs",
                    "faq_ok": faq_ok,
                    "faq_reason": faq_reason,
                    "info_ok": info_ok,
                    "info_reason": info_reason,
                    "enterprise_id": company.enterprise_id,
                    "screenshot": screenshot_path,
                },
            )

        logger.info("  [OK] FAQ・企業情報タブともに対象企業のコンテンツを確認")

    async def _verify_tab_content(
        self,
        company: CompanyInfo,
        tab_keywords: list,
        tab_label: str,
    ) -> tuple:
        """
        指定キーワードを持つタブを開き、そのタブパネル内に対象企業の
        コンテンツが実体として存在するかを検証する。

        厳格化:
            - 対象企業の名前/IDが panel 内に出現
            - panel 内に「実体のあるコンテンツ」(FAQ項目や企業情報本文) が存在
              → 空ヘッダーだけの未保存状態を検出

        Returns:
            (ok: bool, reason: str)
              ok=False の場合、reason に未保存/混入などの理由を入れる
        """
        logger.info(f"  [{tab_label}タブ] 検証開始")

        clicked = False

        # 経路1: タブ (旧 UI 用 / 互換)
        tab_locators = self.page.locator(
            "[data-baseweb='tab'], [role='tab'], button[role='tab']"
        )
        tab_count = await tab_locators.count()

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

        # 経路2: ラジオボタン (現行 Streamlit UI のコンテンツ管理ページは
        #  「📁 FAQ管理 / 📁 企業情報 / 📊 コンテンツ分析 / 🔖 バージョン履歴」
        #  のラジオでセクション切替する)
        if not clicked:
            for kw in tab_keywords:
                try:
                    radio_option = self.page.locator(
                        "[data-testid='stRadio'] label"
                    ).filter(has_text=kw)
                    if await radio_option.count() == 0:
                        continue
                    await radio_option.first.click()
                    await self.page.wait_for_timeout(1500)
                    await self._wait_for_streamlit_load()
                    logger.info(
                        f"  [{tab_label}タブ] ラジオボタンで選択: 「{kw}」"
                    )
                    clicked = True
                    break
                except Exception:
                    continue

        if not clicked:
            logger.warning(
                f"  [{tab_label}タブ] が見つかりません（タブ/ラジオ未表示の可能性）"
            )
            return False, "タブ/ラジオが見つからない"

        # 修正 (2026-05-20):
        #   ラジオ UI のセクション (例: 企業情報) は内部に Edit/Preview タブを
        #   持ち、Edit モードの本文は <textarea> の value に入るため
        #   inner_text では取得できない上、Streamlit の再描画とのレースで
        #   inner_text が一時的に空になることがある。
        #   → get_by_text() で DOM 内テキストを直接照合し、最大 5秒まで
        #     リトライする。それでも見つからない場合のみ未保存と判定。
        screenshot_path = (
            f"screenshots/tab_{tab_label}_{company.enterprise_id}.png"
        )

        async def _has_company_text() -> bool:
            scope = self.page.locator(
                "main, [data-testid='stMain'], section[role='main']"
            )
            if await scope.count() == 0:
                scope = self.page.locator("body")
            for needle in (company.enterprise_id, company.name):
                if not needle:
                    continue
                try:
                    if await scope.first.get_by_text(needle, exact=False).count() > 0:
                        return True
                except Exception:
                    pass
                # textarea の value (Edit モード markdown 等) も検証
                try:
                    textareas = scope.first.locator("textarea")
                    ta_count = await textareas.count()
                    for ti in range(ta_count):
                        try:
                            val = await textareas.nth(ti).input_value(timeout=1000)
                        except Exception:
                            continue
                        if val and needle in val:
                            return True
                except Exception:
                    pass
            return False

        in_company = False
        retry_max = 5
        for retry_i in range(retry_max):
            if await _has_company_text():
                in_company = True
                break
            await self.page.wait_for_timeout(1000)

        await self.page.screenshot(path=screenshot_path)

        if not in_company:
            logger.warning(
                f"  [WARN] [{tab_label}タブ] 企業名/IDを確認できませんでした "
                f"({retry_max}回リトライ後、スクショ: {screenshot_path})"
            )
            return False, "対象企業の名前/IDがタブ本文に見つからない"

        # 実体コンテンツ検証用に main 全体 + textarea を合算
        try:
            main = self.page.locator(
                "main, [data-testid='stMain'], section[role='main']"
            )
            main_text = (
                await main.first.inner_text(timeout=3000)
                if await main.count() > 0 else ""
            )
        except Exception:
            main_text = ""
        textarea_values = []
        try:
            textareas = self.page.locator(
                "main textarea, [data-testid='stMain'] textarea"
            )
            ta_count = await textareas.count()
            for ti in range(ta_count):
                try:
                    val = await textareas.nth(ti).input_value(timeout=1500)
                except Exception:
                    continue
                if val:
                    textarea_values.append(val)
        except Exception:
            pass
        combined_text = "\n".join(t for t in (main_text, *textarea_values) if t)

        # 2. タブパネル内にコンテンツの実体があるか (未保存検出)
        if not self._tab_has_substantive_content(combined_text, tab_label):
            logger.warning(
                f"  [WARN] [{tab_label}タブ] 本文の実体コンテンツが確認できません "
                f"(未保存疑い、スクショ: {screenshot_path})"
            )
            return False, "タブ本文に実体コンテンツがない (未保存疑い)"

        logger.info(
            f"  [OK] [{tab_label}タブ] 企業名/ID + 実体コンテンツを確認 "
            f"(スクショ: {screenshot_path})"
        )
        return True, ""

    async def _get_active_tab_panel_text(self) -> str:
        """
        アクティブなタブパネル (role='tabpanel') のテキストを取得する。
        取れない場合はメインエリア → body にフォールバック。
        """
        try:
            panel = self.page.locator(
                "[role='tabpanel']:visible, [data-baseweb='tab-panel']:visible"
            )
            if await panel.count() > 0:
                txt = await panel.first.inner_text(timeout=3000)
                if txt and txt.strip():
                    return txt
        except Exception:
            pass

        try:
            main = self.page.locator(
                "main, [data-testid='stMain'], section[role='main']"
            )
            if await main.count() > 0:
                return await main.first.inner_text(timeout=3000)
        except Exception:
            pass

        try:
            return await self.page.locator("body").inner_text(timeout=3000)
        except Exception:
            return ""

    def _tab_has_substantive_content(self, text: str, tab_label: str) -> bool:
        """
        タブパネル本文に「実体のあるコンテンツ」が存在するかを判定。
        未保存の状態だとFAQ/企業情報が空のヘッダーのみになるのを検出する。
        """
        if not text:
            return False
        compact = re.sub(r"\s+", "", text)
        if len(compact) < 30:
            return False

        if tab_label == "FAQ":
            faq_patterns = [
                r"FAQ\s*\d+",
                r"Q\s*\d+",
                r"質問\s*\d+",
                r"^\s*Q\s*[:：]",
                r"^\s*A\s*[:：]",
            ]
            for p in faq_patterns:
                if re.search(p, text, re.MULTILINE):
                    return True
            return len(compact) >= 100

        # 企業情報タブ: 一定量のテキストがあれば実体ありとみなす
        return len(compact) >= 50

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
        """
        コンテンツ生成の完了を待機する（ポーリング方式）。

        改善 (2026-05-13):
            - クリック直後にスピナーが現れる前から「完了」判定する誤検知を防止
            - 最低待機時間 (MIN_WAIT_MS) を確保
            - スピナー出現を 15秒まで待ってから消失検出フェーズに入る

        改善 (2026-05-19):
            - **スピナー消失だけでは生成完了とみなさない**。
              Streamlit の生成処理は st.info() の青いボックスで
              「FAQを生成中...」「企業情報を生成中...」を表示し、
              spinner は早期に消えるが info ボックスは生成中の間ずっと残る。
              → spinner 不可視 AND 「生成中」info ボックス不存在 になって
                 初めて完了とみなす。
            - 完了の正シグナルとして「✅ … 生成完了」「保存可能」等の
              成功メッセージが出現したら即時完了に切り上げる。
        """
        MIN_WAIT_MS = 15000           # 最低この時間は完了判定しない
        SPINNER_APPEAR_TIMEOUT = 15000  # スピナー出現待ち
        poll_interval = 3000
        elapsed = 0
        spinner_selector = "[data-testid='stSpinner'], .stSpinner"

        # 「生成中...」表示を判定するためのテキストパターン
        # Streamlit の st.info() / st.status() / 一般 div いずれにも対応
        #
        # 注意 (2026-05-20):
        #   旧パターンに含めていた `⏳` 絵文字 / `スピナー` という単語は、
        #   画面のヘルプテキスト・ステータス凡例等に静的に存在する場合があり、
        #   生成完了後も永続マッチして 5分タイムアウトする不具合があった。
        #   保守的に `_wait_for_in_progress_to_clear` と同じ語句のみに限定する。
        in_progress_pattern = re.compile(
            r"(FAQ.{0,3}生成中|企業情報.{0,3}生成中|生成中\.{2,}|"
            r"生成しています|処理中\.{2,})"
        )
        # 完了シグナル (これが出たら即完了)
        # 修正 (2026-05-20):
        #   実際の画面に表示される成功メッセージを直接拾うように拡充。
        #   - 「✅ コンテンツが正常に生成されました」
        #   - 「✅ 生成されたFAQ」「N個のFAQを生成しました」
        #   - 「✅ 生成された企業情報」「企業情報が正常に生成されました」
        #   旧パターン (生成完了 / 保存可能 等) はこの画面には出ないため
        #   完了検出が永続マッチに頼って 300秒タイムアウトしていた。
        completion_pattern = GENERATION_COMPLETE_PATTERN

        # フェーズ1: スピナー出現を待つ (出なくても最低待機後に消失検出へ移行)
        spinner_seen = False
        try:
            await self.page.locator(spinner_selector).first.wait_for(
                state="visible", timeout=SPINNER_APPEAR_TIMEOUT
            )
            spinner_seen = True
            logger.debug("  スピナー出現を確認 → 消失待ちへ")
        except Exception:
            logger.debug(
                "  スピナー出現せず → 最低待機時間後に完了判定"
            )

        # フェーズ2: スピナー消失 + 「生成中」テキスト消失をポーリング
        last_in_progress_logged_at = -10000  # 過剰ログ抑制用
        while elapsed < CONTENT_GENERATION_TIMEOUT:
            await self.page.wait_for_timeout(poll_interval)
            elapsed += poll_interval

            # ----- スピナー可視性 -----
            spinner_visible = False
            try:
                spinner = self.page.locator(spinner_selector)
                spinner_visible = await spinner.is_visible()
            except Exception:
                pass

            # ----- 生成中テキスト可視性 -----
            #
            # 修正 (2026-05-20):
            #   main 要素全体 (inner_text) を見ると、生成完了後もページ内に
            #   残った「FAQを生成中…」「企業情報を生成中…」等のステータス
            #   履歴やタブ見出しに永続マッチして 300秒タイムアウトする不具合
            #   があった。
            #   → st.info / st.status / stAlert / stToast 等、
            #     「現在進行中であることを示す UI 要素」だけを対象に判定する。
            in_progress_visible = False
            in_progress_match = None
            page_text = ""  # 診断ログ用 (マッチ要素のテキストを格納)
            status_selectors = (
                "[data-testid='stAlert']",          # st.info / st.warning など
                "[data-testid='stStatusWidget']",   # st.status の Running 状態
                "[data-testid='stStatus']",          # 旧 API
                "[data-testid='stToast']",           # st.toast
                "[data-testid='stNotification']",
            )
            try:
                status_locator = self.page.locator(", ".join(status_selectors))
                status_count = await status_locator.count()
                for i in range(status_count):
                    el = status_locator.nth(i)
                    try:
                        if not await el.is_visible():
                            continue
                        text = await el.inner_text(timeout=1500)
                    except Exception:
                        continue
                    if not text:
                        continue
                    m = in_progress_pattern.search(text)
                    if m:
                        in_progress_visible = True
                        in_progress_match = m
                        page_text = text
                        break
            except Exception:
                pass

            # 完了シグナル検出用には main 全体のテキストを別途取得する
            # (こちらは「居残り」で困らないため main 全体で OK)
            try:
                main = self.page.locator(
                    "main, [data-testid='stMain'], section[role='main']"
                )
                if await main.count() > 0:
                    full_main_text = await main.first.inner_text(timeout=3000)
                else:
                    full_main_text = await self.page.locator("body").inner_text(
                        timeout=3000
                    )
            except Exception:
                full_main_text = ""

            # ----- エラー検出 -----
            try:
                error = self.page.locator("[data-testid='stAlert']")
                if await error.count() > 0:
                    error_text = await error.first.text_content()
                    if (
                        "エラー" in (error_text or "")
                        or "Error" in (error_text or "")
                    ) and "生成中" not in (error_text or ""):
                        raise RuntimeError(
                            f"コンテンツ生成エラー: {error_text}"
                        )
            except RuntimeError:
                raise
            except Exception:
                pass

            # ----- 完了正シグナル: 最低待機時間後なら即完了に切り上げ -----
            completion_signal = False
            if full_main_text and completion_pattern.search(full_main_text):
                completion_signal = True

            # ----- 完了判定 -----
            # 条件: スピナー不可視 AND 「生成中」テキスト不可視 AND 最低待機時間経過
            # OR: 明示的な完了シグナル + 最低待機時間経過
            if (
                elapsed >= MIN_WAIT_MS
                and (
                    (not spinner_visible and not in_progress_visible)
                    or completion_signal
                )
            ):
                logger.info(
                    f"生成完了検出 (経過: {elapsed / 1000}秒, "
                    f"スピナー出現: {spinner_seen}, "
                    f"完了シグナル: {completion_signal})"
                )
                return

            # 進捗ログ (10秒ごと、過剰ログ抑制)
            if elapsed - last_in_progress_logged_at >= 10000:
                match_info = ""
                # spinner が消えたのに in_progress_text が居残っている場合は
                # 何が拾われているか診断ログを出す (永続マッチ不具合の調査用)。
                if not spinner_visible and in_progress_match:
                    s_pos = max(0, in_progress_match.start() - 40)
                    e_pos = min(len(page_text), in_progress_match.end() + 40)
                    snippet = page_text[s_pos:e_pos].replace("\n", " ⏎ ")
                    match_info = (
                        f", matched='{in_progress_match.group(0)}'"
                        f", context='…{snippet}…'"
                    )
                logger.info(
                    f"  生成中... (経過: {elapsed / 1000:.0f}秒, "
                    f"spinner={spinner_visible}, "
                    f"in_progress_text={in_progress_visible}"
                    f"{match_info})"
                )
                last_in_progress_logged_at = elapsed

        raise TimeoutError(
            f"コンテンツ生成がタイムアウト ({CONTENT_GENERATION_TIMEOUT / 1000}秒)"
        )
