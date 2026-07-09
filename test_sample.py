import re
from playwright.sync_api import expect

def test_google_search(page):
    page.goto("https://www.google.com/ncr")
