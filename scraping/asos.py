"""
Anonymous ASOS product scraper (Playwright).

Rules enforced:
- Never logs in, never creates an account, never asks for credentials.
- Fresh, cookie-free browser context every run (no exported storage state).
- Automatically dismisses cookie/marketing/login popups and continues.
- If a page turns out to require authentication, it's skipped and logged
  (never actually happens for ASOS's public category/product pages, but
  the check stays in as a safety net).
- One bad/broken page never stops the whole run.

Flow:
  1. Visit one or more ASOS category URLs (e.g. the women's dresses page).
  2. Auto-discover product URLs from the tiles on that page (ASOS loads
     product tiles into the DOM server-side, so no login/API key needed).
  3. Visit each product page and extract title, price, product code,
     bullet details, brand, and image URLs.
  4. Write results to CSV and log any skipped pages with a reason.

Usage:
    pip install playwright --break-system-packages
    playwright install chromium
    python asos_scraper.py
"""

import csv
import json
import re
import time
from dataclasses import dataclass, asdict, field
from typing import Optional

from playwright.sync_api import (
    sync_playwright,
    Page,
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
)

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

# Category (listing) pages to pull products from. Add/replace with any
# public ASOS "/cat/" URL — dresses, tops, sale, a specific brand, etc.
CATEGORY_URLS = [
    "https://www.asos.com/us/women/dresses/cat/?cid=8799",
]

MAX_PRODUCTS_PER_CATEGORY = 20  # how many product tiles to scrape per category
SCROLL_PASSES = 10               # ASOS lazy-loads tiles on scroll; more passes = more tiles
OUTPUT_CSV = "asos_products.csv"
SKIPPED_LOG = "skipped.log"

# Popups ASOS (and similar sites) commonly show: OneTrust cookie consent,
# newsletter signup modals, and country/region redirect prompts.
POPUP_CLOSE_SELECTORS = [
    "#onetrust-accept-btn-handler",         # OneTrust cookie consent (used by ASOS)
    "button[aria-label='Close']",
    "button[aria-label='close']",
    "[data-testid='close-icon']",
    "[class*='modal'] button[class*='close']",
    "[class*='popup'] button[class*='close']",
    "button:has-text('Accept All Cookies')",
    "button:has-text('Accept')",
    "button:has-text('I Agree')",
    "button:has-text('No thanks')",
    "button:has-text('Not now')",
    "button:has-text('Continue to US site')",
    "button:has-text('Stay on')",
    "[aria-label='Dismiss']",
]

LOGIN_WALL_MARKERS = [
    "input[type='password']",
    "form[action*='login']",
    "text=Sign in to continue",
    "text=Please log in",
]

LOGIN_URL_FRAGMENTS = ["/login", "/signin", "/sign-in", "my.asos.com/my-account"]


@dataclass
class ProductResult:
    url: str
    title: Optional[str] = None
    price: Optional[str] = None
    brand: Optional[str] = None
    product_code: Optional[str] = None
    details: list = field(default_factory=list)
    images: list = field(default_factory=list)


def dismiss_popups(page: Page, attempts: int = 3, timeout_ms: int = 1200) -> None:
    """Best-effort popup dismissal. Never raises if nothing is found."""
    for _ in range(attempts):
        closed_any = False
        for selector in POPUP_CLOSE_SELECTORS:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=timeout_ms):
                    locator.click(timeout=timeout_ms)
                    closed_any = True
                    page.wait_for_timeout(300)
            except Exception:
                continue
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        if not closed_any:
            break


def is_login_walled(page: Page) -> bool:
    current_url = page.url.lower()
    if any(fragment in current_url for fragment in LOGIN_URL_FRAGMENTS):
        return True
    for marker in LOGIN_WALL_MARKERS:
        try:
            if page.locator(marker).first.is_visible(timeout=600):
                return True
        except Exception:
            continue
    return False


def discover_product_urls(page: Page, category_url: str, limit: int) -> list[str]:
    """
    Load a category page and pull product URLs from the tiles. ASOS renders
    tiles server-side and lazy-loads more on scroll, so we scroll a few
    times before collecting links.
    """
    page.goto(category_url, wait_until="domcontentloaded")
    dismiss_popups(page)

    for _ in range(SCROLL_PASSES):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(700)
        dismiss_popups(page)

    tiles = page.evaluate(
        """
        () => {
            const anchors = Array.from(document.querySelectorAll('a[href*="/prd/"]'));
            const seen = new Set();
            const results = [];
            for (const a of anchors) {
                const url = a.href.split('#')[0];
                if (seen.has(url)) continue;
                seen.add(url);
                results.push(url);
            }
            return results;
        }
        """
    )
    return tiles[:limit]


def extract_product(page: Page, url: str) -> ProductResult:
    """
    Extract product fields using structural/text-based matching rather than
    hashed CSS class names (ASOS's class names are auto-generated and
    change between deploys, so this is far more durable).
    """
    data = page.evaluate(
        """
        () => {
            const result = { title: null, price: null, brand: null,
                              productCode: null, details: [], images: [] };

            const h1 = document.querySelector('h1');
            if (h1) result.title = h1.textContent.trim();

            const bodyText = document.body.innerText;

            const priceMatch = bodyText.match(/\\$\\d+(\\.\\d{2})?/);
            if (priceMatch) result.price = priceMatch[0];

            const codeMatch = bodyText.match(/Product Code:\\s*(\\d+)/i);
            if (codeMatch) result.productCode = codeMatch[1];

            // Brand: ASOS links product details to a brand page via
            // /a-to-z-of-brands/ links near the top of the product info.
            const brandLink = Array.from(document.querySelectorAll('a[href*="a-to-z-of-brands"]'))[0];
            if (brandLink) result.brand = brandLink.textContent.trim();

            // "Product Details" section: find the heading, then collect
            // list items in its nearest container.
            const headings = Array.from(document.querySelectorAll('h2, h3, button, summary, dt'));
            const detailsHeading = headings.find(
                el => el.textContent.trim().toLowerCase().startsWith('product details')
            );
            if (detailsHeading) {
                const container = detailsHeading.closest('section') || detailsHeading.parentElement;
                if (container) {
                    result.details = Array.from(container.querySelectorAll('li'))
                        .map(li => li.textContent.trim())
                        .filter(Boolean);
                }
            }

            // Product gallery images
            const imgs = Array.from(
                document.querySelectorAll('img[src*="images.asos-media.com/products/"]')
            );
            result.images = [...new Set(imgs.map(img => img.src.split('?')[0]))];

            return result;
        }
        """
    )

    return ProductResult(
        url=url,
        title=data.get("title"),
        price=data.get("price"),
        brand=data.get("brand"),
        product_code=data.get("productCode"),
        details=data.get("details", []),
        images=data.get("images", []),
    )


def scrape_all() -> tuple[list[ProductResult], list[dict]]:
    results: list[ProductResult] = []
    skipped: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        # Fresh, anonymous context: no storage_state, no cookies, no login.
        context: BrowserContext = browser.new_context(
            viewport={"width": 1280, "height": 1600},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.set_default_timeout(15000)

        product_urls: list[str] = []
        for cat_url in CATEGORY_URLS:
            try:
                found = discover_product_urls(page, cat_url, MAX_PRODUCTS_PER_CATEGORY)
                product_urls.extend(found)
            except Exception as e:
                skipped.append({"url": cat_url, "reason": f"category_load_failed: {e}"})
                continue

        for url in product_urls:
            try:
                page.goto(url, wait_until="domcontentloaded")
            except Exception as e:
                skipped.append({"url": url, "reason": f"navigation_failed: {e}"})
                continue

            dismiss_popups(page)

            if is_login_walled(page):
                skipped.append({"url": url, "reason": "login_required"})
                continue

            try:
                product = extract_product(page, url)
                if not product.title:
                    skipped.append({"url": url, "reason": "no_title_found"})
                    continue
                results.append(product)
            except Exception as e:
                skipped.append({"url": url, "reason": f"extraction_failed: {e}"})
                continue

            time.sleep(0.6)  # polite delay between requests

        context.close()
        browser.close()

    return results, skipped


def save_outputs(results: list[ProductResult], skipped: list[dict]) -> None:
    if results:
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            fieldnames = ["url", "title", "price", "brand", "product_code", "details", "images"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                row = asdict(r)
                row["details"] = " | ".join(row["details"])
                row["images"] = " | ".join(row["images"])
                writer.writerow(row)

    with open(SKIPPED_LOG, "w", encoding="utf-8") as f:
        for entry in skipped:
            f.write(json.dumps(entry) + "\n")

    print(f"Scraped: {len(results)} products -> {OUTPUT_CSV}")
    print(f"Skipped: {len(skipped)} pages -> {SKIPPED_LOG}")


if __name__ == "__main__":
    all_results, all_skipped = scrape_all()
    save_outputs(all_results, all_skipped)