"""
Nexaris -- authenticated page dump tool.

Loads a session file saved by capture_session.py and dumps the real rendered
HTML of a page that needs a real login -- e.g. LinkedIn's company search,
which (verified 2026-07-31) has no public/logged-out equivalent at all,
unlike Instagram or LinkedIn's own bare company pages.

This never touches a password -- it reuses the "you're logged in" token
capture_session.py already saved, the same way agent/core/session.py does
in the real pipeline.

RUN (from this tools/ folder):
    ..\\agent\\venv\\Scripts\\python.exe dump_with_session.py <session_file> <url>

Example:
    ..\\agent\\venv\\Scripts\\python.exe dump_with_session.py ^
        captured_sessions\\linkedin_MyLinkedIn.json ^
        "https://www.linkedin.com/search/results/companies/?keywords=bakery"

Saves to dumps/authed_<slug>.html
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path(__file__).parent / "dumps"


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: python dump_with_session.py <session_file> <url>")
        return 1

    session_file, url = sys.argv[1], sys.argv[2]
    print(f"Loading session: {session_file}")
    print(f"Visiting: {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=session_file)
        page = context.new_page()
        page.goto(url, timeout=45_000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)  # let client-side rendering finish

        OUTPUT_DIR.mkdir(exist_ok=True)
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", url).strip("_")[:60]
        out_path = OUTPUT_DIR / f"authed_{slug}.html"
        out_path.write_text(page.content(), encoding="utf-8")
        print(f"  Saved: {out_path}  ({out_path.stat().st_size} bytes)")
        print(f"  Page title: {page.title()}")

        browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
