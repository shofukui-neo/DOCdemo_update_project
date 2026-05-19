"""
DOCdemo 自動化フロー — Stage 4: 納品URL品質チェック

[全体設計]
    Stage 1 (select_urls.py)      : URL選定
    Stage 2 (orchestrator.py)     : デモ作成 (企業追加〜FAQ生成〜納品URL取得)
    Stage 4 (この script)          : 納品URL の品質を自動チェック

[Stage 4 の検証項目 (5項目)]
    1. HTTP応答     : 納品URLが2xxで応答するか
    2. 企業名表示    : ページタイトル/ヘッダーに対象企業名 or 企業IDが表示されているか
    3. 背景画像     : 背景画像が default 以外で設定されているか
                     (background-image CSS / <img> タグの src 存在)
    4. FAQセクション : FAQ的なコンテンツ + 対象企業の名前/IDが本文にあるか
    5. AIチャット    : チャット起動 → テスト質問送信 → 返信に対象企業名が含まれるか

[判定ロジック]
    - 全項目OK         : quality_check="OK"、ステータス維持
    - 1項目以上NG      : quality_check="NG"、ステータスを「エラー」に戻す
                         → 次回 Stage 2 で自動再処理
    - 部分OK (Web側の制約等で一部判定不能) : "部分OK"、ステータス維持

[出力]
    - CSV: 「品質チェック」「品質チェック詳細」列を更新
    - screenshots/quality/<企業ID>.png : ページのスクリーンショット
    - logs/automation.log : 詳細ログ

使い方:
    python verify_quality.py                         # 全完了企業を再チェック
    python verify_quality.py --csv data/foo.csv
    python verify_quality.py --no-headless           # ブラウザ表示で実行
    python verify_quality.py --company "株式会社サンプル"  # 1社のみテスト

    # 2列CSV (企業名,納品URL) を強制的に検証 (ステータスフィルタ無視)
    python verify_quality.py --delivery-csv data/company_list_delivery_urls.csv
"""

import argparse
import asyncio
import csv
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Page

from config import (
    BROWSER_HEADLESS,
    BROWSER_SLOW_MO,
    BROWSER_VIEWPORT,
    LOGS_DIR,
    LOG_FORMAT,
    LOG_LEVEL,
    LOG_FILE,
    PAGE_LOAD_TIMEOUT,
    QUALITY_CHECK_CHAT_QUESTION,
    QUALITY_CHECK_CHAT_TIMEOUT,
    QUALITY_SCREENSHOTS_DIR,
    SERVER_DOWN_HTTP_STATUSES,
    SERVER_RECOVERY_MAX_WAIT_MINUTES,
)
from models import CompanyInfo, ProcessStatus
from spreadsheet_manager import SpreadsheetManager
from web_app_operator import (
    ServerDownError,
    check_server_alive,
    is_server_down_error,
    wait_for_server_recovery,
)

logger = logging.getLogger(__name__)


def setup_logging():
    """ロギング初期化 (他Stageと同じ方針)"""
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


class CheckResult:
    """1企業の品質チェック結果を保持する。"""

    LABELS = ["HTTP", "企業名", "背景画像", "FAQ", "AIチャット"]

    def __init__(self):
        # 項目名 → "OK" / "NG" / "SKIP" / 未設定(None)
        self.items = {label: None for label in self.LABELS}
        # 項目名 → 補足メッセージ (NG時に使う)
        self.notes = {label: "" for label in self.LABELS}

    def set(self, label: str, ok: bool, note: str = ""):
        self.items[label] = "OK" if ok else "NG"
        if note:
            self.notes[label] = note

    def skip(self, label: str, note: str = ""):
        self.items[label] = "SKIP"
        self.notes[label] = note

    def overall(self) -> str:
        """総合判定: OK / NG / 部分OK / 未完"""
        vals = [v for v in self.items.values() if v is not None]
        if not vals:
            return ""
        if "NG" in vals:
            return "NG"
        if "SKIP" in vals:
            return "部分OK"
        return "OK"

    def detail(self) -> str:
        """項目別の OK/NG 内訳を1行文字列で返す。"""
        parts = []
        for label in self.LABELS:
            status = self.items.get(label)
            if status is None:
                continue
            note = self.notes.get(label, "")
            if note:
                parts.append(f"{label}={status}({note})")
            else:
                parts.append(f"{label}={status}")
        return " / ".join(parts)


class QualityVerifier:
    """Stage 4 のメインクラス。完了済企業の納品URLを実機で品質チェックする。"""

    def __init__(
        self,
        csv_path: Path = None,
        headless: bool = None,
        target_company: str = None,
        delivery_csv_path: Path = None,
    ):
        # 通常モード (company_list.csv フルスキーマ) と
        # 配信CSVモード (2列「企業名,納品URL」を強制検証) を排他で持つ
        self.delivery_csv_path = delivery_csv_path
        if delivery_csv_path:
            self.sheet_manager = None
        else:
            self.sheet_manager = SpreadsheetManager(csv_path)
        self.headless = headless if headless is not None else BROWSER_HEADLESS
        self.target_company = target_company
        # 配信CSVモードで使用する元の行データ (再書き出しに使う)
        self._delivery_raw_rows: list = []
        self.stats = {
            "total": 0,
            "ok": 0,
            "partial": 0,
            "ng": 0,
            "error": 0,
            "server_down_aborted": 0,  # サーバーダウンで途中中断した社数
        }

    async def run(self):
        """メインエントリーポイント。"""
        start = datetime.now()
        logger.info("=" * 60)
        logger.info("Stage 4: 納品URL品質チェック 開始")
        logger.info(f"開始時刻: {start.strftime('%Y-%m-%d %H:%M:%S')}")
        if self.delivery_csv_path:
            logger.info(f"対象CSV: {self.delivery_csv_path} (配信CSVモード)")
        else:
            logger.info(f"対象CSV: {self.sheet_manager.csv_path}")
        logger.info("=" * 60)

        QUALITY_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        # 配信CSVモード: 2列CSVを直接読み、status フィルタを無視
        if self.delivery_csv_path:
            companies = self._read_delivery_csv(self.delivery_csv_path)
            targets = companies
            if self.target_company:
                targets = [c for c in targets if self.target_company in c.name]
                if not targets:
                    logger.error(
                        f"対象企業が見つかりません: {self.target_company}"
                    )
                    return
        else:
            companies = self.sheet_manager.read_company_list()
            targets = self.sheet_manager.get_completed_companies(
                companies, require_delivery_url=True
            )
            if self.target_company:
                targets = [c for c in targets if self.target_company in c.name]
                if not targets:
                    logger.error(
                        f"対象企業が見つかりません (完了済かつURL有): {self.target_company}"
                    )
                    return

        self.stats["total"] = len(targets)
        if not targets:
            logger.warning("品質チェック対象企業がありません。終了します。")
            return

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless, slow_mo=BROWSER_SLOW_MO
            )
            context = await browser.new_context(viewport=BROWSER_VIEWPORT)

            idx = 0
            server_down_aborted = False
            while idx < len(targets):
                company = targets[idx]
                idx += 1
                logger.info("")
                logger.info("─" * 50)
                logger.info(
                    f"[{idx}/{len(targets)}] {company.name} "
                    f"(URL: {company.frontend_app_url})"
                )
                logger.info("─" * 50)

                page = await context.new_page()
                try:
                    result = await self._verify_one(company, page)
                    if self.delivery_csv_path:
                        self._apply_result_delivery(company, result)
                    else:
                        self._apply_result(company, companies, result)
                except ServerDownError as sd_err:
                    logger.error("=" * 60)
                    logger.error(
                        f"[サーバーダウン検出] {company.name} "
                        f"({idx}/{len(targets)}社目) 検証中に Brainverse 側へ到達不能"
                    )
                    logger.error(f"  詳細: {sd_err}")
                    logger.error("=" * 60)
                    # この企業の品質判定は触らず保留 (復旧後の再実行で正しく判定する)
                    recovered = await wait_for_server_recovery()
                    if not recovered:
                        remaining = len(targets) - idx + 1
                        self.stats["server_down_aborted"] = remaining
                        server_down_aborted = True
                        try:
                            await page.close()
                        except Exception:
                            pass
                        break
                    # 復旧 → 同企業を再試行
                    idx -= 1
                except Exception as e:
                    # ヘルスチェックで「実はサーバーダウン」を救済
                    if is_server_down_error(e) or not check_server_alive():
                        logger.warning(
                            f"[サーバーダウン疑い] 検証中の例外 ({e}) はサーバー側の問題と判定"
                        )
                        recovered = await wait_for_server_recovery()
                        if not recovered:
                            remaining = len(targets) - idx + 1
                            self.stats["server_down_aborted"] = remaining
                            server_down_aborted = True
                            try:
                                await page.close()
                            except Exception:
                                pass
                            break
                        idx -= 1
                    else:
                        logger.error(f"  [ERR] {company.name}: 検証中に例外 -- {e}")
                        company.quality_check = "NG"
                        company.quality_detail = f"検証例外: {e}"
                        self.stats["error"] += 1
                        if self.delivery_csv_path:
                            self._save_delivery_csv(targets)
                        else:
                            company.mark_error(f"品質チェック検証失敗: {e}")
                            self.sheet_manager.update_company(company, companies)
                finally:
                    try:
                        await page.close()
                    except Exception:
                        pass

            try:
                await browser.close()
            except Exception:
                pass

            if server_down_aborted:
                logger.error("=" * 60)
                logger.error(
                    f"サーバー未復旧のため Stage 4 を中断 "
                    f"(未検証 {self.stats['server_down_aborted']}社)。"
                    f"サーバー復旧後に `python verify_quality.py` を再実行してください。"
                )
                logger.error("=" * 60)

        elapsed = datetime.now() - start
        self._print_summary(elapsed)

    async def _verify_one(self, company: CompanyInfo, page: Page) -> CheckResult:
        """1社分の5項目チェックを実行する。"""
        result = CheckResult()
        url = company.frontend_app_url

        # ===== Check 1: HTTP応答 =====
        try:
            try:
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT
                )
            except Exception as goto_err:
                # 接続不可系は ServerDownError として再 raise (上位ループが復旧待機する)
                if is_server_down_error(goto_err):
                    raise ServerDownError(
                        f"納品URL ({url}) 接続不可: {goto_err}"
                    ) from goto_err
                raise

            status = response.status if response else 0
            # 5xx はサーバーダウン扱い (品質判定ではなく上位で復旧待ち)
            if status in SERVER_DOWN_HTTP_STATUSES or (status >= 500):
                raise ServerDownError(
                    f"納品URL ({url}) が HTTP {status} を返しました"
                )
            if 200 <= status < 400:
                result.set("HTTP", True, f"status={status}")
                logger.info(f"  [OK] HTTP: status={status}")
            else:
                result.set("HTTP", False, f"status={status}")
                logger.warning(f"  [NG] HTTP: status={status}")
        except ServerDownError:
            raise
        except Exception as e:
            result.set("HTTP", False, f"接続失敗: {e}")
            logger.warning(f"  [NG] HTTP: {e}")
            # HTTP接続失敗時は以降をスキップ
            for label in ("企業名", "背景画像", "FAQ", "AIチャット"):
                result.skip(label, "HTTP失敗のため未実施")
            return result

        # ページ完全読込を少し待つ (SPA対応)
        await page.wait_for_timeout(4000)

        # スクリーンショット保存 (項目別の証跡)
        screenshot_path = (
            QUALITY_SCREENSHOTS_DIR / f"{company.enterprise_id}.png"
        )
        try:
            await page.screenshot(path=str(screenshot_path), full_page=True)
            logger.info(f"  スクリーンショット保存: {screenshot_path}")
        except Exception as e:
            logger.debug(f"  スクリーンショット保存失敗: {e}")

        # ページ全体のテキストを1回取得して使い回す
        try:
            page_html = await page.content()
            page_text = await page.locator("body").inner_text(timeout=5000)
        except Exception:
            page_html = ""
            page_text = ""

        # ===== Check 2: 企業名表示 =====
        await self._check_company_name(company, page, page_html, result)

        # ===== Check 3: 背景画像 =====
        await self._check_background_image(page, result)

        # ===== Check 4: FAQセクション =====
        self._check_faq(company, page_text, result)

        # ===== Check 5: AIチャット起動+メッセージ送信 =====
        await self._check_ai_chat(company, page, result)

        return result

    async def _check_company_name(
        self,
        company: CompanyInfo,
        page: Page,
        page_html: str,
        result: CheckResult,
    ):
        """企業名 or 企業ID が title / h1〜h3 / 主要ヘッダーに表示されているか"""
        try:
            title = await page.title()
        except Exception:
            title = ""

        # ヘッダー類のテキストを集めて評価
        header_texts = []
        for sel in ["title", "h1", "h2", "h3", "header"]:
            try:
                els = page.locator(sel)
                n = await els.count()
                for i in range(min(n, 10)):
                    try:
                        t = await els.nth(i).inner_text(timeout=1000)
                        if t:
                            header_texts.append(t.strip())
                    except Exception:
                        continue
            except Exception:
                continue
        header_text_combined = " | ".join(header_texts)

        name_hit = bool(company.name and company.name in header_text_combined)
        # 企業IDはURLにも入っているので title だけで判定 (本文全体だと false positive)
        id_hit = bool(
            company.enterprise_id and company.enterprise_id in title
        )

        if name_hit or id_hit:
            note = "企業名一致" if name_hit else "企業ID一致"
            result.set("企業名", True, note)
            logger.info(f"  [OK] 企業名: {note} (title='{title}')")
        else:
            result.set("企業名", False, f"title/見出しに企業名なし: '{title}'")
            logger.warning(
                f"  [NG] 企業名: title/見出しに企業名なし: '{title}'"
            )

    async def _check_background_image(self, page: Page, result: CheckResult):
        """
        背景画像が設定されているか:
            - 何らかの要素に background-image CSS が non-empty で設定
            - or 画面に大きめの <img> がある
        """
        try:
            bg_check = await page.evaluate(
                """
                () => {
                    // 任意の要素で background-image が none 以外なら 1+
                    const all = document.querySelectorAll('*');
                    let bgCount = 0;
                    let bgSample = '';
                    for (const el of all) {
                        const style = getComputedStyle(el);
                        const bg = style.backgroundImage;
                        if (bg && bg !== 'none' && bg !== 'initial') {
                            // url(...) を含むものだけカウント
                            if (bg.indexOf('url(') !== -1) {
                                bgCount += 1;
                                if (!bgSample) bgSample = bg.slice(0, 100);
                            }
                        }
                    }
                    // 大きめの <img> も1枚としてカウント
                    let bigImgCount = 0;
                    for (const img of document.querySelectorAll('img')) {
                        if (img.naturalWidth >= 200 && img.naturalHeight >= 100) {
                            bigImgCount += 1;
                        }
                    }
                    return {bgCount, bigImgCount, bgSample};
                }
                """
            )
        except Exception as e:
            result.skip("背景画像", f"CSS取得失敗: {e}")
            logger.warning(f"  [SKIP] 背景画像: CSS取得失敗 -- {e}")
            return

        bg_count = bg_check.get("bgCount", 0)
        big_img_count = bg_check.get("bigImgCount", 0)
        if bg_count > 0 or big_img_count > 0:
            result.set(
                "背景画像", True,
                f"bg={bg_count}, img={big_img_count}",
            )
            logger.info(
                f"  [OK] 背景画像: bg={bg_count}, 大きめ画像={big_img_count}"
            )
        else:
            result.set("背景画像", False, "背景画像/大きな画像なし")
            logger.warning("  [NG] 背景画像: 背景画像/大きな画像なし")

    def _check_faq(
        self,
        company: CompanyInfo,
        page_text: str,
        result: CheckResult,
    ):
        """FAQ的なコンテンツ + 対象企業名 が本文にあるか"""
        if not page_text:
            result.skip("FAQ", "本文テキスト取得失敗")
            return

        faq_patterns = [
            re.compile(r"FAQ\s*\d+"),
            re.compile(r"Q\s*\d+\s*[:：.\)]"),
            re.compile(r"質問\s*\d+"),
            re.compile(r"よくある質問"),
            re.compile(r"Q&A"),
        ]
        match_count = sum(1 for p in faq_patterns if p.search(page_text))
        company_in_body = (
            (company.name and company.name in page_text)
            or (company.enterprise_id and company.enterprise_id in page_text)
        )

        if match_count >= 1 and company_in_body:
            result.set(
                "FAQ", True,
                f"FAQパターン{match_count}件+企業名一致",
            )
            logger.info(
                f"  [OK] FAQ: パターン {match_count}件 + 企業名一致"
            )
        elif match_count >= 1:
            result.set("FAQ", False, "FAQありだが企業名一致なし")
            logger.warning("  [NG] FAQ: FAQありだが企業名一致なし (他社混入疑い)")
        else:
            result.set("FAQ", False, "FAQパターン未検出")
            logger.warning("  [NG] FAQ: FAQパターン未検出")

    async def _check_ai_chat(
        self,
        company: CompanyInfo,
        page: Page,
        result: CheckResult,
    ):
        """
        AIチャットを起動 → テスト質問送信 → 返信に対象企業名が含まれるか
        """
        # ===== Step 1: チャット起動ボタンを探す =====
        # 一般的なパターン: 「AI面談」「チャット」「始める」「会話」を含むボタン
        start_btn = None
        for keywords in (
            ["AI面談", "始める"],
            ["AI面談"],
            ["チャット", "始める"],
            ["チャット"],
            ["会話", "始める"],
            ["カジュアル", "面談"],
            ["始める"],
        ):
            try:
                buttons = page.locator("button, a")
                btn_count = await buttons.count()
                for i in range(btn_count):
                    text = (await buttons.nth(i).text_content()) or ""
                    t = text.strip()
                    if not t:
                        continue
                    if all(kw in t for kw in keywords):
                        try:
                            if await buttons.nth(i).is_visible():
                                start_btn = buttons.nth(i)
                                break
                        except Exception:
                            continue
                if start_btn is not None:
                    break
            except Exception:
                continue

        if start_btn is None:
            result.skip("AIチャット", "チャット起動ボタンが見つからない")
            logger.warning("  [SKIP] AIチャット: 起動ボタン未検出")
            return

        try:
            btn_text = (await start_btn.text_content() or "").strip()
            logger.info(f"  AIチャット起動ボタンをクリック: [{btn_text}]")
            await start_btn.click()
        except Exception as e:
            result.set("AIチャット", False, f"起動クリック失敗: {e}")
            logger.warning(f"  [NG] AIチャット: 起動クリック失敗 -- {e}")
            return

        await page.wait_for_timeout(3000)

        # ===== Step 2: 入力欄を探す =====
        input_locator = None
        for selector in [
            "textarea",
            "input[type='text']",
            "div[contenteditable='true']",
            "[role='textbox']",
        ]:
            try:
                els = page.locator(selector)
                n = await els.count()
                for i in range(n):
                    try:
                        if await els.nth(i).is_visible():
                            input_locator = els.nth(i)
                            break
                    except Exception:
                        continue
                if input_locator is not None:
                    break
            except Exception:
                continue

        if input_locator is None:
            result.skip("AIチャット", "入力欄が見つからない (起動はOK)")
            logger.warning("  [SKIP] AIチャット: 入力欄未検出")
            return

        # ===== Step 3: 質問送信 =====
        try:
            await input_locator.click()
            await input_locator.fill(QUALITY_CHECK_CHAT_QUESTION)
            logger.info(
                f"  AIチャットに質問入力: 「{QUALITY_CHECK_CHAT_QUESTION}」"
            )
            # 送信: Enter キー or 送信ボタン
            sent = False
            for keywords in (["送信"], ["Send"], ["▶"]):
                try:
                    buttons = page.locator("button")
                    bc = await buttons.count()
                    for i in range(bc):
                        t = (await buttons.nth(i).text_content()) or ""
                        if all(kw in t for kw in keywords):
                            try:
                                if await buttons.nth(i).is_visible():
                                    await buttons.nth(i).click()
                                    sent = True
                                    break
                            except Exception:
                                continue
                    if sent:
                        break
                except Exception:
                    continue
            if not sent:
                await input_locator.press("Enter")
        except Exception as e:
            result.set("AIチャット", False, f"メッセージ送信失敗: {e}")
            logger.warning(f"  [NG] AIチャット: メッセージ送信失敗 -- {e}")
            return

        # ===== Step 4: 返信を待機 =====
        # 返信エリアの DOM 構造は不明なので、ページ全体のテキストを定期取得し、
        # 質問送信後に「企業名 / 企業ID を含む 50文字以上の新規テキスト」が
        # 現れたら返信検出とみなす。
        try:
            initial_text = await page.locator("body").inner_text(timeout=3000)
        except Exception:
            initial_text = ""

        poll_interval = 2000
        elapsed = 0
        timeout_ms = QUALITY_CHECK_CHAT_TIMEOUT
        reply_detected = False
        latest_text = initial_text
        while elapsed < timeout_ms:
            await page.wait_for_timeout(poll_interval)
            elapsed += poll_interval
            try:
                latest_text = await page.locator("body").inner_text(timeout=3000)
            except Exception:
                continue
            diff_text = latest_text[len(initial_text):] if latest_text.startswith(initial_text) else latest_text
            if (
                len(diff_text) >= 50
                and ((company.name and company.name in latest_text)
                     or (company.enterprise_id and company.enterprise_id in latest_text))
            ):
                reply_detected = True
                break

        if reply_detected:
            result.set("AIチャット", True, f"返信に企業名検出 ({elapsed/1000:.0f}s)")
            logger.info(f"  [OK] AIチャット: 返信に企業名検出 ({elapsed/1000:.0f}s)")
        else:
            # 返信は出たが企業名なし or タイムアウト
            diff = latest_text[len(initial_text):] if latest_text.startswith(initial_text) else latest_text
            if len(diff) >= 50:
                result.set(
                    "AIチャット", False,
                    f"返信に企業名なし (他社混入疑い、{elapsed/1000:.0f}s)",
                )
                logger.warning(
                    f"  [NG] AIチャット: 返信に企業名なし ({elapsed/1000:.0f}s)"
                )
            else:
                result.set(
                    "AIチャット", False,
                    f"返信タイムアウト ({timeout_ms/1000}s)",
                )
                logger.warning(
                    f"  [NG] AIチャット: 返信タイムアウト ({timeout_ms/1000}s)"
                )

    def _apply_result(
        self,
        company: CompanyInfo,
        companies: list,
        result: CheckResult,
    ):
        """検証結果を CompanyInfo + CSV に反映。NG なら status をエラーに戻す。"""
        overall = result.overall()
        company.quality_check = overall
        company.quality_detail = result.detail()

        if overall == "OK":
            self.stats["ok"] += 1
            logger.info(f"  [総合] {company.name}: OK")
        elif overall == "部分OK":
            self.stats["partial"] += 1
            logger.warning(f"  [総合] {company.name}: 部分OK")
        elif overall == "NG":
            # NG企業はステータスを「エラー」に戻して Stage ② で再処理させる
            company.status = ProcessStatus.ERROR
            company.error_message = f"品質チェック失敗: {result.detail()}"
            self.stats["ng"] += 1
            logger.warning(
                f"  [総合] {company.name}: NG → ステータスを「エラー」に戻しました"
            )
        else:
            logger.warning(f"  [総合] {company.name}: 判定不能")

        self.sheet_manager.update_company(company, companies)

    def _apply_result_delivery(
        self,
        company: CompanyInfo,
        result: CheckResult,
    ):
        """
        配信CSVモードの結果反映。
        ステータス列がないので status は変更せず、品質チェック結果のみ反映し
        2列+品質列の CSV を書き戻す。
        """
        overall = result.overall()
        company.quality_check = overall
        company.quality_detail = result.detail()

        if overall == "OK":
            self.stats["ok"] += 1
            logger.info(f"  [総合] {company.name}: OK")
        elif overall == "部分OK":
            self.stats["partial"] += 1
            logger.warning(f"  [総合] {company.name}: 部分OK")
        elif overall == "NG":
            self.stats["ng"] += 1
            logger.warning(f"  [総合] {company.name}: NG")
        else:
            logger.warning(f"  [総合] {company.name}: 判定不能")

        # 全件で逐次保存 (50行程度なので毎回でも軽い)
        # CSV へ書き戻し: 元の raw_rows と対象企業リストから再構築
        # 注: run() ループは targets を渡すが、ここでは保持リストから検索する
        self._save_delivery_csv_for_company(company)

    def _read_delivery_csv(self, path: Path) -> list:
        """
        2列CSV (企業名, 納品URL) を読み込み、CompanyInfo リストを返す。

        - "[推定] " プレフィックスは自動除去
        - 既存の「品質チェック」「品質チェック詳細」列があれば読み込む
        - 企業ID は URL 末尾のパスセグメントから抽出 (title 検証で使用)
        - status は便宜上 COMPLETED にしておく (フィルタを通すため)
        """
        if not path.exists():
            raise FileNotFoundError(f"配信CSVが見つかりません: {path}")

        companies = []
        raw_rows = []
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                raw_rows.append(dict(row))
                name = (row.get("企業名") or "").strip()
                url_raw = (row.get("納品URL") or "").strip()
                if not name or not url_raw:
                    continue

                # "[推定] " プレフィックスを剥がす
                url = re.sub(r"^\[推定\]\s*", "", url_raw).strip()

                # URL 末尾のパスセグメントを企業ID として採用
                try:
                    parsed = urlparse(url)
                    ent_id = parsed.path.strip("/").split("/")[-1] if parsed.path else ""
                except Exception:
                    ent_id = ""

                company = CompanyInfo(
                    row_index=i,
                    name=name,
                    enterprise_id=ent_id or CompanyInfo.generate_enterprise_id(name),
                    frontend_app_url=url,
                    status=ProcessStatus.COMPLETED,
                    quality_check=(row.get("品質チェック") or "").strip(),
                    quality_detail=(row.get("品質チェック詳細") or "").strip(),
                )
                companies.append(company)

        self._delivery_raw_rows = raw_rows
        logger.info(f"配信CSV読み込み完了: {len(companies)}社 → {path}")
        return companies

    def _save_delivery_csv_for_company(self, company: CompanyInfo):
        """1社の結果を反映して配信CSVを書き戻す (逐次保存)"""
        if not self.delivery_csv_path:
            return
        # raw_rows の該当行を更新
        for row in self._delivery_raw_rows:
            if (row.get("企業名") or "").strip() == company.name:
                row["品質チェック"] = company.quality_check
                row["品質チェック詳細"] = company.quality_detail
                break
        self._write_delivery_csv()

    def _save_delivery_csv(self, companies: list):
        """全社の結果を反映して配信CSVを書き戻す"""
        if not self.delivery_csv_path:
            return
        name_to_company = {c.name: c for c in companies}
        for row in self._delivery_raw_rows:
            name = (row.get("企業名") or "").strip()
            c = name_to_company.get(name)
            if c:
                row["品質チェック"] = c.quality_check
                row["品質チェック詳細"] = c.quality_detail
        self._write_delivery_csv()

    def _write_delivery_csv(self):
        """配信CSVを 4列スキーマ (企業名, 納品URL, 品質チェック, 品質チェック詳細) で書き戻す"""
        fieldnames = ["企業名", "納品URL", "品質チェック", "品質チェック詳細"]
        with open(self.delivery_csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in self._delivery_raw_rows:
                writer.writerow({
                    "企業名": row.get("企業名", ""),
                    "納品URL": row.get("納品URL", ""),
                    "品質チェック": row.get("品質チェック", ""),
                    "品質チェック詳細": row.get("品質チェック詳細", ""),
                })

    def _print_summary(self, elapsed):
        logger.info("")
        logger.info("=" * 60)
        logger.info("Stage 4: 納品URL品質チェック 完了サマリー")
        logger.info("=" * 60)
        logger.info(f"  対象企業:    {self.stats['total']}社")
        logger.info(f"  [OK]:        {self.stats['ok']}社")
        logger.info(f"  [部分OK]:    {self.stats['partial']}社")
        logger.info(f"  [NG]:        {self.stats['ng']}社 (ステータスをエラーに戻しました)")
        logger.info(f"  [検証例外]:  {self.stats['error']}社")
        if self.stats.get("server_down_aborted", 0) > 0:
            logger.info(
                f"  [サーバーダウン中断]: {self.stats['server_down_aborted']}社 "
                "（品質判定保留。サーバー復旧後に再実行で判定可能）"
            )
        logger.info(f"  所要時間:    {elapsed}")
        logger.info("=" * 60)
        if self.stats["ng"] > 0 or self.stats["error"] > 0:
            logger.info(
                "  ※ NG/例外 企業は status=エラー になっています。"
                "`python orchestrator.py` を再実行すると自動で再処理されます。"
            )
            logger.info("=" * 60)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Stage 4: 納品URLの品質を自動チェックする"
    )
    parser.add_argument(
        "--csv", type=str, default=None,
        help="企業リストCSVファイルのパス",
    )
    parser.add_argument(
        "--headless", action="store_true", default=None,
        help="ヘッドレスモードで実行",
    )
    parser.add_argument(
        "--no-headless", action="store_true",
        help="ブラウザを表示して実行 (デバッグ用)",
    )
    parser.add_argument(
        "--company", type=str, default=None,
        help="特定企業のみテスト実行",
    )
    parser.add_argument(
        "--delivery-csv", type=str, default=None,
        help=(
            "配信CSVモード: 2列CSV (企業名,納品URL) を直接検証する。"
            "company_list.csv のステータスフィルタを無視し、"
            "全行に対して5項目チェックを実行する。"
            "結果は同CSVに「品質チェック」「品質チェック詳細」列を追加して保存。"
        ),
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
    delivery_csv_path = Path(args.delivery_csv) if args.delivery_csv else None

    if csv_path and delivery_csv_path:
        logger.error(
            "--csv と --delivery-csv は同時に指定できません。"
            "どちらか一方を使ってください。"
        )
        return

    verifier = QualityVerifier(
        csv_path=csv_path,
        headless=headless,
        target_company=args.company,
        delivery_csv_path=delivery_csv_path,
    )
    await verifier.run()


if __name__ == "__main__":
    asyncio.run(main())
