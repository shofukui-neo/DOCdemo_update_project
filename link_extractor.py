import asyncio
import os
import sys
from datetime import datetime
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright

async def extract_internal_links_and_screenshot(target_url):
    # 1. 準備：ベースドメインの取得
    parsed_base = urlparse(target_url)
    base_domain = parsed_base.netloc
    
    unique_links = set()

    # スクリーンショットの保存先パス生成 (Downloadsフォルダ)
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    filename = f"shot_{timestamp}.png"
    filepath = os.path.join(os.path.expanduser("~"), "Downloads", filename)

    async with async_playwright() as p:
        # ブラウザの起動 (headless=Trueでバックグラウンド実行)
        browser = await p.chromium.launch(headless=True)
        # スクリーンショット用にビューポートを設定したコンテキストを作成
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            device_scale_factor=1,
        )
        page = await context.new_page()
        
        print(f"--- 解析および画像取得中: {target_url} ---")
        
        try:
            # 2. ページ遷移（完了を保証するためloadを指定。networkidleは動画や一部スクリプトでタイムアウトするため非推奨）
            print("ページを読み込んでいます...")
            await page.goto(target_url, wait_until="load", timeout=60000)
            
            # 動的なアニメーション等の完了を待つため、1秒間待機
            await page.wait_for_timeout(1000)
            
            # 3. スクリーンショット撮影
            print("真っ先に見える範囲のスクリーンショットを撮影中...")
            await page.screenshot(path=filepath, full_page=False)
            print(f"成功: スクリーンショットを保存しました。")
            print(f"保存先: {filepath}\n")

            # 4. すべてのaタグのhref属性を取得
            print("リンクを抽出中...")
            hrefs = await page.eval_on_selector_all("a", "elements => elements.map(el => el.href)")
            
            for href in hrefs:
                if not href:
                    continue
                
                # 5. 正規化とフィルタリング
                # フラグメント除去（#section など）
                clean_url = href.split('#')[0].rstrip('/')
                
                # 同一ドメインかチェック
                parsed_href = urlparse(clean_url)
                if parsed_href.netloc == base_domain:
                    unique_links.add(clean_url)

            # --- 抽出したリンクの有効性チェック ---
            if unique_links:
                print(f"抽出した {len(unique_links)} 件のリンクの有効性を検証中...")
                request_context = await p.request.new_context(ignore_https_errors=True)
                valid_links = set()
                sem = asyncio.Semaphore(10) # サーバー負荷を考慮し同時リクエスト数を10に制限

                async def check_link(url):
                    async with sem:
                        try:
                            # タイムアウトを10秒に設定し、レスポンスが200番台かチェック
                            res = await request_context.get(url, timeout=10000)
                            if res.ok:
                                valid_links.add(url)
                            else:
                                print(f"  [除外] HTTP {res.status}: {url}")
                        except Exception:
                            print(f"  [除外] 到達不能またはタイムアウト: {url}")

                # すべてのリンクを並行して検証
                await asyncio.gather(*(check_link(url) for url in unique_links))
                unique_links = valid_links
                await request_context.dispose()
                    
        except Exception as e:
            print(f"エラーが発生しました: {e}")
        finally:
            await browser.close()

    # 6. 結果の出力
    if unique_links:
        print("\n--- 抽出された関連ページURL ---")
        for link in sorted(list(unique_links)):
            print(link)

if __name__ == "__main__":
    # 使用例
    input_url = input("企業のホームページURLを入力してください: ").strip()
    
    # 簡単なURLバリデーション
    if not input_url.startswith("http"):
        print("エラー: URLは 'http://' または 'https://' から始めてください。")
        sys.exit(1)

    if input_url:
        asyncio.run(extract_internal_links_and_screenshot(input_url))
