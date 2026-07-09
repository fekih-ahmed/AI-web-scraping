#!/usr/bin/env python3
"""
================================================================================
 TikTok Public Profile & Follower Scraper (login-free, single file)
================================================================================

WHAT THIS TOOL DOES
--------------------
Given a TikTok profile URL, this tool:
  1. Detects whether the account exists.
  2. Detects whether the account is private -> prints "This is a private
     account." and exits.
  3. If public, extracts the profile's own public metadata (username,
     display name, avatar URL, verified flag, bio, follower/following/like
     counts).
  4. Attempts to discover the account's *followers* the same way a logged
     out visitor would: by clicking the "Followers" stat and scrolling the
     resulting panel.

IMPORTANT / READ THIS -- A REAL PLATFORM LIMITATION, NOT A BUG
----------------------------------------------------------------
TikTok's web client does not expose a follower LIST to signed-out visitors.
Whether an account is "public" only controls whether its profile info and
videos are visible without logging in -- the follower/following list itself
is gated behind an authenticated session for every account, public or not.
A logged-out click on "Followers" opens a login prompt, never a list.

This script still implements the click -> wait -> scroll -> collect pipeline
in `attempt_follower_discovery()` for real, so it will keep working with NO
CODE CHANGES if TikTok ever changes this behavior. Under TikTok's current
behavior, that function will correctly detect the login wall, log a clear
warning, and report zero accessible followers. This tool intentionally does
NOT fall back to TikTok's internal signed/authenticated APIs to work around
that wall -- doing so would mean reverse engineering TikTok's auth/signature
scheme (X-Bogus/msToken) to bypass an access control, which this project
explicitly must never do. The profile-level data (which TikTok truly does
serve to anonymous visitors) is still fully and honestly extracted below.

INSTALL
-------
    pip install playwright aiohttp beautifulsoup4 lxml orjson pandas fake-useragent tenacity rich
    playwright install chromium

RUN
---
    python tiktok_scraper.py
    python tiktok_scraper.py https://www.tiktok.com/@some_username

OUTPUT
------
    output/followers.csv
    output/followers.json
(Currently these will contain the target profile itself, and any follower
rows the pipeline above manages to collect -- which, per the note above, is
expected to be none while TikTok keeps follower lists behind a login wall.)

================================================================================
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import signal
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Optional

import aiohttp
import orjson
import pandas as pd
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
)
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

# ------------------------------------------------------------------------- #
# Configuration constants
# ------------------------------------------------------------------------- #

OUTPUT_DIR = Path("output")
CSV_PATH = OUTPUT_DIR / "followers.csv"
JSON_PATH = OUTPUT_DIR / "followers.json"

PAGE_LOAD_TIMEOUT_MS = 30_000
SELECTOR_TIMEOUT_MS = 8_000
MAX_NETWORK_RETRIES = 4
MAX_SCROLL_ATTEMPTS_WITHOUT_NEW_ITEMS = 3
MAX_CONCURRENT_PROFILE_FETCHES = 5

MIN_DELAY_SECONDS = 0.8
MAX_DELAY_SECONDS = 2.4

FALLBACK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# CSV column order, matches the spec exactly.
CSV_COLUMNS = [
    "username",
    "display_name",
    "profile_url",
    "profile_photo_url",
    "verified",
    "bio",
    "followers_count",
    "following_count",
    "likes_count",
]

# ------------------------------------------------------------------------- #
# Logging (colored, via Rich)
# ------------------------------------------------------------------------- #

console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
)
logger = logging.getLogger("tiktok_scraper")


# ------------------------------------------------------------------------- #
# Data model
# ------------------------------------------------------------------------- #


@dataclass
class TikTokProfile:
    """Represents one publicly discoverable TikTok profile.

    All fields are best-effort: TikTok allows creators to hide follower
    count, following count, and like count independently, so those three
    are Optional and left as None when not present/parseable.
    """

    username: str
    display_name: str = ""
    profile_url: str = ""
    profile_photo_url: str = ""
    verified: bool = False
    bio: str = ""
    followers_count: Optional[int] = None
    following_count: Optional[int] = None
    likes_count: Optional[int] = None

    def to_row(self) -> dict[str, Any]:
        """Return a plain dict matching the required CSV column order."""
        d = asdict(self)
        return {col: d.get(col) for col in CSV_COLUMNS}


@dataclass
class ScrapeStats:
    """Bookkeeping for the final run summary."""

    discovered: int = 0
    saved: int = 0
    failed: int = 0
    start_time: float = field(default_factory=time.monotonic)

    def elapsed(self) -> float:
        return time.monotonic() - self.start_time


class ShutdownRequested(Exception):
    """Raised internally to unwind cleanly on Ctrl+C / SIGTERM."""


# ------------------------------------------------------------------------- #
# Small utilities
# ------------------------------------------------------------------------- #


def get_user_agent() -> str:
    """Return a randomized, realistic desktop user agent string.

    Falls back to a hardcoded modern Chrome UA if fake-useragent's data
    file can't be loaded (e.g. no network access to refresh its cache).
    """
    try:
        return UserAgent().random
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.debug("fake_useragent unavailable (%s); using fallback UA.", exc)
        return FALLBACK_USER_AGENT


async def polite_delay(
    min_seconds: float = MIN_DELAY_SECONDS, max_seconds: float = MAX_DELAY_SECONDS
) -> None:
    """Sleep a small randomized amount to avoid hammering the site."""
    await asyncio.sleep(random.uniform(min_seconds, max_seconds))


def normalize_profile_url(raw_url: str) -> tuple[str, str]:
    """Validate and normalize a user-supplied TikTok profile URL.

    Accepts forms like:
        https://www.tiktok.com/@username
        tiktok.com/@username
        @username
        username

    Returns:
        (username, canonical_profile_url)

    Raises:
        ValueError if no valid username can be extracted.
    """
    raw_url = raw_url.strip()
    match = re.search(r"@([\w.\-]{1,64})", raw_url)
    if not match:
        # Allow a bare username with no @ and no domain.
        match = re.fullmatch(r"[\w.\-]{1,64}", raw_url)
        if not match:
            raise ValueError(
                f"Could not parse a TikTok username out of: {raw_url!r}"
            )
        username = match.group(0)
    else:
        username = match.group(1)

    canonical_url = f"https://www.tiktok.com/@{username}"
    return username, canonical_url


def parse_count(value: Any) -> Optional[int]:
    """Best-effort conversion of TikTok's count fields (ints or numeric strings) to int."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------- #
# Network layer (aiohttp) -- used for the public oEmbed fallback endpoint
# ------------------------------------------------------------------------- #


@retry(
    reraise=True,
    stop=stop_after_attempt(MAX_NETWORK_RETRIES),
    wait=wait_random_exponential(multiplier=1, max=10),
    retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def fetch_oembed(session: aiohttp.ClientSession, profile_url: str) -> Optional[dict]:
    """Fetch TikTok's public oEmbed metadata for a profile URL.

    oEmbed is a standard, intentionally-public embed API (no login, no
    signed tokens) that TikTok exposes for generating link previews. It is
    used here only as a *fallback* enrichment source if the embedded page
    JSON can't be parsed -- never as a way to reach gated data.
    """
    endpoint = "https://www.tiktok.com/oembed"
    try:
        async with session.get(
            endpoint,
            params={"url": profile_url},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                logger.debug("oEmbed returned status %s for %s", resp.status, profile_url)
                return None
            raw = await resp.read()
            return orjson.loads(raw)
    except orjson.JSONDecodeError:
        return None


# ------------------------------------------------------------------------- #
# Browser layer (Playwright)
# ------------------------------------------------------------------------- #


async def launch_browser(playwright) -> Browser:
    """Launch a headless Chromium instance."""
    return await playwright.chromium.launch(
        headless=False,
        args=["--disable-dev-shm-usage"],
    )


async def new_stealthy_context(browser: Browser, user_agent: str) -> BrowserContext:
    """Create a browser context with a realistic desktop fingerprint.

    Note: this only sets a normal user agent, locale, and viewport -- it
    does not attempt to defeat bot-detection or fingerprinting systems.
    """
    return await browser.new_context(
        user_agent=user_agent,
        viewport={"width": 1366, "height": 900},
        locale="en-US",
    )


@retry(
    reraise=True,
    stop=stop_after_attempt(MAX_NETWORK_RETRIES),
    wait=wait_random_exponential(multiplier=1, max=8),
    retry=retry_if_exception_type((PlaywrightTimeoutError, PlaywrightError)),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def load_page_html(page: Page, url: str) -> tuple[str, Optional[int]]:
    """Navigate to a URL and return (rendered_html, http_status_code).

    The HTTP status is a strong, independent signal (a genuine 404 always
    means "not found") that complements the content-based detection below.
    """
    response = await page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
    # Give TikTok's client-side hydration a moment to populate the DOM/JSON.
    try:
        await page.wait_for_load_state("networkidle", timeout=SELECTOR_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        # Not fatal -- some pages never truly go idle due to background pings.
        pass
    html = await page.content()
    return html, (response.status if response else None)


# ------------------------------------------------------------------------- #
# Parsing: account status detection
# ------------------------------------------------------------------------- #


def _visible_text(html: str) -> str:
    """Return the page's rendered text only, with <script>/<style> removed.

    TikTok's raw HTML also contains large embedded JSON blobs (i18n string
    tables, pre-loaded component templates, etc.) that include phrases like
    "Couldn't find this account" or "This account is private" as *possible*
    UI copy -- present on every page load regardless of whether that state
    is actually being shown. Searching the *raw* HTML for these phrases (as
    an earlier version of this script did) produces false positives on
    perfectly real, public profiles. Stripping script/style content first
    and only searching genuinely rendered text fixes that.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(" ", strip=True).lower()


def _dump_debug_html(html: str, username: str, label: str) -> Path:
    """Save raw page HTML to output/debug/ for post-mortem inspection.

    Written whenever the status resolves to 'not_found' or 'private', so
    if that classification is ever wrong, it's easy to open the file and
    see exactly what TikTok served (a genuine 404 page, a private-account
    page, an unrelated error/verification page, etc.) instead of guessing.
    """
    debug_dir = OUTPUT_DIR / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    path = debug_dir / f"{username}_{label}.html"
    path.write_text(html, encoding="utf-8")
    return path


def detect_account_status(
    html: str, username: str, http_status: Optional[int] = None
) -> str:
    """Classify a loaded profile page as 'not_found', 'private', or 'public'.

    Detection order, most to least reliable:
      1. A genuine HTTP 404 response -> not_found.
      2. A successfully parsed user object in TikTok's own embedded page
         JSON -> public. This is ground truth: if TikTok's client actually
         hydrated real user data for this username, the account
         unquestionably exists and is public, regardless of any unrelated
         boilerplate strings elsewhere in the page's HTML/JS.
      3. Rendered *visible* text only (script/style stripped) checked
         against TikTok's known private/not-found copy.
      4. Default to "public" and let the extraction fallbacks (meta tags,
         oEmbed) decide -- it's better to attempt extraction and fail
         cleanly than to wrongly report a real account as missing.
    """
    if http_status == 404:
        return "not_found"

    data = _extract_embedded_json(html)
    if data is not None:
        profile = _parse_universal_data(data, username) or _parse_sigi_state(data, username)
        if profile is not None:
            return "public"

    visible = _visible_text(html)
    if "couldn't find this account" in visible or "user not found" in visible:
        _dump_debug_html(html, username, "not_found")
        return "not_found"
    if "this account is private" in visible:
        _dump_debug_html(html, username, "private")
        return "private"

    return "public"


# ------------------------------------------------------------------------- #
# Parsing: profile JSON extraction (three fallback tiers)
# ------------------------------------------------------------------------- #


_UNIVERSAL_DATA_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_SIGI_STATE_RE = re.compile(
    r'<script id="SIGI_STATE"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def _extract_embedded_json(html: str) -> Optional[dict]:
    """Pull TikTok's server-embedded state JSON out of the page's own HTML.

    TikTok server-renders a `<script>` tag containing the exact data used
    to hydrate the page (this is standard, publicly-served page content --
    the same JSON any browser downloads just by loading the page, no
    login or extra request required). Two known tag ids are tried since
    TikTok has used both across web client versions.
    """
    for pattern in (_UNIVERSAL_DATA_RE, _SIGI_STATE_RE):
        match = pattern.search(html)
        if not match:
            continue
        try:
            return orjson.loads(match.group(1))
        except orjson.JSONDecodeError:
            logger.debug("Found embedded script tag but could not parse JSON.")
            continue
    return None


def _parse_universal_data(data: dict, username: str) -> Optional[TikTokProfile]:
    """Parse the modern `__UNIVERSAL_DATA_FOR_REHYDRATION__` schema."""
    try:
        scope = data["__DEFAULT_SCOPE__"]["webapp.user-detail"]
        user = scope["userInfo"]["user"]
        stats = scope["userInfo"].get("stats", {})
    except (KeyError, TypeError):
        return None

    return TikTokProfile(
        username=user.get("uniqueId", username),
        display_name=user.get("nickname", ""),
        profile_url=f"https://www.tiktok.com/@{user.get('uniqueId', username)}",
        profile_photo_url=user.get("avatarLarger") or user.get("avatarMedium", ""),
        verified=bool(user.get("verified", False)),
        bio=user.get("signature", ""),
        followers_count=parse_count(stats.get("followerCount")),
        following_count=parse_count(stats.get("followingCount")),
        likes_count=parse_count(stats.get("heartCount") or stats.get("heart")),
    )


def _parse_sigi_state(data: dict, username: str) -> Optional[TikTokProfile]:
    """Parse the legacy `SIGI_STATE` schema."""
    try:
        user_module = data["UserModule"]
        user = user_module["users"][username]
        stats = user_module.get("stats", {}).get(username, {})
    except (KeyError, TypeError):
        return None

    return TikTokProfile(
        username=user.get("uniqueId", username),
        display_name=user.get("nickname", ""),
        profile_url=f"https://www.tiktok.com/@{user.get('uniqueId', username)}",
        profile_photo_url=user.get("avatarLarger") or user.get("avatarMedium", ""),
        verified=bool(user.get("verified", False)),
        bio=user.get("signature", ""),
        followers_count=parse_count(stats.get("followerCount")),
        following_count=parse_count(stats.get("followingCount")),
        likes_count=parse_count(stats.get("heartCount") or stats.get("heart")),
    )


def _parse_meta_tags(html: str, username: str) -> Optional[TikTokProfile]:
    """Fallback: build a minimal profile from Open Graph meta tags.

    These tags are intentionally public (used for link-preview cards) and
    are present even when the embedded JSON extraction above fails for
    some reason.
    """
    soup = BeautifulSoup(html, "lxml")

    def meta(prop: str) -> str:
        tag = soup.find("meta", attrs={"property": prop})
        return tag["content"].strip() if tag and tag.get("content") else ""

    title = meta("og:title")
    description = meta("og:description")
    image = meta("og:image")

    if not (title or description or image):
        return None

    return TikTokProfile(
        username=username,
        display_name=title.replace(f"@{username}", "").strip(" |-"),
        profile_url=f"https://www.tiktok.com/@{username}",
        profile_photo_url=image,
        bio=description,
    )


async def extract_profile(
    html: str, username: str, session: aiohttp.ClientSession
) -> Optional[TikTokProfile]:
    """Extract a TikTokProfile using a three-tier fallback strategy.

    Tier 1: embedded page JSON (richest data: counts, verified flag, bio).
    Tier 2: Open Graph meta tags (partial data, but very stable).
    Tier 3: TikTok's public oEmbed endpoint (last resort enrichment).
    """
    data = _extract_embedded_json(html)
    profile: Optional[TikTokProfile] = None

    if data is not None:
        profile = _parse_universal_data(data, username) or _parse_sigi_state(data, username)

    if profile is None:
        logger.warning(
            "Embedded page JSON unavailable/unparseable for @%s; "
            "falling back to Open Graph meta tags.",
            username,
        )
        profile = _parse_meta_tags(html, username)

    if profile is None:
        logger.warning(
            "Meta-tag fallback also failed for @%s; trying oEmbed endpoint.",
            username,
        )
        oembed = await fetch_oembed(session, f"https://www.tiktok.com/@{username}")
        if oembed:
            profile = TikTokProfile(
                username=username,
                display_name=oembed.get("author_name", ""),
                profile_url=f"https://www.tiktok.com/@{username}",
                profile_photo_url=oembed.get("thumbnail_url", ""),
                bio="",
            )

    return profile


# ------------------------------------------------------------------------- #
# Follower discovery (see module docstring for the important caveat)
# ------------------------------------------------------------------------- #

_LOGIN_WALL_MARKERS = (
    "log in to tiktok",
    "log in to see",
    "continue with google",
    "use qr code",
    "log in with",
)


async def _login_wall_present(page: Page) -> bool:
    """Check whether the current page state is showing a login prompt.

    TikTok shows one of these panels whenever a logged-out visitor tries
    to open a followers/following list -- this is the actual access
    control this tool respects rather than tries to defeat.
    """
    try:
        text = (await page.content()).lower()
    except PlaywrightError:
        return False
    return any(marker in text for marker in _LOGIN_WALL_MARKERS)


async def attempt_follower_discovery(page: Page, username: str) -> list[str]:
    """Try to discover follower profile links from the profile page.

    Real pipeline: click the "Followers" stat, wait for a panel, and if
    (and only if) an actual list renders -- scroll it, collecting unique
    profile links until no new ones appear for
    MAX_SCROLL_ATTEMPTS_WITHOUT_NEW_ITEMS consecutive scrolls.

    Under TikTok's current behavior this will detect a login wall instead
    of a list and return an empty list -- see module docstring.

    Returns:
        A list of unique follower profile URLs (empty if none accessible).
    """
    followers_selector = '[data-e2e="followers-count"]'
    try:
        await page.wait_for_selector(followers_selector, timeout=SELECTOR_TIMEOUT_MS)
        await page.click(followers_selector)
    except PlaywrightTimeoutError:
        logger.warning(
            "Could not locate a 'Followers' control on @%s's page; "
            "skipping follower discovery.",
            username,
        )
        return []

    await polite_delay(0.5, 1.2)

    if await _login_wall_present(page):
        logger.warning(
            "TikTok is requiring a login to view @%s's followers list. "
            "This is a platform-level access control (not specific to this "
            "account), so no follower profiles are publicly accessible here. "
            "Skipping follower collection and saving profile-level data only.",
            username,
        )
        with contextlib_suppress():
            await page.keyboard.press("Escape")
        return []

    # --- Generic list-scrolling logic, kept for forward compatibility ---
    list_selector = '[data-e2e="user-list"]'
    item_selector = '[data-e2e="user-item"] a[href^="/@"]'
    discovered: set[str] = set()
    stale_rounds = 0

    try:
        await page.wait_for_selector(list_selector, timeout=SELECTOR_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.info("No follower list panel appeared for @%s.", username)
        return []

    while stale_rounds < MAX_SCROLL_ATTEMPTS_WITHOUT_NEW_ITEMS:
        anchors = await page.query_selector_all(item_selector)
        before = len(discovered)
        for anchor in anchors:
            href = await anchor.get_attribute("href")
            if href:
                discovered.add(f"https://www.tiktok.com{href}" if href.startswith("/") else href)

        if len(discovered) == before:
            stale_rounds += 1
        else:
            stale_rounds = 0

        try:
            await page.hover(list_selector)
            await page.mouse.wheel(0, 1200)
        except PlaywrightError:
            break
        await polite_delay(0.6, 1.5)

    logger.info("Discovered %d follower profile link(s) for @%s.", len(discovered), username)
    return sorted(discovered)


class contextlib_suppress:
    """Tiny inline substitute for contextlib.suppress(Exception) for one-liners."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None


# ------------------------------------------------------------------------- #
# Fetching an individual (follower) profile's details
# ------------------------------------------------------------------------- #


async def fetch_profile_details(
    context: BrowserContext,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    profile_url: str,
    stats: ScrapeStats,
) -> Optional[TikTokProfile]:
    """Open one profile URL in its own page and extract its public data.

    Wrapped in a semaphore to bound concurrency, and in a broad
    try/except so a single bad profile can never crash the whole run.
    """
    username_guess = profile_url.rstrip("/").rsplit("@", 1)[-1]
    async with semaphore:
        page = None
        try:
            await polite_delay()
            page = await context.new_page()
            html, http_status = await load_page_html(page, profile_url)
            status = detect_account_status(html, username_guess, http_status)
            if status != "public":
                logger.info("Follower @%s is %s; skipping.", username_guess, status)
                return None
            profile = await extract_profile(html, username_guess, session)
            if profile is None:
                raise ValueError("All extraction tiers failed.")
            return profile
        except Exception as exc:  # noqa: BLE001 - must never crash the run
            logger.error("Failed to fetch profile %s: %s", profile_url, exc)
            stats.failed += 1
            return None
        finally:
            if page is not None:
                await page.close()


# ------------------------------------------------------------------------- #
# Output writing
# ------------------------------------------------------------------------- #


def save_outputs(profiles: list[TikTokProfile]) -> None:
    """Write collected profiles to output/followers.csv and output/followers.json."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = [p.to_row() for p in profiles]

    # JSON via orjson (fast, and preserves types/ordering).
    JSON_PATH.write_bytes(orjson.dumps(rows, option=orjson.OPT_INDENT_2))

    # CSV via pandas, with an explicit column order.
    df = pd.DataFrame(rows, columns=CSV_COLUMNS)
    df.to_csv(CSV_PATH, index=False)

    logger.info("Saved %d profile(s) to %s and %s", len(rows), CSV_PATH, JSON_PATH)


def print_summary(stats: ScrapeStats) -> None:
    """Render the final run summary as a Rich table."""
    table = Table(title="Scrape Summary", show_header=True, header_style="bold cyan")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Total profiles discovered", str(stats.discovered))
    table.add_row("Total profiles saved", str(stats.saved))
    table.add_row("Total failed", str(stats.failed))
    table.add_row("Total execution time", f"{stats.elapsed():.2f}s")
    console.print(table)


# ------------------------------------------------------------------------- #
# Orchestration
# ------------------------------------------------------------------------- #


async def run(profile_url_input: str) -> None:
    """Top-level async orchestration for the whole scrape."""
    stats = ScrapeStats()
    all_profiles: list[TikTokProfile] = []
    seen_usernames: set[str] = set()

    try:
        username, canonical_url = normalize_profile_url(profile_url_input)
    except ValueError as exc:
        console.print(f"[bold red]Invalid input:[/bold red] {exc}")
        return

    user_agent = get_user_agent()

    async with aiohttp.ClientSession(headers={"User-Agent": user_agent}) as session:
        async with async_playwright() as playwright:
            browser = await launch_browser(playwright)
            try:
                context = await new_stealthy_context(browser, user_agent)
                page = await context.new_page()

                logger.info("Loading profile: %s", canonical_url)
                try:
                    html, http_status = await load_page_html(page, canonical_url)
                except (PlaywrightTimeoutError, PlaywrightError) as exc:
                    console.print(
                        f"[bold red]Could not load the profile page after retries:[/bold red] {exc}"
                    )
                    return

                status = detect_account_status(html, username, http_status)

                if status == "not_found":
                    console.print("[bold red]This profile does not exist.[/bold red]")
                    console.print(
                        f"[dim]If @{username} does exist, please open "
                        f"output/debug/{username}_not_found.html to see exactly what "
                        f"TikTok returned, and share it so this can be diagnosed further.[/dim]"
                    )
                    return

                if status == "private":
                    print("This is a private account.")
                    return

                # --- Public profile: extract the target's own data first ---
                target_profile = await extract_profile(html, username, session)
                if target_profile is None:
                    logger.error("Could not extract any data for @%s.", username)
                    stats.failed += 1
                else:
                    all_profiles.append(target_profile)
                    seen_usernames.add(target_profile.username)
                    stats.discovered += 1

                # --- Attempt follower discovery (see caveat in docstring) ---
                follower_links = await attempt_follower_discovery(page, username)

                if follower_links:
                    semaphore = asyncio.Semaphore(MAX_CONCURRENT_PROFILE_FETCHES)
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        TextColumn("{task.completed}/{task.total}"),
                        TimeElapsedColumn(),
                        console=console,
                    ) as progress:
                        task_id = progress.add_task(
                            "Fetching follower profiles...", total=len(follower_links)
                        )
                        tasks = [
                            fetch_profile_details(context, session, semaphore, link, stats)
                            for link in follower_links
                        ]
                        for coro in asyncio.as_completed(tasks):
                            result = await coro
                            progress.advance(task_id)
                            if result and result.username not in seen_usernames:
                                seen_usernames.add(result.username)
                                all_profiles.append(result)
                                stats.discovered += 1
                else:
                    logger.info(
                        "No accessible follower profiles were found for @%s "
                        "(see the warning above for why).",
                        username,
                    )

            finally:
                await browser.close()

    stats.saved = len(all_profiles)
    save_outputs(all_profiles)
    print_summary(stats)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, main_task: asyncio.Task) -> None:
    """Ensure Ctrl+C / SIGTERM cancel the running task so cleanup (finally blocks) still runs."""

    def _handle(*_args: Any) -> None:
        console.print("\n[yellow]Shutdown requested -- cleaning up...[/yellow]")
        main_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle)
        except (NotImplementedError, RuntimeError):
            # Signal handlers aren't available on some platforms (e.g. Windows
            # event loops); Ctrl+C will still raise KeyboardInterrupt there.
            pass


def main() -> None:
    """Entry point: prompt for a URL (or take it from argv) and run the scraper."""
    console.rule("[bold magenta]TikTok Public Profile Scraper[/bold magenta]")

    if len(sys.argv) > 1:
        profile_url_input = sys.argv[1]
    else:
        profile_url_input = console.input("Enter a TikTok profile URL: ")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main_task = loop.create_task(run(profile_url_input))
    _install_signal_handlers(loop, main_task)

    try:
        loop.run_until_complete(main_task)
    except asyncio.CancelledError:
        console.print("[yellow]Stopped by user.[/yellow]")
    except KeyboardInterrupt:
        console.print("[yellow]Stopped by user.[/yellow]")
    finally:
        loop.close()


if __name__ == "__main__":
    main()