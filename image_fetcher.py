"""
DOCdemo 自動化フロー — 企業画像取得モジュール

企業HPから実際に使用されている画像（ヒーロー画像・OGP画像など）を取得する。
スクリーンショットの代替として使用。

優先順位:
1. OGP (og:image) メタタグの画像（最も代表的）
2. ヒーロー/バナー画像（大きいimg要素）
3. トップページの最初の大きな画像
"""

import asyncio
import logging
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright

from config import SCREENSHOTS_DIR

logger = logging.getLogger(__name__)

# 画像の最小サイズ (px)
MIN_IMAGE_WIDTH = 400
MIN_IMAGE_HEIGHT = 200

# 除外する画像パターン（アイコン、バナー広告など）
EXCLUDE_PATTERNS = [
    "favicon", "icon", "logo", "sprite", "banner_ad", "advertisement",
    "1x1", "pixel", "tracking", "analytics",
]


async def fetch_company_image(
    homepage_url: str,
    company_name: str,
    save_dir: Optional[str] = None,
) -> str:
    """
    企業ホームページから代表的な画像を取得して保存する。

    Args:
        homepage_url: 企業のホームページURL
        company_name: 企業名（ログ・ファイル名用）
        save_dir: 保存先ディレクトリ。Noneの場合はconfigのデフォルト。

    Returns:
        保存した画像ファイルのパス。失敗時は空文字列。
    """
    save_path = Path(save_dir) if save_dir else SCREENSHOTS_DIR
    save_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parsed = urlparse(homepage_url)
    safe_domain = parsed.netloc.replace(".", "_").replace(":", "_")
    
    logger.info(f"企業画像取得開始: {company_name} ({homepage_url})")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        try:
            await page.goto(homepage_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)

            # === 優先度1: OGP画像 (og:image) ===
            ogp_url = await _get_ogp_image(page, homepage_url)
            if ogp_url:
                logger.info(f"  OGP画像を発見: {ogp_url}")
                saved = await _download_image(ogp_url, save_path, f"img_{safe_domain}_{timestamp}.jpg")
                if saved:
                    logger.info(f"  画像保存成功 (OGP): {saved}")
                    await browser.close()
                    return saved

            # === 優先度2: ヒーロー画像（大きなimg要素） ===
            hero_url = await _get_hero_image(page, homepage_url)
            if hero_url:
                logger.info(f"  ヒーロー画像を発見: {hero_url}")
                saved = await _download_image(hero_url, save_path, f"img_{safe_domain}_{timestamp}.jpg")
                if saved:
                    logger.info(f"  画像保存成功 (Hero): {saved}")
                    await browser.close()
                    return saved

            # === 優先度3: CSSの background-image ===
            bg_url = await _get_background_image(page, homepage_url)
            if bg_url:
                logger.info(f"  背景画像を発見: {bg_url}")
                saved = await _download_image(bg_url, save_path, f"img_{safe_domain}_{timestamp}.jpg")
                if saved:
                    logger.info(f"  画像保存成功 (BG): {saved}")
                    await browser.close()
                    return saved

            # === フォールバック: スクリーンショット ===
            logger.warning(f"  適切な画像が見つからないため、スクリーンショットにフォールバック")
            screenshot_path = str(save_path / f"shot_{safe_domain}_{timestamp}.png")
            await page.screenshot(path=screenshot_path, full_page=False)
            logger.info(f"  スクリーンショット保存: {screenshot_path}")
            await browser.close()
            return screenshot_path

        except Exception as e:
            logger.error(f"  画像取得エラー: {company_name} -- {e}")
            try:
                # エラー時もスクリーンショットを試みる
                screenshot_path = str(save_path / f"shot_{safe_domain}_{timestamp}.png")
                await page.screenshot(path=screenshot_path, full_page=False)
                await browser.close()
                return screenshot_path
            except Exception:
                pass
            await browser.close()
            return ""


async def _get_ogp_image(page, base_url: str) -> str:
    """og:imageメタタグから画像URLを取得する"""
    try:
        # og:image タグを探す
        og_image = await page.get_attribute('meta[property="og:image"]', "content")
        if og_image:
            # 相対URLの場合は絶対URLに変換
            abs_url = urljoin(base_url, og_image)
            if _is_valid_image_url(abs_url):
                return abs_url
        
        # twitter:image も試す
        tw_image = await page.get_attribute('meta[name="twitter:image"]', "content")
        if tw_image:
            abs_url = urljoin(base_url, tw_image)
            if _is_valid_image_url(abs_url):
                return abs_url
    except Exception as e:
        logger.debug(f"  OGP画像取得エラー: {e}")
    return ""


async def _get_hero_image(page, base_url: str) -> str:
    """ページ内の最も大きなimg要素の画像URLを取得する"""
    try:
        images = await page.evaluate("""
            () => {
                const imgs = Array.from(document.querySelectorAll('img'));
                return imgs.map(img => ({
                    src: img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '',
                    width: img.naturalWidth || img.width || 0,
                    height: img.naturalHeight || img.height || 0,
                    alt: img.alt || '',
                })).filter(img => img.src && img.src.startsWith('http'));
            }
        """)
        
        # サイズでソート（大きい順）
        valid_images = []
        for img in images:
            src = img.get("src", "")
            width = img.get("width", 0)
            height = img.get("height", 0)
            
            if not _is_valid_image_url(src):
                continue
            if not _is_good_image(src):
                continue
            
            # 面積でスコアリング
            area = width * height
            valid_images.append((area, src))
        
        valid_images.sort(reverse=True)
        
        if valid_images:
            best_url = valid_images[0][1]
            return urljoin(base_url, best_url)
            
    except Exception as e:
        logger.debug(f"  ヒーロー画像取得エラー: {e}")
    return ""


async def _get_background_image(page, base_url: str) -> str:
    """CSSのbackground-imageから画像URLを取得する"""
    try:
        js_code = r"""
            () => {
                const elements = Array.from(document.querySelectorAll('*'));
                const urls = [];
                for (const el of elements) {
                    const style = window.getComputedStyle(el);
                    const bg = style.backgroundImage;
                    if (bg && bg !== 'none' && bg.includes('url')) {
                        const match = bg.match(/url\(["']?([^"']+)["']?\)/);
                        if (match && match[1]) {
                            const rect = el.getBoundingClientRect();
                            urls.push({
                                url: match[1],
                                area: rect.width * rect.height,
                            });
                        }
                    }
                }
                return urls;
            }
        """
        bg_urls = await page.evaluate(js_code)
        
        # エリアでソート
        valid_bgs = []
        for item in bg_urls:
            url = item.get("url", "")
            area = item.get("area", 0)
            if url and url.startswith("http") and _is_valid_image_url(url) and _is_good_image(url):
                valid_bgs.append((area, url))
        
        valid_bgs.sort(reverse=True)
        
        if valid_bgs:
            return valid_bgs[0][1]
            
    except Exception as e:
        logger.debug(f"  背景画像取得エラー: {e}")
    return ""


def _is_valid_image_url(url: str) -> bool:
    """有効な画像URLかどうか確認する"""
    if not url:
        return False
    # 画像拡張子チェック
    lower = url.lower().split("?")[0]
    valid_extensions = (".jpg", ".jpeg", ".png", ".webp", ".gif")
    # 拡張子なしでも許容（CDNパスなど）
    has_image_ext = any(lower.endswith(ext) for ext in valid_extensions)
    has_image_path = any(keyword in lower for keyword in ["image", "photo", "img", "picture", "banner", "hero", "visual", "top"])
    return has_image_ext or has_image_path


def _is_good_image(url: str) -> bool:
    """アイコンや広告画像ではないかを確認する"""
    lower = url.lower()
    for pattern in EXCLUDE_PATTERNS:
        if pattern in lower:
            return False
    return True


async def _download_image(image_url: str, save_dir: Path, filename: str) -> str:
    """
    画像URLをダウンロードして保存する。

    Args:
        image_url: ダウンロードするURL
        save_dir: 保存ディレクトリ
        filename: 保存ファイル名

    Returns:
        保存したファイルパス。失敗時は空文字列。
    """
    save_path = str(save_dir / filename)
    
    try:
        # Playwrightのfetchでダウンロード
        async with async_playwright() as p:
            request_ctx = await p.request.new_context()
            try:
                response = await request_ctx.get(
                    image_url,
                    timeout=15000,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        )
                    },
                )
                if response.ok:
                    body = await response.body()
                    if len(body) > 10000:  # 10KB以上のファイルのみ保存
                        with open(save_path, "wb") as f:
                            f.write(body)
                        logger.debug(f"    ダウンロード成功: {save_path} ({len(body):,} bytes)")
                        return save_path
                    else:
                        logger.debug(f"    ファイルが小さすぎます: {len(body)} bytes")
            finally:
                await request_ctx.dispose()
    except Exception as e:
        logger.debug(f"    ダウンロードエラー: {image_url} -- {e}")
    
    return ""


def extract_enterprise_id_from_url(homepage_url: str) -> str:
    """
    企業のホームページURLからエンタープライズIDを抽出する。

    例:
    - https://www.b-minded.com/ → b-minded
    - https://4976.co.jp/ → 4976
    - https://claynel.jp/ → claynel
    - https://akiyama-group.com/ → akiyama-group

    Args:
        homepage_url: 企業ホームページURL

    Returns:
        エンタープライズID
    """
    if not homepage_url:
        return ""
    
    try:
        parsed = urlparse(homepage_url)
        netloc = parsed.netloc.lower()
        
        # www. を除去
        netloc = re.sub(r'^www\.', '', netloc)
        
        # ポート番号を除去
        netloc = netloc.split(":")[0]
        
        # TLDを除去（.co.jp / .or.jp / .ne.jp / .go.jp / .com / .jp / .net / .org etc.）
        # 日本の複合TLD (.co.jp, .or.jp, .ne.jp, .go.jp, .gr.jp, .lg.jp)
        domain = re.sub(
            r'\.(co|or|ne|go|gr|lg|ed|ac|ad|geo)\.jp$', '', netloc
        )
        # 一般TLD (.com, .jp, .net, .org, .info, .biz, .site, .io, .dev, .app etc.)
        domain = re.sub(
            r'\.(com|jp|net|org|info|biz|site|io|dev|app|ltd|inc|page)$', '', domain
        )
        
        # 特殊文字の正規化（ハイフンは残す）
        domain = re.sub(r'[^\w\-]', '-', domain)
        domain = re.sub(r'-+', '-', domain).strip('-')
        
        return domain if domain else "unknown"
    except Exception as e:
        logger.warning(f"URL解析エラー: {homepage_url} -- {e}")
        return "unknown"


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    
    test_companies = [
        ("ブロードマインド", "https://www.b-minded.com/"),
        ("4976ホールディングス", "https://4976.co.jp/"),
        ("クレーネル", "https://claynel.jp/"),
        ("カークリニックアキヤマ", "https://akiyama-group.com/"),
        ("テラ", "https://terracom.co.jp/"),
    ]
    
    async def main():
        for name, url in test_companies:
            enterprise_id = extract_enterprise_id_from_url(url)
            print(f"{name}: {url} → ID: {enterprise_id}")
            path = await fetch_company_image(url, name)
            print(f"  画像: {path}\n")
    
    asyncio.run(main())
