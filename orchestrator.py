"""
DOCdemo 自動化フロー — メインオーケストレーター

全自動化フローの統合・進捗管理・エラーハンドリングを担当する。
企業リストの読み込みから、企業追加、コンテンツ生成、画像アップロード、
リンク取得、スプレッドシート書き戻しまでの全工程を制御する。

変更履歴:
- 2026-04-22: 1件ごとに再ログイン＆キャッシュクリアを追加
- 2026-04-22: 企業IDをURLから抽出するように変更
- 2026-04-22: 画像取得をHP実際の画像に変更（スクショ非推奨）
- 2026-04-22: 企業選択をサイドバーから行うよう変更
- 2026-05-11: 自動化フロー内に4つの検証ポイントを追加
    - Step 1.5: URL企業ID不一致検証（候補URLから抽出した企業IDが
      完全一致しない種類が複数あれば手動確認待ちで中断）
    - Step 2.5: 企業追加直後のID検証（URLベースのIDが正しく反映されているか）
    - Step 4-pre: コンテンツ生成画面のヘッダー切替検証（厳格化）
    - Step 4-post: 保存後にFAQ・企業情報両タブの内容検証
- 2026-05-11: 企業追加完了後（Step 2.5 後）に、ページを閉じて再ログインする
  処理を追加。ページ内キャッシュによる「別企業のコンテンツ生成」「サイドバー
  選択が反映されない」等の不具合を防止。
"""

import asyncio
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from config import (
    WEB_APP_BASE_URL,
    BROWSER_HEADLESS,
    BROWSER_VIEWPORT,
    BROWSER_SLOW_MO,
    LOGS_DIR,
    LOG_FORMAT,
    LOG_LEVEL,
    LOG_FILE,
    RETRY_COUNT,
    RETRY_DELAY,
    FAQ_SAVE_MAX_RETRIES,
    SERVER_RECOVERY_MAX_WAIT_MINUTES,
)
from models import CompanyInfo, ProcessStatus
from spreadsheet_manager import SpreadsheetManager
from url_finder import URLFinder
from image_fetcher import extract_enterprise_id_from_url
from recruit_url_finder import find_recruit_site_urls
from web_app_operator import (
    WebAppOperator,
    ContentSaveVerificationError,
    ServerDownError,
    check_server_alive,
    is_server_down_error,
    wait_for_server_recovery,
)

logger = logging.getLogger(__name__)


def setup_logging():
    """ロギングの初期化"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Windows cp932対策: stdout をUTF-8に設定
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace"
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace"
        )

    # ルートロガー設定
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    # コンソールハンドラ
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root_logger.addHandler(console_handler)

    # ファイルハンドラ
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root_logger.addHandler(file_handler)


class Orchestrator:
    """
    全自動化フローを制御するオーケストレーター。

    責務:
    - 企業リストの読み込みと進捗管理
    - 各モジュールの呼び出しと結果の受け渡し
    - エラーハンドリングとリトライ
    - 処理済み企業のスキップ（レジューム機能）
    - 1件完了ごとに再ログイン＆キャッシュクリア
    """

    def __init__(
        self,
        csv_path: Path = None,
        headless: bool = None,
        test_mode: bool = False,
        target_company: str = None,
    ):
        """
        Args:
            csv_path: 企業リストCSVのパス
            headless: ヘッドレスモードで実行するか
            test_mode: テストモード（1社のみ処理）
            target_company: テストモード時の対象企業名
        """
        self.sheet_manager = SpreadsheetManager(csv_path)
        self.headless = headless if headless is not None else BROWSER_HEADLESS
        self.test_mode = test_mode
        self.target_company = target_company

        # 統計情報
        self.stats = {
            "total": 0,
            "processed": 0,
            "success": 0,
            "skipped": 0,
            "duplicate": 0,  # URL企業ID不一致候補複数のため手動確認待ち
            "error": 0,
            "server_down_aborted": 0,  # サーバーダウンで残処理を中断した社数
        }

    async def run(self):
        """
        メインエントリーポイント (Stage 2: デモ作成自動化)。

        前提: Stage 1 (`python select_urls.py`) でホームページURLが確定済みの
              CSVを入力とする。URL未入力の行は本ステージでは自動スキップ。
        """
        start_time = datetime.now()
        logger.info("=" * 60)
        logger.info("DOCdemo 自動化フロー 開始 (Stage 2: デモ作成)")
        logger.info(f"開始時刻: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)

        # 企業リスト読み込み
        companies = self.sheet_manager.read_company_list()
        # Stage 2 はURL確定済の行だけ処理する (URL未入力行は select_urls.py 側で扱う)
        pending = self.sheet_manager.get_pending_companies(
            companies, require_url=True
        )

        if self.test_mode and self.target_company:
            pending = [c for c in pending if self.target_company in c.name]
            if not pending:
                logger.error(f"テスト対象企業が見つかりません: {self.target_company}")
                return
            pending = [pending[0]]  # 1社のみ

        self.stats["total"] = len(pending)
        logger.info(f"処理対象: {len(pending)}社")

        # ブラウザ起動
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                slow_mo=BROWSER_SLOW_MO,
            )
            context = await browser.new_context(viewport=BROWSER_VIEWPORT)

            # URLファインダー用ページ
            url_finder_page = await context.new_page()
            url_finder = URLFinder(page=url_finder_page)

            # Webアプリ操作用ページ
            web_app_page = await context.new_page()
            web_operator = WebAppOperator(page=web_app_page)

            # ログイン
            await web_operator.login()

            # 各企業を順次処理
            # サーバーダウン復旧後の再試行を扱うため idx ベースの while ループを使う
            idx = 0
            server_down_aborted = False
            while idx < len(pending):
                company = pending[idx]
                idx += 1
                logger.info("")
                logger.info(f"{'─' * 50}")
                logger.info(
                    f"[{idx}/{len(pending)}] {company.name} "
                    f"(ステータス: {company.status.value})"
                )
                logger.info(f"{'─' * 50}")

                try:
                    await self._process_single_company(
                        company, companies, url_finder, web_operator
                    )
                    # 成功カウントは COMPLETED に達した場合のみ
                    if company.status == ProcessStatus.COMPLETED:
                        self.stats["success"] += 1
                except ServerDownError as sd_err:
                    # === サーバーダウン検出 → 復旧待ち & 同企業を再試行 ===
                    recovered = await self._handle_server_down(
                        sd_err, company, companies, web_operator,
                        idx_one_based=idx, total=len(pending),
                    )
                    if not recovered:
                        # 残り処理を中断
                        remaining = len(pending) - idx + 1  # 本企業含む
                        self.stats["server_down_aborted"] = remaining
                        server_down_aborted = True
                        break
                    # 復旧した → 同じ企業を再度キューに戻して次イテレーションで再実行
                    idx -= 1
                    continue
                except Exception as e:
                    # ヘルスチェックで「実はサーバーダウンだった」を救済
                    if is_server_down_error(e) or not check_server_alive():
                        logger.warning(
                            f"[サーバーダウン疑い] {company.name} 処理中の例外 ({e}) は"
                            "サーバー側の問題と判定 — 復旧待機に入ります"
                        )
                        recovered = await self._handle_server_down(
                            e, company, companies, web_operator,
                            idx_one_based=idx, total=len(pending),
                        )
                        if not recovered:
                            remaining = len(pending) - idx + 1
                            self.stats["server_down_aborted"] = remaining
                            server_down_aborted = True
                            break
                        idx -= 1
                        continue

                    logger.error(f"[ERROR] {company.name}: 処理失敗 -- {e}")
                    company.mark_error(str(e))
                    self.sheet_manager.update_company(company, companies)
                    self.stats["error"] += 1

                self.stats["processed"] += 1

                # ===== 1件完了後に再ログイン＆キャッシュクリア =====
                if idx < len(pending):  # 最後の企業では不要
                    logger.info("")
                    logger.info("1件処理完了 → 再ログイン＆キャッシュクリアを実行")
                    try:
                        await web_operator.re_login_with_cache_clear()
                    except ServerDownError as sd_err:
                        # 再ログイン中にサーバーダウン → 次企業に進む前に復旧待ち
                        recovered = await self._handle_server_down(
                            sd_err, company, companies, web_operator,
                            idx_one_based=idx, total=len(pending),
                            during_relogin=True,
                        )
                        if not recovered:
                            remaining = len(pending) - idx
                            self.stats["server_down_aborted"] = remaining
                            server_down_aborted = True
                            break
                    except Exception as e:
                        logger.warning(f"再ログイン失敗（続行）: {e}")

            # クリーンアップ
            try:
                await browser.close()
            except Exception:
                pass

            if server_down_aborted:
                logger.error("=" * 60)
                logger.error(
                    f"サーバー未復旧のため処理を中断しました "
                    f"(未処理 {self.stats['server_down_aborted']}社)。"
                )
                logger.error(
                    "サーバー復旧後に同じCSVで `python orchestrator.py` を再実行すると "
                    "途中ステータスから自動再開します。"
                )
                logger.error("=" * 60)

        # 最終レポート
        elapsed = datetime.now() - start_time
        self._print_summary(elapsed)

        # 納品URLのみのCSVを自動生成 (クライアント納品用)
        self._write_delivery_urls_csv()

    async def _handle_server_down(
        self,
        err: BaseException,
        company: CompanyInfo,
        companies: list,
        web_operator: WebAppOperator,
        idx_one_based: int,
        total: int,
        during_relogin: bool = False,
    ) -> bool:
        """
        サーバーダウン検出時の共通処理。

        手順:
            1. 現企業のステータスは「エラー」に落とさず保持 (途中状態のまま)
               → サーバー復旧後の再実行で続きから自動再開できる
            2. 現在の CSV 状態を保存
            3. サーバー復旧をポーリング待機
            4. 復旧したら再ログインを試み、呼び出し元に True を返す
               (呼び出し元は同企業を再試行 or 次企業へ進む)
            5. 復旧しなければ False を返す (呼び出し元は break する)

        Returns:
            True  : 復旧 (呼び出し元は処理続行可能)
            False : 未復旧 (呼び出し元は中断すべき)
        """
        logger.error("=" * 60)
        logger.error(
            f"[サーバーダウン検出] {company.name} "
            f"({idx_one_based}/{total}社目"
            + ("、再ログイン中" if during_relogin else "")
            + f") 処理中に Brainverse 管理画面サーバーへの到達が失敗しました"
        )
        logger.error(f"  詳細: {err}")
        logger.error("=" * 60)

        # 現状の CSV 状態を念のため保存 (途中ステータスを失わないため)
        try:
            self.sheet_manager.update_company(company, companies)
        except Exception as save_err:
            logger.warning(f"  CSV 保存に失敗 (続行): {save_err}")

        # 復旧待機
        recovered = await wait_for_server_recovery()
        if not recovered:
            logger.error(
                f"サーバーが最大待機時間 ({SERVER_RECOVERY_MAX_WAIT_MINUTES}分) "
                "以内に復旧しませんでした。"
            )
            return False

        # 復旧した → 再ログイン
        logger.info("サーバー復旧を確認 → 再ログインします")
        try:
            await web_operator.close_page_and_relogin()
        except ServerDownError as sd_err:
            logger.error(f"復旧直後の再ログイン中に再びサーバーダウン: {sd_err}")
            return False
        except Exception as relogin_err:
            logger.error(f"復旧後の再ログインに失敗: {relogin_err}")
            # 再ログインのリトライを 1回だけ
            await asyncio.sleep(RETRY_DELAY)
            try:
                await web_operator.close_page_and_relogin()
            except Exception as e2:
                logger.error(f"再ログイン 2回目も失敗: {e2} — 中断します")
                return False

        logger.info(
            f"{company.name} を再試行します" if not during_relogin
            else "次企業から処理を再開します"
        )
        return True

    async def _process_single_company(
        self,
        company: CompanyInfo,
        companies: list,
        url_finder: URLFinder,
        web_operator: WebAppOperator,
    ):
        """
        1社分の全処理フローを実行する。

        各ステップはステータスを更新し、途中から再開可能にする。
        """

        # ===== Step 1 (URL検索) は Stage 1 (select_urls.py) に移管済 =====
        # ここでは URL 確定済の前提で homepage_url + enterprise_id の同期だけ行う。
        if not company.homepage_url:
            # require_url=True フィルタで除外されているはずだが二重ガード
            logger.warning(
                f"  [SKIP] {company.name}: homepage_url が未確定。"
                "先に `python select_urls.py` を実行してください。"
            )
            self.stats["skipped"] += 1
            return

        # URLからenterprise_idを同期 (URL変更があった場合に備えて毎回確認)
        url_based_id = extract_enterprise_id_from_url(company.homepage_url)
        if url_based_id and url_based_id != "unknown":
            company.enterprise_id = url_based_id

        # PENDING / DUPLICATE_DETECTED かつ URL確定済 → URL_FOUND に昇格して後続処理
        if company.status in (
            ProcessStatus.PENDING,
            ProcessStatus.DUPLICATE_DETECTED,
        ):
            logger.info(
                f"Step 1/5: URL確定済の企業を Stage 2 で処理: {company.homepage_url}"
            )
            company.status = ProcessStatus.URL_FOUND
            company.error_message = ""
            self.sheet_manager.update_company(company, companies)

        # ===== Step 2: 企業追加 =====
        if company.status == ProcessStatus.URL_FOUND:
            logger.info("Step 2/6: 企業追加...")

            for attempt in range(RETRY_COUNT):
                try:
                    await web_operator.add_company(company)
                    break
                except Exception as e:
                    if attempt < RETRY_COUNT - 1:
                        logger.warning(
                            f"  リトライ {attempt + 1}/{RETRY_COUNT}: {e}"
                        )
                        await asyncio.sleep(RETRY_DELAY)
                        await web_operator.ensure_logged_in()
                    else:
                        raise

            # === Step 2.5: 企業追加直後の企業ID検証 ===
            # 「homepage_url から抽出した enterprise_id が、追加後の画面に
            #   正しく反映されているか」を確認する
            logger.info("Step 2.5/6: 追加企業のID検証...")
            try:
                await web_operator.verify_enterprise_id_in_added_company(company)
            except Exception as e:
                logger.error(f"  [ERR] 企業ID検証失敗: {e}")
                raise

            company.status = ProcessStatus.COMPANY_ADDED
            self.sheet_manager.update_company(company, companies)
            logger.info(f"  → 企業追加完了 (ID: {company.enterprise_id})")

            # === 企業追加完了後、Step 3 (コンテンツ生成準備) に進む前に
            #     ページを一度閉じて再ログインする ===
            # 目的: 企業追加時にページ内に残ったキャッシュ・JS状態・
            #       Streamlit session_state による「別企業のコンテンツが生成される」
            #       「サイドバー選択が反映されない」等の不具合を防ぐ
            logger.info("企業追加完了 → ページを閉じて再ログイン（キャッシュバグ防止）")
            try:
                await web_operator.close_page_and_relogin()
            except Exception as e:
                logger.warning(f"  ページクローズ＆再ログインに失敗（続行）: {e}")

        # ===== Step 3: 内部リンク + 求人サイトURL収集 =====
        if company.status == ProcessStatus.COMPANY_ADDED:
            logger.info("Step 3/5: URL収集...")

            try:
                # 内部リンク取得（HP内のサブページ）
                internal_links = await self._extract_links_only(
                    company.homepage_url
                )

                # 求人サイトURL取得（マイナビ・リクナビ等の該当企業ページ）
                recruit_links: list = []
                try:
                    recruit_links = await find_recruit_site_urls(
                        company.name, url_finder._page
                    )
                except Exception as e:
                    logger.warning(f"  [WARN] 求人サイトURL収集失敗: {e} (続行)")

                # マージ + 重複排除 (順序保持)
                merged = list(dict.fromkeys(internal_links + recruit_links))
                company.extracted_links = merged

                logger.info(
                    f"  → 内部リンク:{len(internal_links)}件, "
                    f"求人サイト:{len(recruit_links)}件, "
                    f"合計:{len(merged)}件"
                )
            except Exception as e:
                logger.warning(f"  [WARN] リンク取得失敗: {e} (続行)")
                company.extracted_links = [company.homepage_url]

        # ===== Step 4: コンテンツ生成 (検証失敗時は最大 FAQ_SAVE_MAX_RETRIES 回再生成) =====
        if company.status in (ProcessStatus.COMPANY_ADDED,):
            max_attempts = 1 + FAQ_SAVE_MAX_RETRIES
            for attempt in range(1, max_attempts + 1):
                logger.info(
                    f"Step 4/5: コンテンツ生成... "
                    f"(試行 {attempt}/{max_attempts})"
                )

                # コンテンツ生成ページへ遷移
                await web_operator.navigate_to_content_generator()
                await web_operator._wait_for_streamlit_load()

                # === Step 4-pre: サイドバーで企業を選択し、ヘッダーが対象企業に
                #     切り替わっているかを検証（失敗時は例外）===
                logger.info(
                    "  [検証] コンテンツ生成画面のヘッダーが対象企業に切替済か確認..."
                )
                await web_operator.select_company_from_sidebar(company)

                # URL入力
                if company.extracted_links:
                    await web_operator.input_urls_for_content(company.extracted_links)
                else:
                    logger.warning("  抽出リンクがありません。ホームページURLのみ入力...")
                    await web_operator.input_urls_for_content([company.homepage_url])

                # 生成実行 (内部で完了待機 + FAQ実体検証まで行う)
                # ContentSaveVerificationError が出たらリトライ対象
                try:
                    await web_operator.generate_content(company)

                    # === Step 4-post: 保存 + 検証 ===
                    logger.info("  [検証] 保存後のFAQ・企業情報タブを確認...")
                    await web_operator.save_content(company)

                    company.status = ProcessStatus.CONTENT_GENERATED
                    self.sheet_manager.update_company(company, companies)
                    logger.info("  → コンテンツ生成・保存・検証完了")
                    break
                except ContentSaveVerificationError as e:
                    reason_code = getattr(e, "reason_code", "unknown")
                    diagnostics = getattr(e, "diagnostics", {}) or {}

                    # サーバー側生成エラーは試行回数を消費せず即座にエラー扱い
                    # (リトライしても同じ結果になる可能性が高い + リソース節約)
                    if reason_code == "server_error":
                        logger.error(
                            f"  [ERR] サーバー側生成エラーを検出 — リトライしません: "
                            f"{e} / diagnostics={diagnostics}"
                        )
                        raise

                    if attempt >= max_attempts:
                        logger.error(
                            f"  [ERR] FAQ/企業情報の保存検証に "
                            f"{max_attempts}回失敗。最終的に失敗とします "
                            f"(reason_code={reason_code}): {e} / "
                            f"diagnostics={diagnostics}"
                        )
                        raise
                    logger.warning(
                        f"  [RETRY] 保存/生成検証失敗 "
                        f"(reason_code={reason_code}): {e} "
                        f"→ Step 4 から再試行 (次回 {attempt + 1}/{max_attempts}) / "
                        f"diagnostics={diagnostics}"
                    )
                    # ページ状態をクリーンにしてから次の試行へ
                    try:
                        await web_operator.close_page_and_relogin()
                    except Exception as relogin_err:
                        logger.warning(
                            f"  再ログイン失敗（続行）: {relogin_err}"
                        )

        # 背景画像アップロード機能はオミット
        # CONTENT_GENERATED から直接 IMAGE_UPLOADED に遷移して Step 5 (旧Step 6) へ進む
        if company.status == ProcessStatus.CONTENT_GENERATED:
            company.status = ProcessStatus.IMAGE_UPLOADED
            self.sheet_manager.update_company(company, companies)

        # ===== Step 5: フロントエンドアプリURL取得 =====
        if company.status == ProcessStatus.IMAGE_UPLOADED:
            logger.info("Step 5/5: フロントエンドアプリURL取得...")

            # サイドバーキャッシュ対策のためページクローズ&再ログインで状態クリーン化
            logger.info("Step 4完了 → ページを閉じて再ログイン（Step 5 のサイドバーキャッシュ対策）")
            try:
                await web_operator.close_page_and_relogin()
            except Exception as e:
                logger.warning(f"  ページクローズ＆再ログインに失敗（続行）: {e}")

            frontend_url = await web_operator.get_frontend_app_url(
                company_id=company.enterprise_id
            )
            company.frontend_app_url = frontend_url

            company.status = ProcessStatus.COMPLETED
            self.sheet_manager.update_company(company, companies)
            logger.info(f"  → フロントエンドURL: {frontend_url}")

        logger.info(f"[OK] {company.name}: 全処理完了!")

    async def _extract_links_only(self, homepage_url: str) -> list:
        """
        ページから内部リンクを抽出する（画像取得なし・軽量版）

        Returns:
            内部リンクのリスト
        """
        from urllib.parse import urlparse
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800}
                )
                page = await context.new_page()
                
                parsed_base = urlparse(homepage_url)
                base_domain = parsed_base.netloc
                
                await page.goto(homepage_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
                
                hrefs = await page.eval_on_selector_all(
                    "a", "elements => elements.map(el => el.href)"
                )
                
                unique_links = set()
                for href in hrefs:
                    if not href:
                        continue
                    clean_url = href.split('#')[0].rstrip('/')
                    from urllib.parse import urlparse as up
                    parsed_href = up(clean_url)
                    if parsed_href.netloc == base_domain:
                        unique_links.add(clean_url)
                
                await browser.close()
                
                # ホームページURL自体も含める
                unique_links.add(homepage_url)
                return sorted(list(unique_links))[:30]  # 最大30件
                
        except Exception as e:
            logger.warning(f"リンク抽出エラー: {e}")
            return [homepage_url]

    def _write_delivery_urls_csv(self):
        """
        企業リストCSV から「企業名」「納品URL」のみを抜き出した簡易CSVを生成する。
        ファイル名は元CSVから派生 (例: new30_company_list.csv → new30_delivery_urls.csv)。
        """
        import csv as csvmod

        src_path = self.sheet_manager.csv_path
        stem = src_path.stem
        if stem.endswith("_company_list"):
            out_stem = stem[: -len("_company_list")] + "_delivery_urls"
        else:
            out_stem = stem + "_delivery_urls"
        out_path = src_path.parent / f"{out_stem}.csv"

        companies = self.sheet_manager.read_company_list()
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csvmod.writer(f)
            w.writerow(["企業名", "納品URL"])
            for c in companies:
                w.writerow([c.name, c.frontend_app_url])

        delivered = sum(1 for c in companies if c.frontend_app_url)
        logger.info(
            f"納品URL一覧を生成: {out_path} "
            f"({len(companies)}社中、納品URLあり {delivered}社)"
        )

    def _print_summary(self, elapsed):
        """処理完了後のサマリーを出力"""
        logger.info("")
        logger.info("=" * 60)
        logger.info("DOCdemo 自動化フロー 完了サマリー")
        logger.info("=" * 60)
        logger.info(f"  処理対象:    {self.stats['total']}社")
        logger.info(f"  処理完了:    {self.stats['processed']}社")
        logger.info(f"  [OK] 成功:    {self.stats['success']}社")
        logger.info(f"  [HOLD] URL企業ID不一致候補複数: {self.stats['duplicate']}社（手動確認待ち）")
        logger.info(f"  [SKIP] スキップ: {self.stats['skipped']}社")
        logger.info(f"  [ERR] エラー:  {self.stats['error']}社")
        if self.stats.get("server_down_aborted", 0) > 0:
            logger.info(
                f"  [サーバーダウン中断]: {self.stats['server_down_aborted']}社 "
                "（途中ステータスを保持。サーバー復旧後の再実行で続行可能）"
            )
        logger.info(f"  所要時間:    {elapsed}")
        logger.info("=" * 60)
        if self.stats['duplicate'] > 0:
            logger.info(
                "  ※ URL企業ID不一致候補複数で HOLD された企業は CSV の "
                "「ホームページURL」列に「URL候補」列の中から正しいURLを記入して "
                "再実行してください。"
            )
            logger.info("=" * 60)


def parse_args():
    """コマンドライン引数の解析"""
    parser = argparse.ArgumentParser(
        description="DOCdemo 企業登録〜コンテンツ生成 完全自動化ツール"
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=None,
        help="企業リストCSVファイルのパス",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=None,
        help="ヘッドレスモードで実行",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="ブラウザを表示して実行（デバッグ用）",
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="テストモード（1社のみ処理）",
    )
    parser.add_argument(
        "--company",
        type=str,
        default=None,
        help="テストモード時の対象企業名",
    )
    return parser.parse_args()


async def main():
    """エントリーポイント"""
    setup_logging()
    args = parse_args()

    headless = True
    if args.no_headless:
        headless = False
    elif args.headless:
        headless = True

    csv_path = Path(args.csv) if args.csv else None

    orchestrator = Orchestrator(
        csv_path=csv_path,
        headless=headless,
        test_mode=args.test_mode,
        target_company=args.company,
    )

    await orchestrator.run()


if __name__ == "__main__":
    asyncio.run(main())
