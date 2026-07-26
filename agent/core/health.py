"""
Detects account problems and records warning TYPE plus specific REASON.

Warnings must be specific ('CAPTCHA triggered', 'Login challenge detected'),
never generic. On warning: pause the account and log the reason. The
redistribute_flag column is deliberately never touched here -- core rule R9:
redistribution is Hussein's manual decision, never automatic, so this module
only ever reports a problem, it never decides what to do about capacity.
"""

from __future__ import annotations

from playwright.sync_api import Page, Response

from agent.db import repositories as repo

# Phrases that show up on a real login-challenge / CAPTCHA / restriction page.
# These won't fire against the neutral test pages used in Phase 2 -- they exist
# now so Phase 3, which points these same checks at real LinkedIn/Instagram
# pages, doesn't need this detection logic rebuilt from scratch.
CHALLENGE_PHRASES = {
    "captcha": ["captcha", "verify you're human", "i'm not a robot"],
    "login_challenge": ["confirm your identity", "verify it's you", "unusual login activity"],
    "checkpoint": ["checkpoint required", "help us confirm", "we restrict certain activity"],
    "rate_limited": ["try again later", "too many requests"],
}


def check_navigation(response: Response | None) -> tuple[bool, str | None, str | None]:
    """
    Did the page even load successfully?

    Returns (ok, warning_type, reason). A None response means Playwright's
    navigation itself failed (DNS error, timeout, connection refused) before any
    HTTP response came back at all -- that's as basic a failure as exists, and
    is exactly what the Phase 2 test below deliberately triggers to prove this
    path works.
    """
    if response is None:
        return False, "navigation_failed", "No response from target (DNS/timeout/connection error)"

    status = response.status
    if status == 403:
        return False, "blocked", f"HTTP 403 Forbidden from {response.url}"
    if status == 429:
        return False, "rate_limited", f"HTTP 429 Too Many Requests from {response.url}"
    if status >= 400:
        return False, "http_error", f"HTTP {status} from {response.url}"

    return True, None, None


def check_page_content(page: Page) -> tuple[bool, str | None, str | None]:
    """
    Scan the loaded page's visible text for known challenge/CAPTCHA phrasing.

    Not exercised in Phase 2's neutral-page tests (those pages contain none of
    these phrases, by design), but implemented now with real platform wording
    so Phase 3 can call this unchanged against actual LinkedIn/Instagram pages.
    """
    try:
        content = page.content().lower()
    except Exception:  # noqa: BLE001 -- a page that can't even be read is itself a problem
        return False, "unreadable_page", "Could not read page content"

    for warning_type, phrases in CHALLENGE_PHRASES.items():
        for phrase in phrases:
            if phrase in content:
                return False, warning_type, f"Matched phrase: '{phrase}'"

    return True, None, None


def record_warning(account_id: str, warning_type: str, reason: str) -> None:
    """
    Pause the account and log a specific warning. This is the ONLY effect a
    detected problem has -- no automatic redistribution, no automatic retry.
    Hussein sees the type + reason on the dashboard's Account Health monitor
    (Phase 9) and decides what happens next.
    """
    repo.set_account_warning(account_id, warning_type, reason)
