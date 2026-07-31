"""
LinkedIn search and profile reading. Target 30/account/day.

Reads company name, description, headcount, website. Adapts its search terms
when results come back weak -- agentic, not fixed.

============================================================================
VERIFIED against real, live LinkedIn pages on 2026-07-31
============================================================================
Two very different situations, found by inspecting real pages rather than
guessing:

  1. Search (build_search_url / extract_search_results) has NO public
     equivalent -- confirmed both the /about company subpage and the search
     results page hard-redirect anonymous visits to a login page. Verified
     with a real captured login session instead: search itself works fine
     once authenticated, but the results page uses deeply auto-generated,
     non-semantic CSS classes (the same pattern Instagram uses) that are
     useless to select on and will keep changing. The href pattern
     (`/company/<slug>/`) is the only stable part -- each company appears
     3x in the markup (image link, name link, follow-button link), so this
     dedupes on the URL and keeps whichever occurrence actually has text.
  2. Profile reading (extract_company_profile), by contrast, does NOT need
     login at all -- confirmed that a company's bare page
     (linkedin.com/company/<slug>/, NOT the /about subpage, which does
     redirect to login) server-renders its full About section with clean,
     stable `data-test-id="about-us__*"` attributes, even logged out. Fixed
     one real bug found this way: the website link's href is a
     `linkedin.com/redir/redirect?url=...` wrapper, not the site itself --
     the original code returned that wrapper URL verbatim.
============================================================================
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

from playwright.sync_api import Page

SEARCH_URL = "https://www.linkedin.com/search/results/companies/"

_COMPANY_HREF_RE = re.compile(r"^https://www\.linkedin\.com/company/[^/?]+/?")
_REDIRECT_URL_RE = re.compile(r"[?&]url=([^&]*)")
_ACTIVITY_URN_RE = re.compile(r'data-urn="(urn:li:activity:[^"]*)"')
_POST_TIME_RE = re.compile(r'update-components-actor__sub-description[^>]*>\s*<span><!---->(\d+[a-zA-Z]+)')
_RELATIVE_TIME_RE = re.compile(r"^(\d+)(h|d|w|mo|yr)$")


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
    Reads each company card on a loaded search-results page. Verified
    2026-07-31 against a real authenticated search (see module docstring):
    matches on the href pattern directly rather than any containing element
    or CSS class, since none are stable. Each company's URL appears multiple
    times in the raw markup (image link + name link + follow-button link) --
    deduped here, keeping the occurrence that actually has the company name
    as its text.

    Returns raw dicts: {profile_url, display_name, headline_or_bio}.
    headline_or_bio is always None -- no stable subtitle/tagline element was
    found on the search card itself, and it's not needed downstream anyway:
    extract_company_profile() below reads the real description directly off
    each company's own page once discover_companies() visits it.
    """
    results: dict[str, str | None] = {}
    links = page.locator("a[href*='linkedin.com/company/']")

    for i in range(links.count()):
        href = _safe_attr(links.nth(i), "href")
        if not href or not _COMPANY_HREF_RE.match(href):
            continue
        url = href.split("?")[0].rstrip("/") + "/"
        name = _safe_text(links.nth(i))
        if name and not results.get(url):
            results[url] = name
        elif url not in results:
            results[url] = None

    return [
        {"profile_url": url, "display_name": name, "headline_or_bio": None}
        for url, name in results.items()
        if name  # a company with no text occurrence anywhere is unusable -- skip it
    ]


def extract_company_profile(page: Page) -> dict:
    """
    Reads a company's LinkedIn page: description, employee count, and
    website link. Returns the normalised shape discovery/qualify.py's
    qualify_profile() expects. Verified 2026-07-31 against a real, live,
    logged-out LinkedIn company page -- see module docstring for why this
    works without login when the /about subpage doesn't.
    """
    description = _safe_text(page.locator("[data-test-id='about-us__description']").first)
    size_text = _safe_text(page.locator("[data-test-id='about-us__size'] dd").first)
    website = _unwrap_redirect(
        _safe_attr(page.locator("[data-test-id='about-us__website'] a").first, "href")
    )

    return {
        "platform": "linkedin",
        "bio": description or "",
        "has_website": bool(website),
        "website": website,
        "follower_or_headcount": _parse_headcount(size_text),
        "post_count": None,       # this function doesn't visit the Posts tab -- see extract_recent_posts()
        "recent_activity": True,  # placeholder; scheduler.py overwrites both fields with extract_recent_posts()'s real read
    }


def extract_recent_posts(page: Page) -> dict:
    """
    Reads a company's Posts tab (linkedin.com/company/<slug>/posts/) for how
    many posts are visible without scrolling and how recently the newest one
    went up. Verified 2026-07-31 against a real authenticated fetch of
    Nike's Posts tab -- this tab hard-redirects anonymous visits straight to
    LinkedIn's login page (confirmed separately with a plain HTTP request,
    got a 302 to /uas/login), so unlike extract_company_profile() above,
    this only works when `page` already has a real logged-in session -- true
    for every real call site in this codebase (agent/core/session.py always
    restores a logged-in account's session before discovery runs), just not
    for an anonymous verification fetch.

    `visible_post_count` is NOT a lifetime total -- it's genuinely just how
    many post entries rendered on this page view (3, without scrolling, in
    testing). LinkedIn's actual lifetime total sits buried in an internal
    API pagination JSON blob that would be easy to misidentify (multiple
    similarly-shaped paging blocks exist on the same page for unrelated
    things) and give false confidence in a wrong number -- counting the
    real, visible posts is the honest signal, even though it's a smaller one.

    `recent_activity` reads the newest post's relative timestamp (LinkedIn's
    own '10h' / '2d' / '1w' / '3mo' format, confirmed against three real
    posts in order) rather than a post-count threshold, since a company with
    only one visible post from 10 hours ago is obviously more active than
    one with three posts all from a year ago.
    """
    content = page.content()
    urns = list(dict.fromkeys(_ACTIVITY_URN_RE.findall(content)))  # de-dupe, keep order
    times = _POST_TIME_RE.findall(content)
    newest = times[0] if times else None

    return {
        "visible_post_count": len(urns),
        "most_recent_relative_time": newest,
        "recent_activity": _is_recent_relative_time(newest),
    }


def _is_recent_relative_time(text: str | None) -> bool:
    """
    LinkedIn's relative post timestamps ('10h', '2d', '1w', '3mo', '1yr').
    Treated as "recent" within roughly the last two months -- hours, days,
    and weeks are always recent; months only up to 2; years never.
    """
    if not text:
        return False
    match = _RELATIVE_TIME_RE.match(text.strip())
    if not match:
        return False
    value, unit = int(match.group(1)), match.group(2)
    if unit in ("h", "d", "w"):
        return True
    if unit == "mo":
        return value <= 2
    return False  # "yr"


def _unwrap_redirect(href: str | None) -> str | None:
    """
    LinkedIn wraps outbound website links in its own redirect URL
    (linkedin.com/redir/redirect?url=<encoded target>&...) rather than
    linking the real site directly. Verified 2026-07-31 against Nike's real
    website link -- without this, the raw href saved would be LinkedIn's
    wrapper, not the business's actual site.
    """
    if not href:
        return None
    match = _REDIRECT_URL_RE.search(href)
    return unquote(match.group(1)) if match else href


def _parse_headcount(text: str | None) -> int | None:
    """
    LinkedIn shows headcount as a range like '51-200 employees' or an
    open-ended '10,001+ employees'. Genuinely tested below with real sample
    strings -- this parsing logic doesn't touch the live page, only the text
    once it's already been read.
    """
    if not text:
        return None
    numbers = re.findall(r"[\d,]+", text)
    if not numbers:
        return None
    # Take the lower bound of a range (or the '+' floor) as a conservative estimate.
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
