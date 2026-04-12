"""
DOCdemo 自動化フロー — メインオーケストレーター

全自動化フローの統合・進捗管理・エラーハンドリングを担当する。
企業リストの読み込みから、企業追加、コンテンツ生成、画像アップロード、
リンク取得、スプレッドシート書き戻しまでの全工程を制御する。
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
)
from models import CompanyInfo, ProcessStatus
from spreadsheet_manager import SpreadsheetManager
from url_finder import URLFinder
from link_extractor import extract_internal_links_and_screenshot
from web_app_operator import WebAppOperator

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

    # コンソールハンドラ（UTF-8ストリームを使用）
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
                    self.stats["success"] += 1
                except Exception as e:
                    logger.error(f"[ERROR] {company.name}: 処理失敗 -- {e}")
                    company.mark_error(str(e))
                    self.sheet_manager.update_company(company, companies)
                    self.stats["error"] += 1

                self.stats["processed"] += 1

            # クリーンアップ
            await browser.close()

        # 最終レポート
        elapsed = datetime.now() - start_time
        self._print_summary(elapsed)

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
        if company.status == ProcessStatus.PENDING:
            logger.info("Step 1/6: ホームページURL検索...")

            if "リンクなし" in company.name or "見当たらず" in company.name:
                # 備考付き企業名のクリーニング
                clean_name = company.name.split("（")[0].split("(")[0].strip()
                company.name = clean_name

            homepage_url = await url_finder.find_homepage_url(company.name)

            if not homepage_url:
                company.mark_skipped("ホームページURLが見つかりませんでした")
                self.sheet_manager.update_company(company, companies)
                self.stats["skipped"] += 1
                logger.warning(f"[SKIP] {company.name}: URL不明のためスキップ")
                return

            company.homepage_url = homepage_url
            company.status = ProcessStatus.URL_FOUND
            self.sheet_manager.update_company(company, companies)
            logger.info(f"  → URL: {homepage_url}")

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

            company.status = ProcessStatus.COMPANY_ADDED
            self.sheet_manager.update_company(company, companies)
            logger.info(f"  → 企業追加完了 (ID: {company.enterprise_id})")

        # ===== Step 3: リンク抽出 + スクリーンショット =====
        if company.status == ProcessStatus.COMPANY_ADDED:
            logger.info("Step 3/6: リンク抽出 & スクリーンショット...")

            result = await extract_internal_links_and_screenshot(
                company.homepage_url
            )
            company.extracted_links = result["links"]
            company.screenshot_path = result["screenshot_path"]

            logger.info(
                f"  → {len(company.extracted_links)}件のリンク抽出, "
                f"スクリーンショット: {company.screenshot_path}"
            )

            # Step 3 完了だがステータスはCOMPANY_ADDEDのまま
            # (コンテンツ生成完了後にまとめて更新)

        # ===== Step 4: コンテンツ生成 =====
        if company.status in (
            ProcessStatus.COMPANY_ADDED,
        ):
            logger.info("Step 4/6: コンテンツ生成...")

            # コンテンツ生成ページへ遷移
            await web_operator.navigate_to_content_generator()

            # 企業選択
            await web_operator.select_company(company.enterprise_id)

            # URL入力
            if company.extracted_links:
                await web_operator.input_urls_for_content(
                    company.extracted_links
                )
            else:
                logger.warning("  抽出リンクがありません。ホームページURLのみ入力...")
                await web_operator.input_urls_for_content(
                    [company.homepage_url]
                )

            # 生成実行
            await web_operator.generate_content()

            # 保存
            await web_operator.save_content()

            company.status = ProcessStatus.CONTENT_GENERATED
            self.sheet_manager.update_company(company, companies)
            logger.info("  → コンテンツ生成・保存完了")

        # ===== Step 5: 背景画像アップロード =====
        if company.status == ProcessStatus.CONTENT_GENERATED:
            logger.info("Step 5/6: 背景画像アップロード...")

            if company.screenshot_path:
                try:
                    await web_operator.upload_background_image(
                        company.enterprise_id,
                        company.screenshot_path
                    )
                    company.status = ProcessStatus.IMAGE_UPLOADED
                    self.sheet_manager.update_company(company, companies)
                    logger.info("  → 背景画像アップロード完了")
                except Exception as e:
                    logger.warning(f"  [WARN] 画像アップロード失敗: {e} (続行)")
                    company.status = ProcessStatus.IMAGE_UPLOADED  # スキップして続行
                    self.sheet_manager.update_company(company, companies)
            else:
                logger.warning("  スクリーンショットなし: スキップ")
                company.status = ProcessStatus.IMAGE_UPLOADED
                self.sheet_manager.update_company(company, companies)

        # ===== Step 6: フロントエンドアプリURL取得 =====
        if company.status == ProcessStatus.IMAGE_UPLOADED:
            logger.info("Step 6/6: フロントエンドアプリURL取得...")

            frontend_url = await web_operator.get_frontend_app_url()
            company.frontend_app_url = frontend_url

            company.status = ProcessStatus.COMPLETED
            self.sheet_manager.update_company(company, companies)
            logger.info(f"  → フロントエンドURL: {frontend_url}")

        logger.info(f"[OK] {company.name}: 全処理完了!")

    def _print_summary(self, elapsed):
        """処理完了後のサマリーを出力"""
        logger.info("")
        logger.info("=" * 60)
        logger.info("DOCdemo 自動化フロー 完了サマリー")
        logger.info("=" * 60)
        logger.info(f"  処理対象:    {self.stats['total']}社")
        logger.info(f"  処理完了:    {self.stats['processed']}社")
        logger.info(f"  [OK] 成功:   {self.stats['success']}社")
        logger.info(f"  [SKIP] スキップ: {self.stats['skipped']}社")
        logger.info(f"  [ERR] エラー:  {self.stats['error']}社")
        logger.info(f"  所要時間:    {elapsed}")
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
