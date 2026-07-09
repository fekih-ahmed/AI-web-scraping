import json
import re
import sys

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://sandbox.oxylabs.io/products"
OUTPUT_FILE = "products.json"
NAV_TIMEOUT_MS = 20000


def find_last_page(page) -> int:
    """Inspect pagination links to figure out how many pages exist."""
    hrefs = page.eval_on_selector_all(
        "a[href*='page=']", "els => els.map(e => e.getAttribute('href'))"
    )
    last_page = 1
    for href in hrefs:
        match = re.search(r"page=(\d+)", href or "")
        if match:
            last_page = max(last_page, int(match.group(1)))
    return last_page


def extract_products(page):
    """Pull title/price/rating for every product card on the current page."""
    cards = page.query_selector_all(".product-card")
    if not cards:
        cards = page.query_selector_all("a[href*='/products/']")

    products = []
    for card in cards:
        title = ""
        for sel in ["h4", "h3", ".title", "a[href*='/products/']"]:
            el = card.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if text:
                    title = text
                    break
        if not title:
            continue

        price = ""
        el = card.query_selector(".price-wrapper") or card.query_selector(".price")
        if el:
            price = el.inner_text().strip()
        if not price:
            text = card.inner_text()
            match = re.search(r"[\$€£]\s?\d+[.,]\d{2}|\d+[.,]\d{2}\s?[\$€£]", text)
            price = match.group(0) if match else ""

        rating = None
        stars = card.query_selector_all(
            ".star-rating .glyphicon-star, .rating .glyphicon-star, "
            ".star-rating span, .rating span"
        )
        if stars:
            filled = [
                s for s in stars
                if "empty" not in (s.get_attribute("class") or "").lower()
                and "off" not in (s.get_attribute("class") or "").lower()
            ]
            rating = len(filled) if filled else len(stars)
        else:
            rating_el = card.query_selector("[data-rating]")
            if rating_el:
                try:
                    rating = float(rating_el.get_attribute("data-rating"))
                except (TypeError, ValueError):
                    rating = None
            if rating is None:
                text = card.inner_text()
                match = re.search(r"(\d(?:\.\d)?)\s*(?:/|out of)\s*5", text)
                if match:
                    rating = float(match.group(1))

        products.append({"title": title, "price": price, "rating": rating})

    return products


def main():
    all_products = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.set_default_navigation_timeout(NAV_TIMEOUT_MS)

        print(f"Fetching first page: {BASE_URL}")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_selector(".product-card, a[href*='/products/']")

        last_page = find_last_page(page)
        print(f"Detected {last_page} page(s) of products.")

        all_products.extend(extract_products(page))

        for page_num in range(2, last_page + 1):
            url = f"{BASE_URL}?page={page_num}"
            print(f"Fetching page {page_num}/{last_page}...")
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_selector(".product-card, a[href*='/products/']")
            except PlaywrightTimeoutError as exc:
                print(f"  Warning: failed to load page {page_num}: {exc}", file=sys.stderr)
                continue

            all_products.extend(extract_products(page))

        browser.close()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_products, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(all_products)} products to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()