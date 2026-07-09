from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)  # Set True to run in background
    page = browser.new_page()

    page.goto("https://www.google.com")

    print("Title:", page.title())

    browser.close()