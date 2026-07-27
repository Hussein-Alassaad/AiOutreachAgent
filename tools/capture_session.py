"""
Nexaris -- one-time login capture tool.

WHAT THIS DOES
Run this once per social media account (LinkedIn or Instagram). It opens a
real, visible browser window where YOU log in yourself, exactly as normal --
including any 2-step verification. Your password is typed directly into the
real LinkedIn/Instagram page. This script never sees it, never stores it,
and never sends it anywhere.

Once you're logged in and back at your normal feed/home page, it saves only
your session (the cookies that keep you logged in) to one small file. You
then send that ONE file back -- it contains your session, never your
password.

This file is meant to be handed to someone else (e.g. a teammate who owns
the accounts) as a standalone script -- it only needs Playwright, not the
rest of the Nexaris agent project.

SETUP (one-time, before first use):
    pip install playwright
    playwright install chromium

RUN (once per account):
    python capture_session.py

Then follow the prompts on screen.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PLATFORMS = {
    "1": ("linkedin", "https://www.linkedin.com/login"),
    "2": ("instagram", "https://www.instagram.com/accounts/login/"),
}

OUTPUT_DIR = Path(__file__).parent / "captured_sessions"


def main() -> int:
    print("=" * 60)
    print("  NEXARIS -- Session Capture Tool")
    print("=" * 60)
    print()
    print("This opens a real browser window. Log in yourself, exactly as you")
    print("normally would -- including any 2-step verification code.")
    print()
    print("Your password is NEVER seen, stored, or sent anywhere by this")
    print("tool. Only your logged-in session gets saved, once you're done.")
    print()

    print("Which platform is this login for?")
    print("  1) LinkedIn")
    print("  2) Instagram")
    choice = input("Enter 1 or 2: ").strip()
    if choice not in PLATFORMS:
        print("Invalid choice -- run the script again and enter 1 or 2.")
        return 1
    platform, url = PLATFORMS[choice]

    account_label = input(
        "Which account is this (e.g. 'Account 1', matches what Hussein gave you): "
    ).strip().replace(" ", "_")
    if not account_label:
        account_label = "unlabeled"

    print()
    print(f"Opening {platform} now...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(url)

        print()
        print("-" * 60)
        print(f"A browser window has opened to {platform}.")
        print("Log in there now, exactly as you normally would.")
        print()
        print("Once you can see your normal feed / home page -- fully")
        print("logged in, no more prompts on screen -- come back here.")
        print("-" * 60)
        input("Press Enter here once you're fully logged in... ")

        OUTPUT_DIR.mkdir(exist_ok=True)
        out_path = OUTPUT_DIR / f"{platform}_{account_label}.json"
        context.storage_state(path=str(out_path))

        browser.close()

    print()
    print("=" * 60)
    print(f"  Done! Session saved to:")
    print(f"  {out_path}")
    print("=" * 60)
    print()
    print("Send that ONE file back. It contains only your session --")
    print("never your password, never your 2-step verification details.")
    print()
    print("Repeat this for each account (run the script again).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
