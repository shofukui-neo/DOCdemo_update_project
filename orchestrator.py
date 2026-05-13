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
import subprocess
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
    URL_CANDIDATE_MAX,
    FAQ_SAVE_MAX_RETRIES,
    PROJECT_ROOT,
)
from models import CompanyInfo, ProcessStatus
from spreadsheet_manager import SpreadsheetManager, write_url_candidates
from url_finder import URLFinder
from image_fetcher import extract_enterprise_id_from_url
from recruit_url_finder import find_recruit_site_urls
from web_app_operator import WebAppOperator, ContentSaveVerificationError

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
        }

    async def run(self):
        """メインエントリーポイント: 全企業の自動化フローを実行する。"""
        start_time = datetime.now()
        logger.info("=" * 60)
        logger.info("DOCdemo 自動化フロー 開始")
        logger.info(f"開始時刻: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)

        # 企業リスト読み込み
        companies = self.sheet_manager.read_company_list()
        pending = self.sheet_manager.get_pending_companies(companies)

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
            for idx, company in enumerate(pending, start=1):
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
                except Exception as e:
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
                    except Exception as e:
                        logger.warning(f"再ログイン失敗（続行）: {e}")

            # クリーンアップ
            await browser.close()

        # 最終レポート
        elapsed = datetime.now() - start_time
        self._print_summary(elapsed)

        # 納品URLのみのCSVを自動生成 (クライアント納品用)
        self._write_delivery_urls_csv()

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

        # ===== Step 1: ホームページURL検索 =====
        # ===== Step 1.5: URL企業ID重複検証 =====
        #   候補URLから抽出した企業ID（URL内で企業を表すスラッグ）が
        #   完全一致しない種類が複数あれば手動確認待ちで中断する。
        if company.status == ProcessStatus.PENDING:
            logger.info("Step 1/6: ホームページURL候補検索...")

            if "リンクなし" in company.name or "見当たらず" in company.name:
                clean_name = company.name.split("（")[0].split("(")[0].strip()
                company.name = clean_name

            candidates = await url_finder.find_homepage_candidates(
                company.name, max_candidates=URL_CANDIDATE_MAX
            )

            if not candidates:
                company.mark_skipped("ホームページURLが見つかりませんでした")
                self.sheet_manager.update_company(company, companies)
                self.stats["skipped"] += 1
                logger.warning(f"[SKIP] {company.name}: URL不明のためスキップ")
                return

            # === URL企業ID重複検証 ===
            # 各候補URLから企業IDを抽出し、ユニークなIDが複数あれば
            # 「URL内の企業IDが完全一致しない候補が複数」=手動確認待ち。
            candidate_ids = [
                extract_enterprise_id_from_url(c) for c in candidates
            ]
            unique_ids = {eid for eid in candidate_ids if eid and eid != "unknown"}

            if len(unique_ids) >= 2:
                logger.warning(
                    f"  [PAUSE] URL内の企業IDが完全一致しない候補が "
                    f"{len(unique_ids)} 種類 (候補URL {len(candidates)}件) "
                    f"検出されました → HOLD UI ポップアップを起動します"
                )
                for idx_c, (cand, eid) in enumerate(
                    zip(candidates, candidate_ids), start=1
                ):
                    logger.warning(f"    候補 {idx_c}: {cand} (企業ID: {eid})")

                # HOLD状態として一旦記録 (URL候補はサイドカーJSONに自動保存)
                company.mark_duplicate(
                    candidates,
                    reason=(
                        f"URL内の企業IDが完全一致しない候補が "
                        f"{len(unique_ids)} 種類検出されました。"
                        "ポップアップでURLを選択してください。"
                    ),
                )
                # CompanyInfo にURL候補を反映してから保存 (サイドカーJSONに書き出される)
                company.url_candidates = candidates
                self.sheet_manager.update_company(company, companies)

                # === HOLD UI を同期起動 (subprocess.run でブロッキング) ===
                self._launch_hold_ui_for_company(company)

                # UI終了後、CSV を再読込してこの企業の最新状態を取得
                refreshed = self._reload_company(company, companies)
                if refreshed is None:
                    logger.error(f"  [ERR] {company.name}: 再読込失敗")
                    self.stats["duplicate"] += 1
                    return

                # ユーザがURLを採用してくれた → URL_FOUND として続行
                if refreshed.homepage_url:
                    logger.info(
                        f"  HOLD UI で URL 採用: {refreshed.homepage_url} → "
                        f"Step 2 へ続行"
                    )
                    company.homepage_url = refreshed.homepage_url
                    company.url_candidates = refreshed.url_candidates or candidates
                    url_based_id = extract_enterprise_id_from_url(company.homepage_url)
                    if url_based_id and url_based_id != "unknown":
                        company.enterprise_id = url_based_id
                    company.status = ProcessStatus.URL_FOUND
                    company.error_message = ""
                    self.sheet_manager.update_company(company, companies)
                else:
                    # ユーザがスキップ or UIを閉じた → HOLDのまま次へ
                    self.stats["duplicate"] += 1
                    logger.warning(
                        f"[HOLD] {company.name}: URLが選択されませんでした "
                        f"(後で resolve_hold_ui.py を再実行してください)"
                    )
                    return

            # 候補のURL企業IDが1種類のみ (TLD違い・サブドメイン正規化後に同一)
            # → 先頭の候補URLを採用して通常フロー
            homepage_url = candidates[0]
            company.homepage_url = homepage_url
            company.url_candidates = candidates  # 履歴として保持

            url_based_id = extract_enterprise_id_from_url(homepage_url)
            if url_based_id and url_based_id != "unknown":
                logger.info(f"  企業ID (URLから抽出): {url_based_id}")
                company.enterprise_id = url_based_id
            else:
                logger.warning(
                    f"  URLからIDを抽出できないため、企業名から生成します: "
                    f"{company.enterprise_id}"
                )

            company.status = ProcessStatus.URL_FOUND
            self.sheet_manager.update_company(company, companies)
            logger.info(f"  → URL: {homepage_url}, ID: {company.enterprise_id}")

        # URL企業ID不一致で HOLD された状態から再実行されたケース
        # （人間が CSV の homepage_url 列に正しいURLを記入した想定）
        # → URL_FOUND として扱い後続処理に進める
        elif (
            company.status == ProcessStatus.DUPLICATE_DETECTED
            and company.homepage_url
        ):
            logger.info(
                f"Step 1/6: URL企業ID不一致 HOLD からの手動再開: {company.homepage_url}"
            )
            url_based_id = extract_enterprise_id_from_url(company.homepage_url)
            if url_based_id and url_based_id != "unknown":
                company.enterprise_id = url_based_id
            company.status = ProcessStatus.URL_FOUND
            company.error_message = ""
            self.sheet_manager.update_company(company, companies)

        # URLが既にある場合もIDをURLから再確認
        elif company.status != ProcessStatus.PENDING and company.homepage_url:
            url_based_id = extract_enterprise_id_from_url(company.homepage_url)
            if url_based_id and url_based_id != "unknown":
                company.enterprise_id = url_based_id

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

                # 生成実行 (内部で完了待機 + FAQ実体検証まで行う。
                # 検証失敗時は ContentSaveVerificationError が送出され、
                # 下の except でリトライ対象になる)
                await web_operator.generate_content(company)

                # === Step 4-post: 保存（2段階）+ コンテンツ管理タブで
                #     FAQ・企業情報の両タブに対象企業のコンテンツが反映されたかを検証 ===
                logger.info("  [検証] 保存後のFAQ・企業情報タブを確認...")
                try:
                    await web_operator.save_content(company)
                    company.status = ProcessStatus.CONTENT_GENERATED
                    self.sheet_manager.update_company(company, companies)
                    logger.info("  → コンテンツ生成・保存・検証完了")
                    break
                except ContentSaveVerificationError as e:
                    if attempt >= max_attempts:
                        logger.error(
                            f"  [ERR] FAQ/企業情報の保存検証に "
                            f"{max_attempts}回失敗。最終的に失敗とします: {e}"
                        )
                        raise
                    logger.warning(
                        f"  [RETRY] 保存検証失敗 ({e}) "
                        f"→ Step 4 (コンテンツ生成) から再試行します "
                        f"(次回試行 {attempt + 1}/{max_attempts})"
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

    def _launch_hold_ui_for_company(self, company: CompanyInfo):
        """
        HOLD UI (resolve_hold_ui.py) を subprocess で同期起動する。
        ユーザがUIを閉じるまでブロッキングし、結果はCSVに書き戻される。

        Args:
            company: HOLD対象の企業 (--company-id で UI 側にフィルタを渡す)
        """
        script_path = PROJECT_ROOT / "resolve_hold_ui.py"
        cmd = [
            sys.executable,
            str(script_path),
            "--csv", str(self.sheet_manager.csv_path),
            "--company-id", company.enterprise_id,
        ]
        logger.info(
            f"  HOLD UI ポップアップを起動: {company.name} "
            f"(企業ID: {company.enterprise_id})"
        )
        try:
            # tkinter UI は親プロセスとは独立したウィンドウなので
            # subprocess.run で起動 (UIが閉じられるまでブロック)
            result = subprocess.run(cmd, check=False)
            logger.info(f"  HOLD UI 終了 (rc={result.returncode})")
        except Exception as e:
            logger.error(f"  HOLD UI 起動失敗: {e}")

    def _reload_company(
        self,
        company: CompanyInfo,
        companies: list,
    ):
        """
        CSV を再読込し、指定企業の最新状態を返す。
        in-place で companies リストも更新する。
        """
        try:
            refreshed_list = self.sheet_manager.read_company_list()
        except Exception as e:
            logger.error(f"  CSV 再読込失敗: {e}")
            return None

        for r in refreshed_list:
            if r.enterprise_id == company.enterprise_id or r.name == company.name:
                # in-place で companies リストも更新
                for i, c in enumerate(companies):
                    if c.row_index == company.row_index:
                        # url_candidates は新CSVスキーマでサイドカーJSONから
                        # 読込済みなので r の値を採用
                        companies[i] = r
                        break
                return r
        return None

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
