"""
Instagram search and profile reading. Target 20/account/day. READ ONLY.

Hashtags, explore pages, location tags. Reads bio, followers, post count,
engagement signals. This module NEVER sends -- Instagram's ToS bans automated
cold outreach and automating it risks the account. Only discovery/reading
happens here.

============================================================================
HONESTY NOTE -- read before trusting any selector in this file
============================================================================
Instagram's real content is login-gated and its markup changes periodically.
Every selector below was written from Instagram's general, publicly-known page
structure -- NOT verified against a live, logged-in session, because no
account was available to test against while this was built. Functions that
touch real page content are marked UNVERIFIED and must be checked (and very
likely corrected) the first time this runs against a real logged-in account.
The hashtag-URL and count-parsing helpers that don't depend on Instagram's
actual DOM are genuinely tested today.
============================================================================
"""

from __future__ import annotations

from playwright.sync_api import Page

HASHTAG_URL = "https://www.instagram.com/explore/tags/{tag}/"


def build_hashtag_url(niche: str) -> str:
    """
    Turn a niche like 'home bakery' into a hashtag explore URL, e.g.
    '#homebakery'. Instagram hashtags can't contain spaces or punctuation, so
    this strips both -- genuinely testable without touching a live page.
    """
    tag = "".join(ch for ch in niche if ch.isalnum())
    return HASHTAG_URL.format(tag=tag.lower())


def parse_count(text: str | None) -> int | None:
    """
    Instagram displays counts abbreviated ('12.4K', '3.1M', or a plain
    '842'). Converts any of those to a real integer. Pure text parsing --
    fully tested today, independent of any live page.
    """
    if not text:
        return None

    cleaned = text.strip().upper().replace(",", "")
    multiplier = 1
    if cleaned.endswith("K"):
        multiplier = 1_000
        cleaned = cleaned[:-1]
    elif cleaned.endswith("M"):
        multiplier = 1_000_000
        cleaned = cleaned[:-1]

    try:
        return int(float(cleaned) * multiplier)
    except ValueError:
        return None


def extract_hashtag_results(page: Page) -> list[dict]:
    """
    UNVERIFIED SELECTORS. Reads post thumbnails on a loaded hashtag explore
    page and pulls the linked account for each. Instagram's explore grid links
    to individual posts, not directly to profiles, so this returns post URLs
    for now -- resolving each to its owning account happens in
    extract_profile(), which visits that account's own page.
    """
    results = []
    posts = page.locator("article a[href*='/p/']")

    for i in range(posts.count()):
        href = _safe_attr(posts.nth(i), "href")
        if href:
            results.append({"post_url": href})

    return results


def resolve_post_to_profile_url(page: Page, post_url: str) -> str | None:
    """
    UNVERIFIED SELECTOR. The hashtag explore grid links to individual posts,
    not to the accounts that made them -- this opens a post and reads the
    owning account's profile link from it, resolving 'a post about this niche'
    into 'the business account behind it'.
    """
    page.goto(post_url, timeout=15_000)
    return _safe_attr(page.locator("header a[role='link']").first, "href")


def extract_profile(page: Page) -> dict:
    """
    UNVERIFIED SELECTORS. Reads a profile page's header: bio, follower count,
    post count, and whether a website link is present. Returns the normalised
    shape discovery/qualify.py's qualify_profile() expects.
    """
    bio = _safe_text(page.locator("div.-vDIg span, header section div._aa_c").first)
    website = _safe_attr(page.locator("a.-nal3, header a[href*='l.instagram.com']").first, "href")

    stats = page.locator("header section ul li")
    post_count = parse_count(_safe_text(stats.nth(0))) if stats.count() > 0 else None
    follower_count = parse_count(_safe_text(stats.nth(1))) if stats.count() > 1 else None

    return {
        "platform": "instagram",
        "bio": bio or "",
        "has_website": bool(website),
        "website": website,
        "follower_or_headcount": follower_count,
        "post_count": post_count,
        "recent_activity": (post_count or 0) > 0,  # refined once real story/post-date data is read
    }


def _safe_text(locator) -> str | None:
    try:
        return locator.inner_text().strip()
    except Exception:  # noqa: BLE001 -- a missing element is expected, not exceptional
        return None


def _safe_attr(locator, attr: str) -> str | None:
    try:
        return locator.get_attribute(attr)
    except Exception:  # noqa: BLE001
        return None
