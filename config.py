from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    target_url: str = "https://www.asos.com/us/women/jeans/cat/?cid=3638"
    category_id: str = "3638"
    store: str = "US"
    currency: str = "USD"
    country: str = "US"
    lang: str = "en-US"
    size_schema: str = "US"
    channel: str = "desktop-web"

    headless: bool = True
    slow_mo: int = 50
    timeout: int = 30000
    navigation_timeout: int = 60000

    max_retries: int = 3
    retry_delay: int = 2

    output_file: str = "output/products.json"
    log_file: str = "logs/scraper.log"
    error_log_file: str = "logs/errors.log"

    page_limit: Optional[int] = None
    max_concurrent_pages: int = 5

    block_resources: bool = True
    blocked_resource_types: tuple = (
        "image", "media", "font", "stylesheet",
        "imageset",
    )

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )

    viewport: dict = field(default_factory=lambda: {"width": 1920, "height": 1080})

    proxy: Optional[str] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None

    @property
    def api_search_url(self) -> str:
        return f"https://www.asos.com/api/product/search/v2/categories/{self.category_id}"

    @property
    def api_product_detail_url(self) -> str:
        return "https://www.asos.com/api/product/search/v2"

    @property
    def api_base_params(self) -> dict:
        return {
            "store": self.store,
            "lang": self.lang,
            "currency": self.currency,
            "country": self.country,
            "sizeSchema": self.size_schema,
            "channel": self.channel,
            "limit": 72,
        }
