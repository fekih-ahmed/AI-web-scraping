import re
from typing import Optional
from urllib.parse import urljoin

from playwright.async_api import Page

from models import Product
from utils import extract_number


class ProductParser:
    def __init__(self, base_url: str = "https://www.asos.com"):
        self.base_url = base_url

    async def parse_listing_item(self, page: Page, element_index: int) -> Optional[dict]:
        try:
            product_data = await page.evaluate(f"""
                () => {{
                    const tiles = document.querySelectorAll(
                        '[data-testid="productTile"], article, [data-auto-id="productTile"], .productTile'
                    );
                    const el = tiles[{element_index}];
                    if (!el) return null;

                    const link = el.querySelector('a[href*="/prd/"]');
                    const nameEl = el.querySelector('[data-testid="productDescription"], h3, [class*="title"]');
                    const priceEl = el.querySelector('[data-testid="price"], [class*="price"], .current-price');
                    const imgEl = el.querySelector('img');
                    const brandEl = el.querySelector('[data-testid="brandName"], [class*="brand"]');

                    return {{
                        name: nameEl?.textContent?.trim() || '',
                        url: link?.href || '',
                        price: priceEl?.textContent?.trim() || '',
                        image: imgEl?.src || imgEl?.getAttribute('data-src') || '',
                        brand: brandEl?.textContent?.trim() || '',
                    }};
                }}
            """)
            return product_data
        except Exception:
            return None

    async def parse_product_page(self, page: Page, listing_data: dict) -> Optional[Product]:
        try:
            product_data = await page.evaluate("""
                () => {
                    const data = {};

                    const jsonLd = document.querySelector('script[type="application/ld+json"]');
                    if (jsonLd) {
                        try {
                            const parsed = JSON.parse(jsonLd.textContent);
                            Object.assign(data, parsed);
                        } catch(e) {}
                    }

                    const metaDesc = document.querySelector('meta[name="description"]');
                    if (metaDesc) data.description = metaDesc.content;

                    const priceEl = document.querySelector(
                        '[data-testid="price"], [class*="price"], .current-price, .product-price'
                    );
                    if (priceEl) data.price = priceEl.textContent.trim();

                    const oldPriceEl = document.querySelector(
                        '[data-testid="previousPrice"], [class*="old-price"], .previous-price, s, del'
                    );
                    if (oldPriceEl) data.oldPrice = oldPriceEl.textContent.trim();

                    const discountEl = document.querySelector(
                        '[data-testid="discount"], [class*="discount"], [class*="savings"]'
                    );
                    if (discountEl) data.discount = discountEl.textContent.trim();

                    const ratingEl = document.querySelector(
                        '[data-testid="rating"], [class*="rating"], [itemprop="ratingValue"]'
                    );
                    if (ratingEl) data.rating = parseFloat(ratingEl.textContent.trim()) || null;

                    const reviewsEl = document.querySelector(
                        '[data-testid="reviews"], [class*="reviews"], [itemprop="reviewCount"]'
                    );
                    if (reviewsEl) data.reviews = parseInt(reviewsEl.textContent.trim()) || null;

                    const brandEl = document.querySelector(
                        '[data-testid="brandName"], [class*="brand"], [itemprop="brand"] [itemprop="name"]'
                    );
                    if (brandEl) data.brand = brandEl.textContent.trim();

                    const skuEl = document.querySelector(
                        '[data-testid="productCode"], [class*="sku"], [itemprop="sku"]'
                    );
                    if (skuEl) data.sku = skuEl.textContent.trim();

                    const sizeEls = document.querySelectorAll(
                        '[data-testid="sizeOption"], [class*="size"] button:not([disabled]), .size-selector option'
                    );
                    data.sizes = Array.from(sizeEls)
                        .map(el => el.textContent.trim())
                        .filter(s => s.length > 0);

                    const colorEls = document.querySelectorAll(
                        '[data-testid="colourOption"], [class*="colour"] button, [class*="color"] button'
                    );
                    data.colors = Array.from(colorEls)
                        .map(el => el.getAttribute('aria-label') || el.textContent.trim())
                        .filter(s => s.length > 0);

                    const stockEl = document.querySelector(
                        '[data-testid="stockStatus"], [class*="stock"], [itemprop="availability"]'
                    );
                    if (stockEl) {
                        data.stock = stockEl.textContent.trim();
                        const href = stockEl.getAttribute('href') || '';
                        if (href.includes('InStock')) data.stock = 'In Stock';
                        else if (href.includes('OutOfStock')) data.stock = 'Out of Stock';
                    }

                    const breadcrumbEls = document.querySelectorAll(
                        '[data-testid="breadcrumb"] a, [class*="breadcrumb"] a, nav[aria-label*="breadcrumb"] a'
                    );
                    data.breadcrumb = Array.from(breadcrumbEls)
                        .map(a => a.textContent.trim())
                        .filter(s => s.length > 0)
                        .join(' > ');

                    const imgEl = document.querySelector(
                        '[data-testid="galleryImage"] img, [class*="gallery"] img, [class*="product-image"] img, meta[property="og:image"]'
                    );
                    if (imgEl) {
                        data.image = imgEl.src || imgEl.getAttribute('data-src') || imgEl.content;
                    }

                    return data;
                }
            """)

            name = listing_data.get("name") or product_data.get("name") or await page.title()
            url = listing_data.get("url") or page.url
            price = listing_data.get("price") or product_data.get("price") or product_data.get("offers", {}).get("price", "")
            old_price = product_data.get("oldPrice", "")
            discount = product_data.get("discount", "")
            image = listing_data.get("image") or product_data.get("image", "")

            if product_data.get("offers"):
                offers = product_data["offers"]
                if isinstance(offers, dict):
                    if not price and offers.get("price"):
                        price = str(offers["price"])
                    if offers.get("availability"):
                        stock = offers["availability"]
                        if "InStock" in str(stock):
                            stock = "In Stock"
                        elif "OutOfStock" in str(stock):
                            stock = "Out of Stock"

            if not image:
                image = await page.evaluate("""
                    () => {
                        const og = document.querySelector('meta[property="og:image"]');
                        return og?.content || '';
                    }
                """)

            if not price:
                price_text = await page.evaluate("""
                    () => {
                        const el = document.querySelector('[class*="price"]');
                        return el?.textContent?.trim() || '';
                    }
                """)
                if price_text:
                    price = price_text

            rating = product_data.get("rating")
            if rating is None and product_data.get("aggregateRating"):
                rating = product_data["aggregateRating"].get("ratingValue")

            reviews_count = product_data.get("reviews")
            if reviews_count is None and product_data.get("aggregateRating"):
                reviews_count = product_data["aggregateRating"].get("reviewCount")

            return Product(
                name=str(name)[:500] if name else "",
                url=str(url) if url else page.url,
                price=str(price) if price else None,
                old_price=str(old_price) if old_price else None,
                discount=str(discount) if discount else None,
                rating=extract_number(str(rating)) if rating else None,
                reviews=int(extract_number(str(reviews_count))) if reviews_count else None,
                brand=str(product_data.get("brand", "")) or None,
                sku=str(product_data.get("sku", "")) or None,
                sizes=product_data.get("sizes", []),
                colors=product_data.get("colors", []),
                stock=str(product_data.get("stock", "In Stock")) or "In Stock",
                image=str(image) if image else None,
                description=str(product_data.get("description", "")) or None,
                category="Women's Jeans",
                breadcrumb=str(product_data.get("breadcrumb", "")) or None,
            )

        except Exception as e:
            return None

    async def parse_api_product(self, item: dict) -> Optional[Product]:
        try:
            price_data = item.get("price", {})
            current_price = price_data.get("current", {}).get("text", "")
            previous_price = price_data.get("previous", {}).get("text", "")
            discount_val = None
            if previous_price and current_price:
                try:
                    old_val = float(re.sub(r'[^\d.]', '', previous_price))
                    new_val = float(re.sub(r'[^\d.]', '', current_price))
                    if old_val > 0:
                        discount_val = f"{round((1 - new_val / old_val) * 100)}%"
                except (ValueError, TypeError):
                    pass

            rating_data = item.get("rating", None) or item.get("ratingSummary", {})
            rating_val = None
            reviews_val = None
            if isinstance(rating_data, dict):
                rating_val = rating_data.get("averageRating") or rating_data.get("rating")
                reviews_val = rating_data.get("reviewCount") or rating_data.get("count")
            elif isinstance(rating_data, (int, float)):
                rating_val = rating_data

            brand_data = item.get("brand", {}) or {}
            brand_name = ""
            if isinstance(brand_data, dict):
                brand_name = brand_data.get("name", "")
            else:
                brand_name = str(brand_data)

            product_id = item.get("id")
            product_url = f"https://www.asos.com/prd/{product_id}" if product_id else ""

            image_url = ""
            image_data = item.get("image", {}) or {}
            if isinstance(image_data, dict):
                image_url = image_data.get("url", "")
            elif isinstance(image_data, str):
                image_url = image_data

            name = item.get("name", "")
            if not name:
                return None

            return Product(
                name=name,
                url=product_url,
                price=str(current_price) if current_price else None,
                old_price=str(previous_price) if previous_price else None,
                discount=discount_val,
                rating=float(rating_val) if rating_val else None,
                reviews=int(reviews_val) if reviews_val else None,
                brand=brand_name or None,
                sku=str(product_id) if product_id else None,
                sizes=[],
                colors=[],
                stock="In Stock",
                image=image_url or None,
                description=item.get("description", ""),
                category="Women's Jeans",
                breadcrumb="",
            )

        except Exception:
            return None
