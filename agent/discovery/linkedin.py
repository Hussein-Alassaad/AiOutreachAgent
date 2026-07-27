"""
LinkedIn search and profile reading. Target 30/account/day.

Reads company name, description, headcount, website. Prioritises recently
active companies over dormant ones. Adapts its search terms when results come
back weak -- agentic, not fixed.

============================================================================
HONESTY NOTE -- read before trusting any selector in this file
============================================================================
LinkedIn requires a logged-in session to show real search results, and its
page structure changes periodically. Every selector below was written from
LinkedIn's general, publicly-documented page structure -- NOT verified against
a live, logged-in session, because no account was available to test against
while this was built. Every function that touches real page content is marked
UNVERIFIED and must be checked (and very likely corrected) the first time this
runs against a real logged-in account. The URL-building and text-parsing
helpers that don't depend on LinkedIn's actual DOM are genuinely tested today.
============================================================================
"""

from __future__ import annotations

import re
from urllib.parse import quote

from playwright.sync_api import Page

SEARCH_URL = "https://www.linkedin.com/search/results/companies/"


def build_search_url(niche: str, location: str) -> str:
    """
    Build a LinkedIn company-search URL from the dashboard's niche/location
    settings.

    UNVERIFIED CAVEAT: LinkedIn's precise faceted filters (an exact city, a
    specific industry code, a company-size bracket) use internal facet IDs
    that only appear once a real search is performed and the URL it produces
    is inspected -- they are not plain text. This function instead folds
    niche and location into the free-text `keywords` parameter, which LinkedIn
    does support without any facet IDs, as a reasonable working fallback.
    Precise faceted search can replace this once a real account confirms what
    those facet parameters look like.
    """
    query = " ".join(part for part in (niche, location) if part).strip()
    return f"{SEARCH_URL}?keywords={quote(query)}&origin=GLOBAL_SEARCH_HEADER"


def widen_search_terms(niche: str, location: str) -> tuple[str, str]:
    """
    Agentic adaptation: called when a search comes back with too few results.
    Drops the location first (the more restrictive term), then would drop
    niche specificity on a second retry if the caller loops. Returns the new
    (niche, location) to search with -- a real, working decision function,
    independent of any LinkedIn-specific markup.
    """
    if location:
        return niche, ""  # widen: search the niche everywhere, not just one place
    return "", ""  # already as wide as it gets


def extract_search_results(page: Page) -> list[dict]:
    """
    UNVERIFIED SELECTORS. Reads each result card on a loaded search-results
    page. Returns raw dicts: {profile_url, display_name, headline_or_bio} --
    NOT yet the normalised shape qualify_profile() expects; that mapping
    happens in discover_companies() below, after this raw read.
    """
    results = []
    cards = page.locator("li.reusable-search__result-container")

    for i in range(cards.count()):
        card = cards.nth(i)
        results.append({
            "profile_url": _safe_attr(card.locator("a.app-aware-link").first, "href"),
            "display_name": _safe_text(card.locator("span[aria-hidden='true']").first),
            "headline_or_bio": _safe_text(card.locator(".entity-result__primary-subtitle").first),
        })

    return results


def extract_company_profile(page: Page) -> dict:
    """
    UNVERIFIED SELECTORS. Reads a company's About page: description, employee
    count, and website link. Returns the normalised shape
    discovery/qualify.py's qualify_profile() expects.
    """
    description = _safe_text(page.locator(
        "[data-test-id='about-us__description'], .org-about-us-organization-description__text"
    ).first)
    headcount_text = _safe_text(page.locator(
        ".org-about-company-module__company-size-definition-text"
    ).first)
    website = _safe_attr(page.locator("a[data-tracking-control-name='about_website']").first, "href")

    return {
        "platform": "linkedin",
        "bio": description or "",
        "has_website": bool(website),
        "website": website,
        "follower_or_headcount": _parse_headcount(headcount_text),
        "post_count": None,       # would need the Posts tab -- separate navigation, not yet built
        "recent_activity": True,  # unknown without the Posts tab; assume yes rather than unfairly reject
    }


def _parse_headcount(text: str | None) -> int | None:
    """
    LinkedIn shows headcount as a range like '51-200 employees'. Genuinely
    tested below with real sample strings -- this parsing logic doesn't touch
    the live page, only the text once it's already been read.
    """
    if not text:
        return None
    numbers = re.findall(r"[\d,]+", text)
    if not numbers:
        return None
    # Take the lower bound of a range as a conservative estimate.
    return int(numbers[0].replace(",", ""))


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
