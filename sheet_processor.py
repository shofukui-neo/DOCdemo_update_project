import asyncio
from playwright.async_api import async_playwright
import gspread
import os
import sys

# 既存の詳細解析コード（link_extractor.py）からインポート
# （もしlink_extractor.pyが異なるファイル名の場合は修正してください）
from link_extractor import extract_internal_links_and_screenshot

# --- 設定 ---
SHEET_NAME = "企業リスト"             # 対象のスプレッドシート名
CREDENTIALS_JSON = "credentials.json" # Google APIサービスアカウントファイルのパス

async def auto_url_finder():
    # 1. Google Sheets 接続
    try:
        print("Google Sheets に接続中...")
        gc = gspread.service_account(filename=CREDENTIALS_JSON)
        sh = gc.open(SHEET_NAME).sheet1
        records = sh.get_all_records()
    except FileNotFoundError:
        print(f"エラー: '{CREDENTIALS_JSON}' が見つかりません。")
        print("Google Cloud Consoleからサービスアカウントのキーを作成・ダウンロードし、このスクリプトと同じフォルダに配置してください。")
        sys.exit(1)
    except Exception as e:
        print(f"スプレッドシートへの接続エラー: {e}")
        print("対象のスプレッドシートがサービスアカウントのEmailアドレスに共有されているか確認してください。")
        sys.exit(1)

    # 2. ブラウザ起動 (Headless=Falseで目視確認可能に)
    async with async_playwright() as p:
        # 管理者の目視確認が入るため headless=False
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("\n=== URL特定と詳細解析の連携処理を開始します ===")
        for i, row in enumerate(records, start=2): # records配列は0始まりだが、スプレッドシートの行はヘッダー（1行目）があるためデータは2行目から
            # 「優先度」が「完了」ではないものを対象
            # ※シートの実際の列名が違う場合は '優先度' の部分を書き換えてください。
            if row.get('優先度') == '完了':
                continue
            
            company_name = row.get('企業名')
            if not company_name:
                continue
            
            print(f"\n--- 処理中 ({i}行目): {company_name} ---")

            # 3. Google検索実行
            search_url = f"https://www.google.com/search?q={company_name}+公式サイト"
            await page.goto(search_url)

            # 4. 【管理者確認】対話モード
            print(">>> Google検索結果を表示しています。")
            input(">>> [アクション待ち] 正しいサイトのリンクをクリックして開いたら、この黒い画面（ターミナル）でEnterキーを押してください...")
            
            # Enterキーが押された時点の開いているURLを取得
            found_url = page.url 
            
            # 5. スプレッドシート更新 (Q列)
            print(f"取得したURL: {found_url}")
            try:
                # 17はQ列を意味します（A=1, B=2 ... Q=17）
                sh.update_cell(i, 17, found_url) 
                print(f"✅ Q列にURLを保存しました。")
                
                # 完了したら優先度も更新しておくと再実行時にスキップされます（例: 3がC列の場合）
                # sh.update_cell(i, 3, "完了") 
            except Exception as e:
                print(f"❌ シートの更新に失敗しました: {e}")

            # 6. 実装済みの詳細解析コードを呼び出し
            print(f"👉 続いて詳細解析（スクショ保存＆内部リンク抽出）を開始します...")
            try:
                # 既存のlink_extractor.pyの関数を直接呼び出して連携
                await extract_internal_links_and_screenshot(found_url)
            except Exception as e:
                print(f"❌ 詳細解析中にエラーが発生しました: {e}")
                
        # ループ終了
        await browser.close()
        print("\n全ての処理が完了しました！")

if __name__ == "__main__":
    asyncio.run(auto_url_finder())
