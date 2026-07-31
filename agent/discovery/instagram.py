"""
Instagram search and profile reading. Target 20/account/day. READ ONLY.

Hashtags and explore pages. Reads bio, followers, post count, and website.
This module NEVER sends -- Instagram's ToS bans automated cold outreach and
automating it risks the account. Only discovery/reading happens here.

============================================================================
VERIFIED against real, live, logged-out public Instagram pages on 2026-07-31
============================================================================
Instagram serves its actual rendered page (not the SSR-disabled shell a bare
HTTP request gets -- confirmed separately) to a real browser, even logged
out, for PUBLIC hashtag/profile/post pages. Two things the original guesses
got wrong, found by inspecting real dumps (instagram.com/nike/, a #bakery
hashtag page, and a real reel page):

  1. Hashtag pages now show Reels (/reel/... links) alongside classic posts
     (/p/...), with NO <article> wrapper -- Instagram's grid items sit behind
     deeply auto-generated, non-semantic CSS classes (e.g. "x1i10hfl xjbqb8w
     ...") that are useless to select on and will keep changing. Matching on
     the href prefix is the only stable part.
  2. The reliable data isn't in visible DOM text at all -- it's in the page's
     `og:description` / `og:url` meta tags (follower/following/post counts as
     Instagram's own formatted string, and the canonical URL with the owning
     username as its first path segment) and a `"biography":"..."` /
     `"bio_links":[...]` field embedded in the page's own hydration JSON.
     Both are semantic, Instagram-authored strings, not auto-generated
     classnames -- far less likely to silently break than a CSS selector,
     and they survive even on pages that also render a login-wall overlay
     over the visible content (confirmed on the reel page dump).

Extraction below reads those meta tags and embedded JSON fields via regex
over the raw page content, not DOM locators, for exactly that reason.
============================================================================
"""

from __future__ import annotations

import json
import re

from playwright.sync_api import Page

HASHTAG_URL = "https://www.instagram.com/explore/tags/{tag}/"

_STATS_RE = re.compile(
    r"([\d.,KMk]+)\s+Followers,\s+([\d.,KMk]+)\s+Following,\s+([\d.,KMk]+)\s+Posts",
    re.IGNORECASE,
)
_BIOGRAPHY_RE = re.compile(r'"biography":"((?:[^"\\]|\\.)*)"')
_BIO_LINK_URL_RE = re.compile(r'"bio_links":\[\{.{0,1000}?"url":"((?:[^"\\]|\\.)*)"')
_OG_URL_USERNAME_RE = re.compile(r"https://www\.instagram\.com/([^/]+)/")
_ENGAGEMENT_RE = re.compile(r"([\d.,KMk]+)\s+likes?,\s+([\d.,KMk]+)\s+comments?", re.IGNORECASE)


def build_hashtag_url(niche: str) -> str:
    """
    Turn a niche like 'home bakery' into a hashtag explore URL, e.g.
    '#homebakery'. Instagram hashtags can't contain spaces or punctuation, so
    this strips both -- genuinely testable without touching a live page.
    """
    tag = "".join(ch for ch in niche if ch.isalnum())
    return HASHTAG_URL.format(tag=tag.lower())


def widen_hashtag_terms(niche: str) -> str:
    """
    Agentic adaptation: called when a hashtag search comes back with too few
    results. Drops the leading qualifier word of a multi-word niche (e.g.
    "home bakery" -> "bakery") and keeps searching on the broader term --
    English noun phrases put the general category last and modifiers first,
    so this is the same "drop the more restrictive term first" idea as
    linkedin.widen_search_terms, adapted to a single hashtag instead of two
    separate search fields. Returns the niche unchanged once it's already
    down to one word (as wide as it gets).
    """
    words = niche.split()
    if len(words) <= 1:
        return niche
    return " ".join(words[1:])


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
    Reads post/reel thumbnails on a loaded hashtag explore page. Matches on
    the href prefix directly (verified 2026-07-31 -- see module docstring)
    rather than any containing element, since there is no reliable one.
    Query params (Instagram appends tracking params like
    '?utm_source=popular_topic_grid') are stripped so the same post found
    twice doesn't look like two different URLs.
    """
    results = []
    seen: set[str] = set()
    links = page.locator("a[href^='/p/'], a[href^='/reel/']")

    for i in range(links.count()):
        href = _safe_attr(links.nth(i), "href")
        if not href:
            continue
        url = href.split("?")[0]
        if url in seen:
            continue
        seen.add(url)
        results.append({"post_url": url})

    return results


def resolve_post_to_profile_url(page: Page, post_url: str) -> str | None:
    """
    Visits one post/reel and reads its `og:url` meta tag, which Instagram
    fills in as 'https://www.instagram.com/<username>/reel/<code>/' (or
    '/p/<code>/' for classic posts) -- the owning account's username is
    always the first path segment. Verified 2026-07-31 against a real reel
    page (which also rendered a login-wall overlay in the body -- this meta
    tag was unaffected, which is exactly why it's used instead of a DOM
    locator that overlay could shift or hide).
    """
    if not post_url.startswith("http"):
        post_url = f"https://www.instagram.com{post_url}"
    page.goto(post_url, timeout=15_000)

    og_url = _meta_content(page, "og:url")
    if not og_url:
        return None
    match = _OG_URL_USERNAME_RE.match(og_url)
    return f"https://www.instagram.com/{match.group(1)}/" if match else None


def extract_post_engagement(page: Page) -> dict:
    """
    Reads one post/reel's like and comment counts from its `og:description`
    meta tag (Instagram's own format: "N likes, N comments - username on
    DATE: caption..."). Verified 2026-07-31 against a real reel page. Meant
    to be called right after resolve_post_to_profile_url(), while the page
    is still on that post/reel -- this is a per-post ENGAGEMENT SAMPLE, one
    data point from whichever post discovery happened to find via the
    hashtag search, not a full-profile average (that would mean visiting
    several of the account's posts, an extra page load per lead this
    deliberately avoids).
    """
    og_description = _meta_content(page, "og:description") or ""
    match = _ENGAGEMENT_RE.search(og_description)
    if not match:
        return {"likes": None, "comments": None}
    return {"likes": parse_count(match.group(1)), "comments": parse_count(match.group(2))}


def extract_profile(page: Page) -> dict:
    """
    Reads a profile page's bio, follower/post counts, and website. Verified
    2026-07-31 against a real public profile page (instagram.com/nike/) --
    see module docstring. Reads the `og:description` meta tag for the three
    counts (Instagram's own format: "N Followers, N Following, N Posts -
    ...") and the page's embedded hydration JSON for the actual biography
    text and bio-link URL. `json.loads` on the raw matched fragment (wrapped
    back in quotes) correctly unescapes whatever JSON string-escaping
    Instagram used (\\/, \\uXXXX, etc.) rather than hand-rolling that.
    """
    content = page.content()

    bio_match = _BIOGRAPHY_RE.search(content)
    bio = json.loads(f'"{bio_match.group(1)}"') if bio_match else ""

    link_match = _BIO_LINK_URL_RE.search(content)
    website = json.loads(f'"{link_match.group(1)}"') if link_match else None

    og_description = _meta_content(page, "og:description") or ""
    stats_match = _STATS_RE.search(og_description)
    follower_count = parse_count(stats_match.group(1)) if stats_match else None
    post_count = parse_count(stats_match.group(3)) if stats_match else None

    return {
        "platform": "instagram",
        "bio": bio,
        "has_website": bool(website),
        "website": website,
        "follower_or_headcount": follower_count,
        "post_count": post_count,
        "recent_activity": (post_count or 0) > 0,  # refined once real story/post-date data is read
    }


def _meta_content(page: Page, property_name: str) -> str | None:
    """
    Reads one <meta property="..."> tag's content, waiting briefly for it to
    attach first -- meta tags land with the page's initial render rather
    than needing the full 'load' event, but a short explicit wait is cheaper
    and more honest than assuming that timing never changes.
    """
    locator = page.locator(f"meta[property='{property_name}']").first
    try:
        locator.wait_for(state="attached", timeout=5_000)
    except Exception:  # noqa: BLE001 -- tag may genuinely never appear (deleted post, hard login wall)
        pass
    return _safe_attr(locator, "content")


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
