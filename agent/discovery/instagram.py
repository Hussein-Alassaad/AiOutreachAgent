"""
Instagram search and profile reading. Target 20/account/day. READ ONLY.

Hashtags and explore pages. Reads bio, followers, post count, and website.
This module NEVER sends -- Instagram's ToS bans automated cold outreach and
automating it risks the account. Only discovery/reading happens here.

============================================================================
VERIFIED against real, live, logged-out public Instagram pages -- 2026-07-31,
RE-VERIFIED 2026-08-03 (hashtag page behaviour changed)
============================================================================
Instagram serves its actual rendered page (not the SSR-disabled shell a bare
HTTP request gets -- confirmed separately) to a real browser, even logged
out, for PUBLIC hashtag/profile/post pages. Two things the original guesses
got wrong, found by inspecting real dumps (instagram.com/nike/, a #bakery
hashtag page, and a real reel page):

**RE-VERIFIED 2026-08-03 -- caught during a real supervised discovery run
that found 0 Instagram leads on every attempt regardless of niche.**
`build_hashtag_url`'s URL shape (`/explore/tags/<tag>/`) still resolves, but
Instagram now hard-redirects it to a generic search page
(`/explore/search/keyword/?q=%23<tag>`, confirmed universal across multiple
tags -- not a #bakery-specific quirk). That page's own post/reel links
render client-side well after `domcontentloaded` fires (confirmed empty
immediately on load; live testing found the delay inconsistent -- a 5s wait
found 0 results on one run and 21 on the next against the identical tag) --
scheduler.py's `_discover_instagram` had no wait at all after `page.goto`
here, so it was reading the page before any results existed. Fixed with an
explicit `page.wait_for_timeout(7_000)` after navigation (7s for margin over
the observed variance). The href-matching logic in extract_hashtag_results()
below is unaffected -- once given enough time to render, the same relative
`/p/<code>/` / `/reel/<code>/` link pattern still works on this redirected
page (confirmed real hrefs are relative, not absolute, so the existing
`href^='/p/'` prefix match was never actually the problem).

  1. Hashtag pages now show Reels (/reel/... links) alongside classic posts
     (/p/...), with NO <article> wrapper -- Instagram's grid items sit behind
     deeply auto-generated, non-semantic CSS classes (e.g. "x1i10hfl xjbqb8w
     ...") that are useless to select on and will keep changing. Matching on
     the href prefix is the only stable part.
  2. The reliable data isn't in visible DOM text at all -- it's in the page's
     `og:description` / `og:url` meta tags (follower/following/post counts as
     Instagram's own formatted string, and the canonical URL with the owning
     username as its first path segment). Semantic, Instagram-authored
     strings, not auto-generated classnames -- far less likely to silently
     break than a CSS selector, and they survive even on pages that also
     render a login-wall overlay over the visible content (confirmed on the
     reel page dump).

**RE-VERIFIED 2026-08-03 -- the bio/website half of point 2 above broke.**
The `"biography":"..."` / `"bio_links":[...]` embedded-JSON fields this
module originally read are gone or wrong now: caught when a real supervised
discovery run returned the identical bio ("🌟") for 5 unrelated real
bakery accounts with 43K-584K followers. Root cause: that JSON key now
belongs to a `["PolarisViewer", ...]` entity -- the logged-in VIEWER's own
profile data, not whichever account's page is being visited. `bio_links`
doesn't exist on the page at all anymore. extract_profile()'s own docstring
has the full replacement: the real bio moved to a plain
`<meta name="description">` tag (not `og:description`, which now only has
the stats line); the website/bio-link is still visibly present but sits
behind a button with no `href` to read statically, so that field is
honestly left as always-None rather than guessed at.

Extraction below reads those meta tags via regex over the raw page content,
not DOM locators, for exactly the semantic-vs-auto-generated reason above.
============================================================================
"""

from __future__ import annotations

import html
import re

from playwright.sync_api import Page

HASHTAG_URL = "https://www.instagram.com/explore/tags/{tag}/"

_STATS_RE = re.compile(
    r"([\d.,KMk]+)\s+Followers,\s+([\d.,KMk]+)\s+Following,\s+([\d.,KMk]+)\s+Posts",
    re.IGNORECASE,
)
# RE-VERIFIED 2026-08-03: the old `"biography":"..."` embedded-JSON approach
# is gone -- see extract_profile()'s docstring for the full story. This
# reads the plain <meta name="description"> tag instead, whose content is
# Instagram's own '<stats> - @<username> on Instagram: "<bio text>"' string
# -- everything after `on Instagram: "` up to the closing `"` is the real
# bio. Matches a literal `"`, not `&quot;`: Playwright's get_attribute()
# (what _meta_content() below actually uses) returns the attribute already
# HTML-entity-decoded by the browser -- only page.content()'s raw source
# would still show `&quot;`, confirmed live (get_attribute() returned real
# `"` characters, which is why an earlier version of this regex matching
# `&quot;` literally found nothing against the live page despite working
# against a saved raw-HTML dump).
_DESCRIPTION_BIO_RE = re.compile(r'on Instagram:\s*"(.*)"\s*$', re.DOTALL)
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
    # RE-VERIFIED 2026-08-03: same root cause as discovery/linkedin.py's
    # navigation timeouts that day -- the default wait_until="load" waits for
    # every resource (images, trackers, ads) to finish, not just the DOM the
    # og:url meta tag needs; _meta_content()'s own wait_for already covers
    # the "did the tag actually attach yet" case.
    page.goto(post_url, timeout=30_000, wait_until="domcontentloaded")

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
    Reads a profile page's bio and follower/post counts. Verified 2026-07-31
    against a real public profile page (instagram.com/nike/), then
    **RE-VERIFIED 2026-08-03 with the bio extraction rewritten** -- see
    module docstring's summary. Full story, caught during a real supervised
    discovery run where every one of 5 real bakery accounts (43K-584K real
    followers) came back with bio == "🌟" (a single star emoji,
    identical across every unrelated account):

    The old `"biography":"..."` embedded-JSON regex WAS matching something
    real -- just not the profile owner. Instagram's hydration payload now
    puts that key under a `["PolarisViewer", ...]` entity -- the currently
    logged-in VIEWER's own data (Hussein's account), not toicroissant's/
    kookies.bakery's/etc. There is exactly one `"biography"` occurrence on
    the whole page and it's always the viewer's, which is why every lead
    got the identical wrong value. The corresponding `"bio_links"` key is
    gone from the page entirely -- confirmed via a plain substring search,
    not just a regex miss.

    The real bio text is genuinely still present, just moved: a plain
    `<meta name="description" content="...">` tag (distinct from
    `og:description`, which only has the stats line now) holds Instagram's
    own `<stats> - @<username> on Instagram: "<bio text>"` string.
    _DESCRIPTION_BIO_RE pulls everything after `on Instagram: "` up to the
    closing `"` -- see that regex's own comment for why it matches a plain
    `"` rather than `&quot;`.

    Website/bio-link extraction was NOT fixed here and now always returns
    None/False -- the visible link ("share.google/... and N more") sits
    behind a `<button>` with no `href` at all (confirmed live: Instagram
    now requires an actual click to reveal/navigate the bio link, there is
    no static attribute to read). Website presence was already just one of
    several qualify.py signals (2 points out of a multi-factor score), not
    load-bearing on its own, so this is a real, honestly-flagged gap rather
    than something patched with a guess.
    """
    bio = ""
    description_meta = _meta_content(page, "description", attr="name") or ""
    bio_match = _DESCRIPTION_BIO_RE.search(description_meta)
    if bio_match:
        bio = html.unescape(bio_match.group(1)).strip()

    website = None  # see docstring -- no longer extractable without a real click

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


def _meta_content(page: Page, name_or_property: str, attr: str = "property") -> str | None:
    """
    Reads one <meta property="..."> or <meta name="..."> tag's content
    (`attr` picks which), waiting briefly for it to attach first -- meta
    tags land with the page's initial render rather than needing the full
    'load' event, but a short explicit wait is cheaper and more honest than
    assuming that timing never changes. `attr="name"` added 2026-08-03 for
    the plain `<meta name="description">` tag extract_profile() now reads
    (Instagram's OpenGraph tags use `property=`, but this one doesn't).
    """
    locator = page.locator(f"meta[{attr}='{name_or_property}']").first
    try:
        locator.wait_for(state="attached", timeout=5_000)
    except Exception:  # noqa: BLE001 -- tag may genuinely never appear (deleted post, hard login wall)
        pass
    return _safe_attr(locator, "content")


def _safe_text(locator) -> str | None:
    """
    text_content() reads the DOM directly with no visibility wait -- see the
    identical fix in discovery/linkedin.py's own _safe_text for why
    inner_text()'s default visibility wait is a real hang risk on a page with
    off-screen/hidden matches, confirmed live 2026-08-03 during a supervised
    LinkedIn discovery run. Applied here too since this module has the same
    "match everything with this href prefix" pattern as linkedin.py's search
    results (extract_hashtag_results above).
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
