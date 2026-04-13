"""
DOCdemo 自動化フロー — 採用担当者検索モジュール

企業名から採用担当者名、人事責任者名、または広報担当者名を自動で調査する。
Wantedly, PR TIMES, インタビュー記事、SNS等の情報を解析する。
"""

import asyncio
import logging
import re
from urllib.parse import urlparse, quote
from typing import List, Dict, Optional

from playwright.async_api import Page

from config import PAGE_LOAD_TIMEOUT, RETRY_COUNT, RETRY_DELAY

logger = logging.getLogger(__name__)

class RecruiterFinder:
    """採用担当者を自動で特定するクラス"""

    def __init__(self, page: Optional[Page] = None):
        """
        Args:
            page: Playwrightのページインスタンス。
        """
        self._page = page
        self.found_candidates = [] # List[Dict]

    async def find_recruiter_info(self, company_name: str) -> List[Dict]:
        """
        指定された企業の採用担当者情報を調査する。
        
        Args:
            company_name: 企業名
            
        Returns:
            見つかった候補者のリスト [{"name": "...", "title": "...", "source": "...", "url": "..."}]
        """
        self.found_candidates = []
        
        # 1. Wantedly検索
        await self._search_and_process(f"{company_name} Wantedly", "Wantedly")
        
        # 2. PR TIMES検索
        await self._search_and_process(f"{company_name} PR TIMES 採用", "PR TIMES")
        
        # 3. インタビュー・採用広報検索
        await self._search_and_process(f"{company_name} 採用 インタビュー", "Interview")
        
        # 4. 人事・担当者名検索 (Google Dork風クエリ)
        await self._search_and_process(f"{company_name} 採用 担当者名", "General Search")
        
        # 重複排除
        unique_candidates = self._deduplicate(self.found_candidates)
        return unique_candidates

    async def _search_and_process(self, query: str, strategy_name: str):
        """検索を実行し、結果を解析する"""
        logger.info(f"[{strategy_name}] 検索開始: {query}")
        page = self._page
        search_url = f"https://search.yahoo.co.jp/search?p={quote(query)}"

        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
            await page.wait_for_timeout(1500)

            # 検索結果のタイトルとスニペットを取得して即時解析
            results = await page.eval_on_selector_all(
                ".sw-Card",
                """elements => elements.map(el => {
                    const titleEl = el.querySelector(".sw-Card__title a");
                    const snippetEl = el.querySelector(".sw-Card__description, .sw-Card__summary");
                    return {
                        title: titleEl ? titleEl.textContent : "",
                        url: titleEl ? titleEl.href : "",
                        snippet: snippetEl ? snippetEl.textContent : ""
                    };
                })"""
            )

            for res in results:
                if not res["url"] or "yahoo" in res["url"]:
                    continue
                
                # スニペットから名前を抽出
                names = self._extract_names(res["snippet"] + " " + res["title"])
                for name_info in names:
                    self.found_candidates.append({
                        "name": name_info["name"],
                        "title": name_info["title"],
                        "source": strategy_name,
                        "url": res["url"],
                        "context": res["snippet"][:100]
                    })

                # 特定のドメインなら詳細ページへ遷移して深掘り
                if "wantedly.com" in res["url"] and "/projects" not in res["url"]:
                    await self._deep_scrape_wantedly(res["url"])
                elif "prtimes.jp" in res["url"]:
                    await self._deep_scrape_prtimes(res["url"])

        except Exception as e:
            logger.error(f"[{strategy_name}] 検索エラー: {e}")

    async def _deep_scrape_wantedly(self, url: str):
        """Wantedlyの会社ページやメンバーページを解析"""
        try:
            # メンバーページがあればそこへ、なければトップ
            member_url = url.split("?")[0].rstrip("/") + "/members"
            page = self._page
            await page.goto(member_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
            await page.wait_for_timeout(1000)
            
            members = await page.eval_on_selector_all(
                ".member-card, .user-name, [class*='MemberCard']",
                "elements => elements.map(el => el.textContent)"
            )
            for text in members:
                # 役職っぽいキーワードが含まれているかチェック
                if any(k in text for k in ["採用", "人事", "HR", "広報", "CEO", "代表"]):
                    name_info = self._extract_names(text)
                    for n in name_info:
                        self.found_candidates.append({
                            "name": n["name"],
                            "title": n["title"] or "Wantedly Member",
                            "source": "Wantedly Deep",
                            "url": member_url
                        })
        except:
            pass

    async def _deep_scrape_prtimes(self, url: str):
        """PR TIMESのコンタクト情報を解析"""
        try:
            page = self._page
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
            
            # お問い合わせ先セクションを探す
            contact_text = await page.eval_on_selector(
                ".base-contact, .contact-info, body",
                "el => el.innerText"
            )
            # お問い合わせ先キーワード以降を抽出
            if "お問い合わせ先" in contact_text:
                parts = contact_text.split("お問い合わせ先")
                relevant_text = parts[-1][:1000]
                names = self._extract_names(relevant_text)
                for n in names:
                    self.found_candidates.append({
                        "name": n["name"],
                        "title": n["title"] or "PR Contact",
                        "source": "PR TIMES Contact",
                        "url": url
                    })
        except:
            pass

    def _extract_names(self, text: str) -> List[Dict]:
        """テキストから名前と役職を抽出する（簡易版）"""
        results = []
        # 日本人の名前っぽいパターン (例: 担当の田中、人事の佐藤、鈴木 太郎 様)
        # 1. 「[役職]の[名前]」パターン
        patterns = [
            r"(採用担当|人事|広報|責任者|代表)の\s?([一-龠]{2,4})",
            r"(担当)[:：]\s?([一-龠]{2,4})",
            r"([一-龠]{2,4})\s?(様|氏|さん)\s*\(?(採用|人事|広報)",
            r"([一-龠]{2,4})\s?([一-龠]{2,4})\s*(?:（|\()?(採用|人事|代表)"
        ]
        
        for p in patterns:
            matches = re.findall(p, text)
            for m in matches:
                # パターンによってグループの順番が違うので調整
                if "採用" in m[0] or "人事" in m[0] or "広報" in m[0] or "代表" in m[0] or "担当" in m[0]:
                    title = m[0]
                    name = m[1]
                else:
                    name = m[0]
                    title = m[1]
                
                # 名前のバリデーション（2文字以上4文字以下程度）
                if len(name) >= 2 and len(name) <= 6:
                    results.append({"name": name, "title": title})

        # フルネーム（スペースあり）の抽出: [漢字2-3] [漢字2-3]
        fullname_matches = re.findall(r"([一-龠]{2,3})\s([一-龠]{2,3})", text)
        for fm in fullname_matches:
            name = f"{fm[0]} {fm[1]}"
            # 周囲に役職キーワードがあるか
            start_pos = text.find(fm[0])
            context = text[max(0, start_pos-20):min(len(text), start_pos+30)]
            if any(k in context for k in ["採用", "人事", "広報", "代表", "HR", "Manager"]):
                results.append({"name": name, "title": ""})

        return results

    def _deduplicate(self, candidates: List[Dict]) -> List[Dict]:
        """名前で重複を排除"""
        seen = set()
        unique = []
        for c in candidates:
            name_norm = c["name"].replace(" ", "").replace("　", "")
            if name_norm not in seen:
                seen.add(name_norm)
                unique.append(c)
        return unique

async def main():
    """テスト実行用"""
    from playwright.async_api import async_playwright
    import sys

    if len(sys.argv) < 2:
        print("Usage: python recruiter_finder.py [Company Name]")
        return

    company = sys.argv[1]
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        finder = RecruiterFinder(page)
        print(f"--- Searching for {company} ---")
        results = await finder.find_recruiter_info(company)
        
        if not results:
            print("No names found.")
        else:
            for r in results:
                print(f"Name: {r['name']} | Title: {r['title']} | Source: {r['source']}")
        
        await browser.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
