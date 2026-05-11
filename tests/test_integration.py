"""
統合テスト: 各モジュールの実動作テスト

外部接続を伴うテストを含むため、個別に実行する。
"""

import asyncio
import logging
import sys
import os

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows cp932 対策
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from config import SCREENSHOTS_DIR, DATA_DIR, LOGS_DIR, COMPANY_LIST_CSV
from models import CompanyInfo, ProcessStatus
from spreadsheet_manager import SpreadsheetManager


def test_1_directory_structure():
    """テスト1: ディレクトリ構成の確認"""
    print("\n" + "=" * 60)
    print("テスト1: ディレクトリ構成の確認")
    print("=" * 60)
    
    results = []
    
    # data ディレクトリ
    exists = DATA_DIR.exists()
    results.append(("data/", exists))
    print(f"  {'✅' if exists else '❌'} data/ ディレクトリ: {DATA_DIR}")
    
    # screenshots ディレクトリ
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    exists = SCREENSHOTS_DIR.exists()
    results.append(("screenshots/", exists))
    print(f"  {'✅' if exists else '❌'} screenshots/ ディレクトリ: {SCREENSHOTS_DIR}")
    
    # logs ディレクトリ
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    exists = LOGS_DIR.exists()
    results.append(("logs/", exists))
    print(f"  {'✅' if exists else '❌'} logs/ ディレクトリ: {LOGS_DIR}")
    
    # company_list.csv
    exists = COMPANY_LIST_CSV.exists()
    results.append(("company_list.csv", exists))
    print(f"  {'✅' if exists else '❌'} company_list.csv: {COMPANY_LIST_CSV}")
    
    all_pass = all(r[1] for r in results)
    print(f"\n  結果: {'✅ ALL PASS' if all_pass else '❌ FAIL'}")
    return all_pass


def test_2_csv_read():
    """テスト2: CSVファイル読み込みテスト"""
    print("\n" + "=" * 60)
    print("テスト2: CSVファイル読み込みテスト")
    print("=" * 60)
    
    try:
        manager = SpreadsheetManager()
        companies = manager.read_company_list()
        print(f"  ✅ 企業リスト読み込み成功: {len(companies)}社")
        
        # 最初の5社を表示
        print(f"  --- 先頭5社 ---")
        for c in companies[:5]:
            print(f"    {c.name} (ID: {c.enterprise_id}, Status: {c.status.value})")
        
        # ステータス別集計
        status_counts = {}
        for c in companies:
            status_counts[c.status.value] = status_counts.get(c.status.value, 0) + 1
        print(f"  --- ステータス集計 ---")
        for status, count in sorted(status_counts.items()):
            print(f"    {status}: {count}社")
        
        # 処理対象の確認
        pending = manager.get_pending_companies(companies)
        print(f"  ✅ 処理対象: {len(pending)}社")
        
        return True
    except Exception as e:
        print(f"  ❌ エラー: {e}")
        return False


def test_3_enterprise_id_generation():
    """テスト3: 企業ID自動生成テスト"""
    print("\n" + "=" * 60)
    print("テスト3: 企業ID自動生成テスト")
    print("=" * 60)
    
    test_cases = [
        ("one-hat株式会社", "one-hat"),
        ("株式会社Felnis", "felnis"),
        ("伊勢住宅株式会社", "伊勢住宅"),
        ("医療法人社団日生会", "日生会"),
        ("一般社団法人新経済連盟", "新経済連盟"),
        ("AGC株式会社", "agc"),
        ("株式会社Select Buddy", "select-buddy"),
        ("KPMG税理士法人", "kpmg"),
        ("T-NEXT株式会社", "t-next"),
        ("岩泉町", "岩泉町"),
    ]
    
    all_pass = True
    for name, expected_id in test_cases:
        c = CompanyInfo(row_index=0, name=name)
        result = c.enterprise_id
        passed = result == expected_id
        if not passed:
            all_pass = False
        print(f"  {'✅' if passed else '❌'} {name} → {result} (期待: {expected_id})")
    
    print(f"\n  結果: {'✅ ALL PASS' if all_pass else '❌ FAIL'}")
    return all_pass


def test_4_process_status_flow():
    """テスト4: ステータス遷移テスト"""
    print("\n" + "=" * 60)
    print("テスト4: ステータス遷移テスト")
    print("=" * 60)
    
    c = CompanyInfo(row_index=0, name="テスト株式会社")
    all_pass = True
    
    # 初期状態
    passed = c.status == ProcessStatus.PENDING
    print(f"  {'✅' if passed else '❌'} 初期状態: {c.status.value}")
    all_pass = all_pass and passed
    
    # PENDING → is_processable
    passed = c.is_processable() == True
    print(f"  {'✅' if passed else '❌'} PENDING → is_processable: {c.is_processable()}")
    all_pass = all_pass and passed
    
    # URL_FOUND
    c.status = ProcessStatus.URL_FOUND
    passed = c.is_processable() == True
    print(f"  {'✅' if passed else '❌'} URL_FOUND → is_processable: {c.is_processable()}")
    all_pass = all_pass and passed
    
    # COMPLETED
    c.status = ProcessStatus.COMPLETED
    passed = c.is_processable() == False
    print(f"  {'✅' if passed else '❌'} COMPLETED → is_processable: {c.is_processable()}")
    all_pass = all_pass and passed
    
    # ERROR
    c2 = CompanyInfo(row_index=1, name="テスト2")
    c2.mark_error("テストエラー")
    passed = c2.status == ProcessStatus.ERROR and c2.error_message == "テストエラー"
    print(f"  {'✅' if passed else '❌'} mark_error: status={c2.status.value}, msg={c2.error_message}")
    all_pass = all_pass and passed
    
    # SKIPPED
    c3 = CompanyInfo(row_index=2, name="テスト3")
    c3.mark_skipped("URL不明")
    passed = c3.status == ProcessStatus.SKIPPED and c3.error_message == "URL不明"
    print(f"  {'✅' if passed else '❌'} mark_skipped: status={c3.status.value}, msg={c3.error_message}")
    all_pass = all_pass and passed
    
    print(f"\n  結果: {'✅ ALL PASS' if all_pass else '❌ FAIL'}")
    return all_pass


async def test_5_link_extractor():
    """テスト5: リンク抽出の実動作テスト（ネオキャリア公式サイト）"""
    print("\n" + "=" * 60)
    print("テスト5: リンク抽出 + スクリーンショット 実動作テスト")
    print("=" * 60)
    
    from link_extractor import extract_internal_links_and_screenshot
    
    target_url = "https://www.neocareer.co.jp"
    print(f"  対象URL: {target_url}")
    print(f"  処理中...")
    
    try:
        result = await extract_internal_links_and_screenshot(target_url)
        
        # 結果の検証
        has_links = len(result["links"]) > 0
        has_screenshot = os.path.exists(result["screenshot_path"])
        has_csv = result["csv_path"] is not None and os.path.exists(result["csv_path"])
        
        print(f"  {'✅' if has_links else '❌'} リンク抽出: {result['total_links']}件")
        print(f"  {'✅' if has_screenshot else '❌'} スクリーンショット: {result['screenshot_path']}")
        print(f"  {'✅' if has_csv else '⚠️'} CSV保存: {result['csv_path']}")
        
        if result["links"]:
            print(f"  --- 先頭5リンク ---")
            for link in result["links"][:5]:
                print(f"    {link}")
        
        # スクリーンショットファイルサイズ確認
        if has_screenshot:
            size_kb = os.path.getsize(result["screenshot_path"]) / 1024
            print(f"  スクリーンショットサイズ: {size_kb:.1f} KB")
        
        all_pass = has_links and has_screenshot
        print(f"\n  結果: {'✅ PASS' if all_pass else '❌ FAIL'}")
        return all_pass
        
    except Exception as e:
        print(f"  ❌ エラー: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_6_web_app_connectivity():
    """テスト6: Webアプリへの接続テスト"""
    print("\n" + "=" * 60)
    print("テスト6: Webアプリ接続テスト")
    print("=" * 60)
    
    from playwright.async_api import async_playwright
    from config import WEB_APP_BASE_URL
    
    print(f"  対象URL: {WEB_APP_BASE_URL}")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            response = await page.goto(
                WEB_APP_BASE_URL,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            
            status = response.status if response else "N/A"
            title = await page.title()
            url = page.url
            
            passed = response is not None and response.ok
            print(f"  {'✅' if passed else '❌'} HTTP Status: {status}")
            print(f"  ページタイトル: {title}")
            print(f"  最終URL: {url}")
            
            # ログインフォームの存在確認
            email_input = page.locator('input[aria-label="メールアドレス"]')
            email_exists = await email_input.count() > 0
            print(f"  {'✅' if email_exists else '❌'} ログインフォーム(メールアドレス): {'検出' if email_exists else '未検出'}")
            
            pw_input = page.locator('input[aria-label="パスワード"]')
            pw_exists = await pw_input.count() > 0
            print(f"  {'✅' if pw_exists else '❌'} ログインフォーム(パスワード): {'検出' if pw_exists else '未検出'}")
            
            login_btn = page.locator('button:has-text("ログイン")')
            btn_exists = await login_btn.count() > 0
            print(f"  {'✅' if btn_exists else '❌'} ログインボタン: {'検出' if btn_exists else '未検出'}")
            
            await browser.close()
            
            all_pass = passed and email_exists and pw_exists and btn_exists
            print(f"\n  結果: {'✅ PASS' if all_pass else '❌ FAIL'}")
            return all_pass
            
    except Exception as e:
        print(f"  ❌ 接続エラー: {e}")
        import traceback
        traceback.print_exc()
        return False


async def run_all_tests():
    """全テストを実行"""
    print("=" * 60)
    print("DOCdemo 自動化フロー -- 動作テスト")
    print("=" * 60)
    
    results = {}
    
    # テスト1-4: 同期テスト
    results["1. ディレクトリ構成"] = test_1_directory_structure()
    results["2. CSV読み込み"] = test_2_csv_read()
    results["3. 企業ID生成"] = test_3_enterprise_id_generation()
    results["4. ステータス遷移"] = test_4_process_status_flow()
    
    # テスト5: リンク抽出（非同期・外部接続あり）
    results["5. リンク抽出"] = await test_5_link_extractor()
    
    # テスト6: Webアプリ接続（非同期・外部接続あり）
    results["6. Webアプリ接続"] = await test_6_web_app_connectivity()
    
    # 最終サマリー
    print("\n" + "=" * 60)
    print("テスト結果サマリー")
    print("=" * 60)
    
    passed = 0
    failed = 0
    for test_name, result in results.items():
        icon = "✅" if result else "❌"
        status_text = "PASS" if result else "FAIL"
        print(f"  {icon} {test_name}: {status_text}")
        if result:
            passed += 1
        else:
            failed += 1
    
    print(f"\n  合計: {passed + failed}テスト, {passed}成功, {failed}失敗")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
