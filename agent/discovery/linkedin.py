"""
LinkedIn search and profile reading. Target 30/account/day.

Reads company name, description, headcount, website. Adapts its search terms
when results come back weak -- agentic, not fixed.

============================================================================
VERIFIED against real, live LinkedIn pages -- 2026-07-31, RE-VERIFIED 2026-08-03
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
  2. Profile reading (extract_company_profile) -- **RE-VERIFIED 2026-08-03,
     selectors changed.** The 2026-07-31 claim that `data-test-id="about-us__*"`
     attributes existed on the bare company page was found to be stale during
     a real supervised discovery run: LinkedIn had redesigned this page and
     those attributes no longer exist anywhere on it (confirmed empty via
     `eval_on_selector_all('[data-test-id]', ...)`). extract_company_profile()'s
     own docstring has the full current selector detail; the short version:
     this function now expects `page` to be on the **`/about`** subpage, not
     the bare page (the bare page is missing the Website/Industry/size info
     entirely) -- scheduler.py's caller was updated to navigate there. The
     original claim that `/about` "redirects anonymous visits to login" turned
     out to only ever have been about *anonymous* visits; every real call site
     here is authenticated (agent/core/session.py), so that redirect never
     actually applied to this codebase's own usage -- confirmed live: `/about`
     renders full real content when logged in. Fixed one real bug found this
     way: the website link's href is a `linkedin.com/redir/redirect?url=...`
     wrapper, not the site itself -- the original code returned that wrapper
     URL verbatim.
============================================================================
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

from playwright.sync_api import Page

SEARCH_URL = "https://www.linkedin.com/search/results/companies/"

# Facet IDs confirmed 2026-08-02 against a real, authenticated search --
# found by applying each filter through LinkedIn's own UI (Locations /
# Industry dropdowns on the company search results page) and reading the
# facet parameter LinkedIn added to the URL, e.g. companyHqGeo=["101834488"]
# for Lebanon. Only entries confirmed this exact way go here. This is a
# small, growing lookup, not an exhaustive list of LinkedIn's facet IDs --
# add more the same way (apply the filter live, read the resulting URL) as
# new target_location/target_industry values come up.
LOCATION_FACETS = {
    "lebanon": "101834488",
}
INDUSTRY_FACETS = {
    "food and beverage": "34",
    "food and beverage services": "34",
}

_COMPANY_HREF_RE = re.compile(r"^https://www\.linkedin\.com/company/[^/?]+/?")
_REDIRECT_URL_RE = re.compile(r"[?&]url=([^&]*)")
_ACTIVITY_URN_RE = re.compile(r'data-urn="(urn:li:activity:[^"]*)"')
# RE-VERIFIED 2026-08-03: the actor sub-description's inner <span> now
# carries `aria-hidden="true"` (confirmed against a real post on Wooden
# Bakery's Posts tab) -- the original regex assumed a bare `<span>` with no
# attributes and silently matched nothing once LinkedIn added it, making
# every real post read as having no relative-time text.
_POST_TIME_RE = re.compile(r'update-components-actor__sub-description[^>]*>\s*<span[^>]*><!---->(\d+[a-zA-Z]+)')
_RELATIVE_TIME_RE = re.compile(r"^(\d+)(h|d|w|mo|yr)$")


def build_search_url(niche: str, location: str, industry: str = "") -> str:
    """
    Build a LinkedIn company-search URL from the dashboard's niche/location/
    industry settings.

    Uses a precise facet parameter (LOCATION_FACETS/INDUSTRY_FACETS above)
    whenever `location`/`industry` matches a verified entry -- exactly what
    LinkedIn's own UI produces when you apply that filter by hand. Anything
    not in those two small lookups still works, just folded into the
    free-text `keywords` parameter instead, same as before this was verified.
    """
    keyword_parts = [niche]
    facet_params: dict[str, str] = {}

    geo_id = LOCATION_FACETS.get(location.strip().lower()) if location else None
    if geo_id:
        facet_params["companyHqGeo"] = f'["{geo_id}"]'
    elif location:
        keyword_parts.append(location)

    industry_id = INDUSTRY_FACETS.get(industry.strip().lower()) if industry else None
    if industry_id:
        facet_params["industryCompanyVertical"] = f'["{industry_id}"]'
    elif industry:
        keyword_parts.append(industry)

    query = " ".join(part for part in keyword_parts if part).strip()
    origin = "FACETED_SEARCH" if facet_params else "GLOBAL_SEARCH_HEADER"
    url = f"{SEARCH_URL}?keywords={quote(query)}&origin={origin}"
    for key, value in facet_params.items():
        url += f"&{key}={quote(value)}"
    return url


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

    ============================================================================
    RE-VERIFIED live 2026-08-03 -- the dedup rule below was wrong in practice.
    A real supervised discovery run caught every saved lead's display_name
    holding the ENTIRE card's text ("Wooden Bakery Wooden Bakery Food and
    Beverage ServicesBeirutFollowWooden Bakery was founded in..."), not just
    the company name. The original rule ("first truthy text wins, keep it")
    assumed the *wrong* occurrence would have *empty* text (e.g. an image-only
    link) -- but LinkedIn's outer card-wrapping `<a>` (which wraps the image,
    the name, the whole description, and the follower count all in one link)
    also has non-empty text, and Playwright's locator order returned it before
    the inner name-only `<a>`. Confirmed via raw HTML inspection: the real
    name-only link is nested *inside* that outer one
    (`<a href="...wooden-bakery/">Wooden Bakery</a>`, no other text). Since
    the wrapper's text is always a superset of the name link's shorter text,
    keeping the SHORTEST non-empty occurrence per URL reliably picks the real
    name instead of the whole card.
    ============================================================================

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
        if not name:
            continue
        existing = results.get(url)
        if existing is None or len(name) < len(existing):
            results[url] = name

    return [
        {"profile_url": url, "display_name": name, "headline_or_bio": None}
        for url, name in results.items()
        if name  # a company with no text occurrence anywhere is unusable -- skip it
    ]


def extract_company_profile(page: Page) -> dict:
    """
    Reads a company's LinkedIn **`/about`** page: description, employee
    count, and website link. Returns the normalised shape
    discovery/qualify.py's qualify_profile() expects.

    ============================================================================
    RE-VERIFIED live 2026-08-03 -- the original 2026-07-31 selectors below had
    silently broken (LinkedIn redesigned this page; `data-test-id="about-us__*"`
    no longer exists anywhere on it -- confirmed via `page.eval_on_selector_all`
    returning an empty list). Caught via a real supervised discovery run against
    Paul Bakery Beirut: every one of 5 real, legitimate leads scored as
    "bio missing" and "zero posts" by qualify.py, which is what surfaced this.

    Current real structure (`/about`, authenticated):
      - the tagline/bio is `p.org-top-card-summary__tagline` -- also present on
        the bare (non-/about) page, but Website/Industry/size below are not,
        which is why this function now expects `page` to already be on
        `/about`, not the bare company page (scheduler.py's caller was updated
        to match).
      - `/about` no longer redirects anonymous/logged-out visits to login for
        at least this account's authenticated session (the original docstring's
        claim was specifically about *anonymous* visits, which was never
        exercised via a real login before) -- every real call site here is
        always authenticated anyway (agent/core/session.py), so this is moot
        in practice.
      - Website/Industry/Company size render as a plain `<dl>` with `<h3>`
        label text (no data-test-id, no stable class per field) inside
        `section.org-about-module__margin-bottom` -- matched here by finding
        the `<dt>` whose text is the label, then reading its following `<dd>`.
    ============================================================================
    """
    description = _safe_text(page.locator("p.org-top-card-summary__tagline").first)

    about_section = page.locator("section.org-about-module__margin-bottom").first
    website = _unwrap_redirect(_safe_attr(about_section.locator("dd a").first, "href"))
    size_text = _safe_text(_dd_after_label(about_section, "Company size"))

    return {
        "platform": "linkedin",
        "bio": description or "",
        "has_website": bool(website),
        "website": website,
        "follower_or_headcount": _parse_headcount(size_text),
        "post_count": None,       # this function doesn't visit the Posts tab -- see extract_recent_posts()
        "recent_activity": True,  # placeholder; scheduler.py overwrites both fields with extract_recent_posts()'s real read
    }


def _dd_after_label(section_locator, label_text: str):
    """
    The About `<dl>`'s fields have no stable per-field selector (see
    extract_company_profile's docstring) -- each is just a `<dt><h3>Label</h3>
    </dt><dd>value</dd>` pair in sequence. XPath is the only way to say "the
    <dd> immediately after the <dt> containing this exact label text" without
    a stable class/attribute to anchor on.
    """
    return section_locator.locator(
        f"xpath=.//dt[normalize-space(.)='{label_text}']/following-sibling::dd[1]"
    ).first


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
    """
    inner_text() waits for the element to be visible by default, which can
    hang for tens of seconds on hidden/off-screen matches (ad panels, footer
    links, related-page suggestions -- a real search page has plenty of
    `a[href*='linkedin.com/company/']` matches beyond the visible company
    cards). text_content() reads the DOM directly with no visibility wait,
    which is all this needs -- the caller only wants the link's text, not
    confirmation it's interactable.
    """
    try:
        text = locator.text_content(timeout=2_000)
        return text.strip() if text else None
    except Exception:  # noqa: BLE001 -- a missing element is expected, not exceptional
        return None


def _safe_attr(locator, attr: str) -> str | None:
    try:
        return locator.get_attribute(attr, timeout=2_000)
    except Exception:  # noqa: BLE001
        return None
