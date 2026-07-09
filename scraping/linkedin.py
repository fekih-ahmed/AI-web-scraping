# Requirements:
#   pip install playwright
#   playwright install chromium
#
# Run:
#   python main.py

import asyncio
import os
import random
from playwright.async_api import async_playwright

SESSION_FILE = "auth_state.json"
MAX_FOLLOWS_PER_RUN = 50


async def get_browser(playwright):
    browser = await playwright.chromium.launch(headless=False)
    if os.path.exists(SESSION_FILE):
        context = await browser.new_context(storage_state=SESSION_FILE)
    else:
        context = await browser.new_context()
    return browser, context


async def ensure_logged_in(page, context):
    await page.goto("https://www.linkedin.com/feed/")
    await asyncio.sleep(2)

    if "login" in page.url or "checkpoint" in page.url:
        print("Please log in manually in the browser window...")
        while "feed" not in page.url:
            await asyncio.sleep(2)
        print("Login detected.")

    await context.storage_state(path=SESSION_FILE)
    print("Session saved.")


def ask_search_keyword():
    keyword = input("Enter the search keyword: ").strip()
    while not keyword:
        keyword = input("Please enter a non-empty keyword: ").strip()
    return keyword


def ask_start_position():
    while True:
        try:
            start = int(input("Start from which result number? (e.g. 1, 20, 50): "))
            if start >= 1:
                return start
            print("Please enter a number 1 or greater.")
        except ValueError:
            print("Please enter a valid number.")


async def open_search(page, keyword):
    url = f"https://www.linkedin.com/search/results/people/?keywords={keyword}"
    await page.goto(url)
    await human_delay(2, 3)


def ask_follow_count():
    while True:
        try:
            count = int(input(f"How many people do you want to follow? (max {MAX_FOLLOWS_PER_RUN}) "))
            if 0 < count <= MAX_FOLLOWS_PER_RUN:
                return count
            print(f"Please enter a number between 1 and {MAX_FOLLOWS_PER_RUN}.")
        except ValueError:
            print("Please enter a valid number.")


async def human_delay(a=1.5, b=3.5):
    await asyncio.sleep(random.uniform(a, b))


FOLLOW_WORDS = {"follow", "suivre", "seguir", "folgen", "segui"}

async def follow_people(page, target_count, start_position):
    followed = 0
    current_position = 0

    while followed < target_count:

        cards = await page.locator("li").all()

        for card in cards:

            try:
                button = card.locator("button").first

                if await button.count() == 0:
                    continue

                text = (await button.inner_text()).strip().lower()

                current_position += 1

                if current_position < start_position:
                    continue

                if text not in ["suivre", "+ suivre", "se connecter"]:
                    continue

                await button.scroll_into_view_if_needed()
                await human_delay(0.8, 1.5)

                await button.click()

                followed += 1
                print(f"Followed {followed}/{target_count}")

                await human_delay(2, 4)

                if followed >= target_count:
                    break

            except Exception:
                continue

        if followed >= target_count:
            break

        await page.mouse.wheel(0, 2500)
        await human_delay(2, 3)

    print(f"Done. Followed {followed}/{target_count}")

async def main():
    async with async_playwright() as playwright:
        browser, context = await get_browser(playwright)
        page = await context.new_page()

        await ensure_logged_in(page, context)

        keyword = ask_search_keyword()
        start_position = ask_start_position()
        await open_search(page, keyword)

        target_count = ask_follow_count()

        await follow_people(page, target_count, start_position)

        await context.storage_state(path=SESSION_FILE)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
    