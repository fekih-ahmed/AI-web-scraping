import asyncio
import json
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from playwright.async_api import Page, Route, Request


def is_valid_product_url(url: str, base_domain: str = "asos.com") -> bool:
    try:
        parsed = urlparse(url)
        return (
            base_domain in parsed.netloc
            and "/prd/" in parsed.path
        )
    except Exception:
        return False


def extract_product_id(url: str) -> Optional[str]:
    import re
    match = re.search(r'/prd/(\d+)', url)
    return match.group(1) if match else None


def ensure_dir(path: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save_json(data: list[dict], filepath: str) -> None:
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_json(filepath: str) -> list[dict]:
    path = Path(filepath)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def build_product_url(product_id: str | int) -> str:
    return f"https://www.asos.com/prd/{product_id}"


def clean_price(text: str) -> str:
    if not text:
        return ""
    return text.replace("\u00a0", " ").replace("\u20ac", "€").replace("\u00a3", "£").strip()


def extract_number(text: str) -> Optional[float]:
    if not text:
        return None
    import re
    numbers = re.findall(r"[\d.]+", text.replace(",", ""))
    if numbers:
        return float(numbers[0])
    return None


async def block_resources(route: Route, request: Request, blocked_types: tuple) -> None:
    if request.resource_type in blocked_types:
        await route.abort()
    else:
        await route.continue_()


async def configure_page(page: Page, config: Any) -> None:
    await page.add_init_script("""
        () => {
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
        }
    """)

    if config.block_resources:
        await page.route(
            "**/*",
            lambda route, request: block_resources(
                route, request, config.blocked_resource_types
            ),
        )


class ProgressTracker:
    def __init__(self, total: int = 0):
        self.total = total
        self.done = 0
        self.errors = 0

    def set_total(self, total: int) -> None:
        self.total = total

    def increment(self, n: int = 1) -> None:
        self.done += n

    def add_error(self) -> None:
        self.errors += 1

    def __str__(self) -> str:
        return f"Scraped: {self.done}/{self.total} | Errors: {self.errors}"
