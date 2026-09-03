"""
Email lookup via Hunter.io's Email Finder -- finds a real email address for
a person at a company, given their full name and the company's domain.

Same exact shape of lookup findymail.py already does (name+domain -> email),
same public interface (find_email/HunterNotConfigured/HunterLookupFailed
mirror FindymailNotConfigured/FindymailLookupFailed), so scheduler.py's
_maybe_find_email() can call whichever provider is active with no other
code changes -- see that function's own docstring for the provider
decision this file is part of testing.

LIVE-VERIFIED 2026-08-27 -- real HUNTER_API_KEY tested with two real calls
against the production endpoint (one that hit Hunter's own PII-suppression
list for a public figure, one that returned a clean 200 with the exact
response shape this module expects). Auth confirmed working. Being run
first on Hunter's 50 free credits/month before deciding whether to move to
Icypeas -- see scheduler.py's _maybe_find_email() docstring for the
go/no-go criteria on that decision. Findymail (paid, ~$49/mo full price,
~$20/mo lower tier) was set aside in favor of testing the free option
first.
"""

from __future__ import annotations

import httpx

from agent import config

_EMAIL_FINDER_ENDPOINT = "https://api.hunter.io/v2/email-finder"
_DOMAIN_SEARCH_ENDPOINT = "https://api.hunter.io/v2/domain-search"
_TIMEOUT_SECONDS = 20.0


class HunterNotConfigured(RuntimeError):
    """Raised when HUNTER_API_KEY isn't set -- callers should treat this the
    same way findymail.FindymailNotConfigured is treated: a normal,
    expected "can't do this one thing yet" outcome, not a crash."""


class HunterLookupFailed(RuntimeError):
    """Raised for a real API error (bad key, no credits left this month,
    network failure) -- distinct from a clean "no email found for this
    person", which is not an error and returns None instead (see
    find_email())."""


def find_email(name: str, domain: str) -> str | None:
    """
    Look up an email for `name` at `domain` (e.g. "tesla.com", not a full
    URL -- same normalization scheduler.py already does before calling
    findymail.find_email(), reused as-is for this call site).

    Returns the found email, or None if Hunter has no confident match --
    a normal, expected outcome for some leads, not a failure. Raises
    HunterLookupFailed only for a genuine API/network problem (bad key, no
    credits, timeout), matching findymail.find_email()'s exact error
    contract so scheduler.py's existing per-lead try/except handles either
    provider identically.

    Splits `name` into first/last for Hunter's required parameters --
    Hunter's Email Finder needs first_name + last_name, not a single full
    name field (unlike Findymail's /search/name endpoint). A single-word
    name (no space) is passed as first_name only, matching Hunter's docs
    on making first_name+domain a valid request on its own.
    """
    api_key = config.HUNTER_API_KEY
    if not api_key:
        raise HunterNotConfigured("HUNTER_API_KEY is not set in agent/.env.")

    parts = name.strip().split(maxsplit=1)
    first_name = parts[0] if parts else name
    last_name = parts[1] if len(parts) > 1 else None

    params = {"domain": domain, "first_name": first_name, "api_key": api_key}
    if last_name:
        params["last_name"] = last_name

    try:
        response = httpx.get(_EMAIL_FINDER_ENDPOINT, params=params, timeout=_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise HunterLookupFailed(f"Hunter request failed: {exc}") from exc

    if response.status_code == 401:
        raise HunterLookupFailed("Hunter rejected the API key (401) -- check HUNTER_API_KEY.")
    if response.status_code == 429:
        raise HunterLookupFailed("Hunter rate limit or monthly credit limit reached (429).")
    if response.status_code >= 400:
        raise HunterLookupFailed(f"Hunter returned HTTP {response.status_code}: {response.text[:200]}")

    body = response.json()
    data = body.get("data") or {}
    email = data.get("email")
    return email or None


def find_company_emails(domain: str) -> str | None:
    """
    Fallback for when no founder/decision-maker name was detected (so
    find_email() above has nothing to search a person by): Hunter's Domain
    Search endpoint takes just a company domain and returns whatever real
    email addresses it has on file for that domain -- generic role
    addresses (info@, sales@, contact@) as well as any named people it
    knows about, no name input required. This is what lets scheduler.py's
    _maybe_find_email() still produce an email lead for a company whose
    founder/decision-maker couldn't be identified, rather than that lead's
    email side being a dead end.

    Returns the single best email Hunter has on file (its own `emails`
    array is pre-sorted by confidence -- the first entry is Hunter's own
    top pick), or None if Hunter has nothing for this domain. Same
    HunterLookupFailed/HunterNotConfigured error contract as find_email()
    above, so the caller's existing per-lead try/except handles both
    identically.

    Uses more Hunter credits per successful lookup than find_email() (this
    endpoint returns a full page of company data, not one targeted match)
    -- see this module's own docstring on the 50 free-credits/month
    ceiling; calling this as a fallback (not the primary path) keeps it to
    only the leads find_email() couldn't already resolve.
    """
    api_key = config.HUNTER_API_KEY
    if not api_key:
        raise HunterNotConfigured("HUNTER_API_KEY is not set in agent/.env.")

    params = {"domain": domain, "api_key": api_key, "limit": 5}

    try:
        response = httpx.get(_DOMAIN_SEARCH_ENDPOINT, params=params, timeout=_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise HunterLookupFailed(f"Hunter request failed: {exc}") from exc

    if response.status_code == 401:
        raise HunterLookupFailed("Hunter rejected the API key (401) -- check HUNTER_API_KEY.")
    if response.status_code == 429:
        raise HunterLookupFailed("Hunter rate limit or monthly credit limit reached (429).")
    if response.status_code >= 400:
        raise HunterLookupFailed(f"Hunter returned HTTP {response.status_code}: {response.text[:200]}")

    body = response.json()
    data = body.get("data") or {}
    emails = data.get("emails") or []
    if not emails:
        return None
    return emails[0].get("value") or None
