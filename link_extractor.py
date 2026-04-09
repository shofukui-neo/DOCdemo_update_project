"""
DOCdemo 自動化フロー — リンク抽出 & スクリーンショットモジュール

企業ホームページURLから：
1. 同一ドメインの内部リンクを抽出
2. トップページのスクリーンショットを撮影
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Optional

from playwright.async_api import async_playwright

from config import SCREENSHOTS_DIR, LINK_CHECK_CONCURRENCY

logger = logging.getLogger(__name__)


async def extract_internal_links_and_screenshot(
    target_url: str,
    screenshot_dir: Optional[str] = None,
) -> dict:
    """
    指定URLのページから内部リンクを抽出し、スクリーンショットを撮影する。

    Args:
        target_url: 解析対象のURL
        screenshot_dir: スクリーンショット保存先ディレクトリ。
                        Noneの場合はconfig.pyのデフォルト。

    Returns:
        dict: {
            "links": list[str] — 抽出された有効な内部リンクのリスト（ソート済み）,
            "screenshot_path": str — 保存したスクリーンショットのファイルパス
        }
    """
    # 1. 準備：ベースドメインの取得
    parsed_base = urlparse(target_url)
    base_domain = parsed_base.netloc
    
    unique_links = set()

    # スクリーンショットの保存先パス生成
    save_dir = Path(screenshot_dir) if screenshot_dir else SCREENSHOTS_DIR
    save_dir.mkdir(parents=True, exist_ok=True)
    
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    # ドメイン名をファイル名に含める
    safe_domain = base_domain.replace(".", "_").replace(":", "_")
    filename = f"shot_{safe_domain}_{timestamp}.png"
    filepath = str(save_dir / filename)

    async with async_playwright() as p:
        # ブラウザの起動 (headless=Trueでバックグラウンド実行)
        browser = await p.chromium.launch(headless=True)
        # スクリーンショット用にビューポートを設定したコンテキストを作成
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            device_scale_factor=1,
        )
        page = await context.new_page()
        
        logger.info(f"解析および画像取得中: {target_url}")
        
        try:
            # 2. ページ遷移
            logger.info("ページを読み込んでいます...")
            await page.goto(target_url, wait_until="load", timeout=60000)
            
            # 動的なアニメーション等の完了を待つため、1秒間待機
            await page.wait_for_timeout(1000)
            
            # 3. スクリーンショット撮影
            logger.info("スクリーンショットを撮影中...")
            await page.screenshot(path=filepath, full_page=False)
            logger.info(f"スクリーンショット保存: {filepath}")

            # 4. すべてのaタグのhref属性を取得
            logger.info("リンクを抽出中...")
            hrefs = await page.eval_on_selector_all(
                "a", "elements => elements.map(el => el.href)"
            )
            
            for href in hrefs:
                if not href:
                    continue
                
                # 5. 正規化とフィルタリング
                clean_url = href.split('#')[0].rstrip('/')
                
                parsed_href = urlparse(clean_url)
                if parsed_href.netloc == base_domain:
                    unique_links.add(clean_url)

            # --- 抽出したリンクの有効性チェック ---
            if unique_links:
                logger.info(
                    f"抽出した {len(unique_links)} 件のリンクの有効性を検証中..."
                )
                request_context = await p.request.new_context(
                    ignore_https_errors=True
                )
                valid_links = set()
                sem = asyncio.Semaphore(LINK_CHECK_CONCURRENCY)

                async def check_link(url):
                    async with sem:
                        try:
                            res = await request_context.get(url, timeout=10000)
                            if res.ok:
                                valid_links.add(url)
                            else:
                                logger.debug(f"[除外] HTTP {res.status}: {url}")
                        except Exception:
                            logger.debug(f"[除外] 到達不能: {url}")

                await asyncio.gather(*(check_link(url) for url in unique_links))
                unique_links = valid_links
                await request_context.dispose()
                    
        except Exception as e:
            logger.error(f"リンク抽出エラー: {e}")
        finally:
            await browser.close()

    # 6. 結果
    sorted_links = sorted(list(unique_links))
    logger.info(f"抽出完了: {len(sorted_links)}件の有効リンク")
    
    if sorted_links:
        for link in sorted_links:
            logger.debug(f"  {link}")

    return {
        "links": sorted_links,
        "screenshot_path": filepath,
    }


if __name__ == "__main__":
    # 使用例（後方互換性のため残す）
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    
    input_url = input("企業のホームページURLを入力してください: ").strip()
    
    if not input_url.startswith("http"):
        print("エラー: URLは 'http://' または 'https://' から始めてください。")
        sys.exit(1)

    if input_url:
        result = asyncio.run(extract_internal_links_and_screenshot(input_url))
        print(f"\nスクリーンショット: {result['screenshot_path']}")
        print(f"リンク数: {len(result['links'])}")
        for link in result['links']:
            print(f"  {link}")
