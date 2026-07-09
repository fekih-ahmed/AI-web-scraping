from playwright.sync_api import sync_playwright
from rich import print

                
def run(playwright :Playwright):
    start_url =  "https://www.scrap4love.com/shop-4"
    chrome = playwright.chromium
    browser = chrome.launch(headless=False)
    page = browser.new_page()
    page.goto(start_url)
    while True :
        for link in page.locator(
            "a[data-selenium='LNYVZi ayCf9D']"
        ).all():
            p = browser.new_page(base_url="https://www.scrap4love.com/shop-4")
            url = link.get_attribute("href")
            if url is not None :
                p.goto(url)
            else :
                p.close()






with sync_playwright() as playwright :
    run(playwright)
