from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=r"C:\chrome-profile",
        channel="chrome",
        headless=False
    )

    page = context.new_page()
    page.goto("https://accounts.google.com")

    input("Press Enter...")

print("Session sauvegardée.")
import re
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    nom=input("donne le nom de l artiste : ")
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(storage_state="google_session.json")
    page = context.new_page()
    page.goto("https://www.youtube.com/")
    page.get_by_role("combobox", name="Rechercher").click()
    page.get_by_role("combobox", name="Rechercher").fill(nom)
    page.get_by_role("combobox", name="Rechercher").press("Enter")
    page.locator('a#video-title').first.click()
    page.get_by_role("button", name="J'aime cette vidéo, comme 67").click()
    import time 
    time.sleep(2)

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
