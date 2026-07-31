"""
Nexaris -- public page dump tool (no login required).

Fetches the real rendered HTML of PUBLIC Instagram/LinkedIn pages using a
headless browser and saves it to a file. Used to design/verify the actual
selectors agent/discovery/*.py relies on, against real current markup --
without needing any account or login, since a public profile, hashtag, or
company-about page is visible to anyone, logged out, in a normal browser.

RUN (from this tools/ folder):
    ..\\agent\\venv\\Scripts\\python.exe dump_public_profile.py <target> [<target> ...]

Each <target> is one of:
    a username             -- dumps that public Instagram profile page
    tag:<hashtag>           -- dumps that Instagram hashtag's explore page
    post:<shortcode>        -- dumps a single Instagram post page (the code in
                               a post URL, e.g. "ABC123xyz" in .../p/ABC123xyz/)
    reel:<shortcode>        -- same, for a /reel/ URL
    li:<company-slug>        -- dumps that public LinkedIn company About page
                               (the slug in linkedin.com/company/<slug>/)
    lisearch:<query>          -- dumps LinkedIn's anonymous company-search
                               results page for that query (to see whether
                               it even renders results while logged out)

Example:
    ..\\agent\\venv\\Scripts\\python.exe dump_public_profile.py nike tag:bakery li:nike

Saves each page to dumps/<name>.html in this folder -- these files contain
only what anyone can see by visiting that public page in a normal browser,
logged out. Nothing sensitive, safe to leave in the repo or send back.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path(__file__).parent / "dumps"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _resolve(target: str) -> tuple[str, str]:
    """Turn a CLI target into (url, output_filename_stem)."""
    if target.startswith("tag:"):
        tag = target[len("tag:") :]
        return f"https://www.instagram.com/explore/tags/{tag}/", f"tag_{tag}"
    if target.startswith("post:"):
        shortcode = target[len("post:") :]
        return f"https://www.instagram.com/p/{shortcode}/", f"post_{shortcode}"
    if target.startswith("reel:"):
        shortcode = target[len("reel:") :]
        return f"https://www.instagram.com/reel/{shortcode}/", f"reel_{shortcode}"
    if target.startswith("li:"):
        slug = target[len("li:") :]
        return f"https://www.linkedin.com/company/{slug}/", f"li_{slug}"
    if target.startswith("lisearch:"):
        query = target[len("lisearch:") :]
        url = f"https://www.linkedin.com/search/results/companies/?keywords={quote(query)}"
        return url, f"lisearch_{query.replace(' ', '_')}"
    return f"https://www.instagram.com/{target}/", target


def dump(target: str) -> None:
    url, out_stem = _resolve(target)
    print(f"Fetching {url} ...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA)
        page.goto(url, timeout=45_000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)  # let client-side rendering finish

        OUTPUT_DIR.mkdir(exist_ok=True)
        out_path = OUTPUT_DIR / f"{out_stem}.html"
        out_path.write_text(page.content(), encoding="utf-8")
        print(f"  Saved: {out_path}  ({out_path.stat().st_size} bytes)")
        print(f"  Page title: {page.title()}")

        browser.close()


def main() -> int:
    targets = sys.argv[1:]
    if not targets:
        print("Usage: python dump_public_profile.py <target> [<target> ...]")
        print("  <target> = username, tag:<hashtag>, or post:<shortcode>")
        return 1

    for target in targets:
        try:
            dump(target)
        except Exception as exc:  # noqa: BLE001 -- report and keep going for remaining targets
            print(f"  FAILED for {target}: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
