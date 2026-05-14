"""
DOCdemo 自動化フロー — Stage 1: URL選定フェーズ

[全体設計: 2段階構成]
    Stage 1 (この script): 企業URLのセレクトフェーズ
        入力: 企業名のみ または 企業名+URL の CSV (1列〜8列いずれも可)
        処理: 各企業のホームページURLを検索・確定
        出力: ホームページURL列が埋まった 8列CSV (Stage 2 で使用)

    Stage 2 (orchestrator.py): デモ作成自動化
        入力: Stage 1 出力 CSV (URL確定済み)
        処理: 企業追加→コンテンツ生成→保存→納品URL取得
        URL未入力の行は自動スキップ

[Stage 1 処理フロー]
    1. CSV読込 (1列/2列/8列を自動判定)
    2. 各企業について:
        - homepage_url がすでに埋まっている → スキップ
        - 検索結果が無い → SKIPPED
        - 検索結果から抽出した企業IDが 1種類のみ → 先頭候補を採用 (URL_FOUND)
        - 検索結果の企業IDが 2種類以上 → DUPLICATE_DETECTED + URL候補列に記録
    3. 全社処理完了後、HOLD企業が 1社以上あれば
       resolve_hold_ui.py を起動 (一括処理ポップアップ)
    4. UI終了後にサマリ出力

使い方:
    python select_urls.py                                # data/company_list.csv を対象
    python select_urls.py --csv data/foo.csv             # CSV指定
    python select_urls.py --csv data/foo.csv --no-popup  # HOLD UI を起動しない
"""

import argparse
import asyncio
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from config import (
    BROWSER_HEADLESS,
    BROWSER_SLOW_MO,
    BROWSER_VIEWPORT,
    LOGS_DIR,
    LOG_FORMAT,
    LOG_LEVEL,
    LOG_FILE,
    PROJECT_ROOT,
    URL_CANDIDATE_MAX,
)
from image_fetcher import extract_enterprise_id_from_url
from models import CompanyInfo, ProcessStatus
from spreadsheet_manager import SpreadsheetManager
from url_finder import URLFinder

logger = logging.getLogger(__name__)


def setup_logging():
    """ロギング初期化 (orchestrator.py と同じ方針)"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace"
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace"
        )
    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(ch)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(fh)


class URLSelector:
    """
    Stage 1 のメインクラス。全企業のホームページURLを確定させる。

    ステータス遷移:
        PENDING        → URL_FOUND           (候補 1種類)
        PENDING        → DUPLICATE_DETECTED  (候補 2種類以上、HOLD)
        PENDING        → SKIPPED             (候補なし)
        URL_FOUND      → URL_FOUND           (既に確定済、何もしない)
        DUPLICATE_DETECTED + homepage_url → URL_FOUND (前回HOLD解消済)
    """

    def __init__(self, csv_path: Path = None, headless: bool = None):
        self.sheet_manager = SpreadsheetManager(csv_path)
        self.headless = headless if headless is not None else BROWSER_HEADLESS
        self.stats = {
            "total": 0,
            "already_resolved": 0,
            "newly_found": 0,
            "duplicate": 0,
            "skipped": 0,
            "error": 0,
        }

    async def run(self) -> int:
        """エントリポイント。HOLD企業数を返す (resolve_hold_ui 起動判定用)"""
        start = datetime.now()
        logger.info("=" * 60)
        logger.info("Stage 1: URL選定フェーズ 開始")
        logger.info(f"開始時刻: {start.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"対象CSV: {self.sheet_manager.csv_path}")
        logger.info("=" * 60)

        companies = self.sheet_manager.read_company_list()
        self.stats["total"] = len(companies)
        if not companies:
            logger.warning("企業リストが空です。終了します。")
            return 0

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless, slow_mo=BROWSER_SLOW_MO
            )
            context = await browser.new_context(viewport=BROWSER_VIEWPORT)
            page = await context.new_page()
            url_finder = URLFinder(page=page)

            for idx, company in enumerate(companies, start=1):
                logger.info("")
                logger.info(
                    f"[{idx}/{len(companies)}] {company.name} "
                    f"(現ステータス: {company.status.value})"
                )
                try:
                    await self._process_one(company, companies, url_finder)
                except Exception as e:
                    logger.error(f"  [ERR] {company.name}: {e}")
                    company.mark_error(str(e))
                    self.sheet_manager.update_company(company, companies)
                    self.stats["error"] += 1

            await browser.close()

        elapsed = datetime.now() - start
        self._print_summary(elapsed)
        return self.stats["duplicate"]

    async def _process_one(
        self,
        company: CompanyInfo,
        companies: list,
        url_finder: URLFinder,
    ):
        """1社分のURL選定処理"""
        # 既にURL確定済 → 何もしない
        if company.homepage_url and company.status not in (
            ProcessStatus.PENDING,
            ProcessStatus.DUPLICATE_DETECTED,
        ):
            logger.info(f"  → 既にURL確定済: {company.homepage_url}")
            self.stats["already_resolved"] += 1
            return

        # 2列入力で homepage_url が入っている (status=URL_FOUND) → ID補完のみ
        if (
            company.status == ProcessStatus.URL_FOUND
            and company.homepage_url
            and not company.url_candidates
        ):
            self._fill_enterprise_id(company)
            self.sheet_manager.update_company(company, companies)
            logger.info(
                f"  → 入力済URLを採用: {company.homepage_url} "
                f"(ID: {company.enterprise_id})"
            )
            self.stats["already_resolved"] += 1
            return

        # HOLDからの再開ケース: ユーザがホームページURL列を埋めた → URL_FOUND へ
        if (
            company.status == ProcessStatus.DUPLICATE_DETECTED
            and company.homepage_url
        ):
            self._fill_enterprise_id(company)
            company.status = ProcessStatus.URL_FOUND
            company.error_message = ""
            self.sheet_manager.update_company(company, companies)
            logger.info(
                f"  → HOLD解消: {company.homepage_url} "
                f"(ID: {company.enterprise_id})"
            )
            self.stats["newly_found"] += 1
            return

        # 通常フロー: URL検索
        if "リンクなし" in company.name or "見当たらず" in company.name:
            company.name = company.name.split("（")[0].split("(")[0].strip()

        logger.info("  URL候補を検索中...")
        candidates = await url_finder.find_homepage_candidates(
            company.name, max_candidates=URL_CANDIDATE_MAX
        )

        if not candidates:
            company.mark_skipped("ホームページURLが見つかりませんでした")
            self.sheet_manager.update_company(company, companies)
            self.stats["skipped"] += 1
            logger.warning(f"  [SKIP] URL候補なし")
            return

        candidate_ids = [extract_enterprise_id_from_url(c) for c in candidates]
        unique_ids = {eid for eid in candidate_ids if eid and eid != "unknown"}

        if len(unique_ids) >= 2:
            # HOLD
            for k, (cand, eid) in enumerate(
                zip(candidates, candidate_ids), start=1
            ):
                logger.warning(f"    候補{k}: {cand} (企業ID: {eid})")
            company.mark_duplicate(
                candidates,
                reason=(
                    f"URL内の企業IDが {len(unique_ids)} 種類検出されました。"
                    "あとで HOLD UI から正しいURLを選択してください。"
                ),
            )
            self.sheet_manager.update_company(company, companies)
            self.stats["duplicate"] += 1
            logger.warning(
                f"  [HOLD] {company.name}: 候補ID {len(unique_ids)}種類 → 後で UI で確定"
            )
            return

        # 単一企業ID → 先頭候補を採用
        company.homepage_url = candidates[0]
        company.url_candidates = candidates
        self._fill_enterprise_id(company)
        company.status = ProcessStatus.URL_FOUND
        company.error_message = ""
        self.sheet_manager.update_company(company, companies)
        self.stats["newly_found"] += 1
        logger.info(
            f"  → URL確定: {company.homepage_url} "
            f"(ID: {company.enterprise_id})"
        )

    def _fill_enterprise_id(self, company: CompanyInfo):
        """homepage_url から企業IDを抽出。失敗時は企業名由来のIDをそのまま使う。"""
        if not company.homepage_url:
            return
        url_based_id = extract_enterprise_id_from_url(company.homepage_url)
        if url_based_id and url_based_id != "unknown":
            company.enterprise_id = url_based_id

    def _print_summary(self, elapsed):
        logger.info("")
        logger.info("=" * 60)
        logger.info("Stage 1: URL選定フェーズ 完了サマリー")
        logger.info("=" * 60)
        logger.info(f"  対象企業:           {self.stats['total']}社")
        logger.info(f"  [既済] 既にURL確定:  {self.stats['already_resolved']}社")
        logger.info(f"  [新規] 今回URL確定:  {self.stats['newly_found']}社")
        logger.info(f"  [HOLD] 候補複数:    {self.stats['duplicate']}社")
        logger.info(f"  [SKIP] 候補なし:    {self.stats['skipped']}社")
        logger.info(f"  [ERR]  エラー:      {self.stats['error']}社")
        logger.info(f"  所要時間:           {elapsed}")
        logger.info("=" * 60)


def launch_hold_ui(csv_path: Path):
    """resolve_hold_ui.py を subprocess で同期起動 (UIが閉じるまでブロック)"""
    script_path = PROJECT_ROOT / "resolve_hold_ui.py"
    if not script_path.exists():
        logger.error(f"resolve_hold_ui.py が見つかりません: {script_path}")
        return
    cmd = [sys.executable, str(script_path), "--csv", str(csv_path)]
    logger.info("")
    logger.info("HOLD企業のURL選定UIを起動します...")
    logger.info("  (UI上で各企業の正しいURLを選択 → 終了して保存)")
    try:
        rc = subprocess.run(cmd, check=False).returncode
        logger.info(f"HOLD UI 終了 (rc={rc})")
    except Exception as e:
        logger.error(f"HOLD UI 起動失敗: {e}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Stage 1: 企業リストCSVのホームページURLを検索・確定する"
    )
    parser.add_argument(
        "--csv", type=str, default=None,
        help="企業リストCSVファイルのパス (デフォルト: config.COMPANY_LIST_CSV)",
    )
    parser.add_argument(
        "--headless", action="store_true", default=None,
        help="ヘッドレスモードで実行",
    )
    parser.add_argument(
        "--no-headless", action="store_true",
        help="ブラウザを表示して実行",
    )
    parser.add_argument(
        "--no-popup", action="store_true",
        help="HOLD UI のポップアップを起動しない (HOLDはCSVに記録のみ)",
    )
    return parser.parse_args()


async def main():
    setup_logging()
    args = parse_args()

    headless = True
    if args.no_headless:
        headless = False
    elif args.headless:
        headless = True

    csv_path = Path(args.csv) if args.csv else None

    selector = URLSelector(csv_path=csv_path, headless=headless)
    hold_count = await selector.run()

    # HOLD企業があれば resolve_hold_ui を起動 (--no-popup 指定時はスキップ)
    if hold_count > 0 and not args.no_popup:
        launch_hold_ui(selector.sheet_manager.csv_path)
        # UI終了後にCSVが更新されているため、HOLD解消結果のサマリを再表示
        logger.info("")
        logger.info("HOLD UI 終了後の状態を再確認...")
        post_companies = selector.sheet_manager.read_company_list()
        remaining_hold = sum(
            1 for c in post_companies
            if c.status == ProcessStatus.DUPLICATE_DETECTED
            and not c.homepage_url
        )
        resolved = sum(
            1 for c in post_companies
            if c.status == ProcessStatus.URL_FOUND and c.homepage_url
        )
        logger.info(f"  URL確定済: {resolved}社")
        logger.info(f"  未解消HOLD: {remaining_hold}社")
        if remaining_hold > 0:
            logger.warning(
                f"  ※ 未解消の HOLD が {remaining_hold}社 あります。"
                f"後で `python resolve_hold_ui.py` を再実行できます。"
            )

    logger.info("")
    logger.info("Stage 1 完了。次は `python orchestrator.py` で Stage 2 を実行してください。")


if __name__ == "__main__":
    asyncio.run(main())
