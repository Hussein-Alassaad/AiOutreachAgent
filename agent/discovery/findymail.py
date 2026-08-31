"""
Email lookup via Findymail -- finds a real, verified email address for a
person at a company, given their name and the company's domain.

============================================================================
LIVE-VERIFIED 2026-08-22 -- real key, two real calls against the real API
============================================================================
Built against Findymail's own published API docs (https://findymail.com/api
and https://app.findymail.com/docs/, fetched 2026-08-21), then confirmed
against the real endpoint once a real FINDYMAIL_API_KEY was added:
  1. A real match (name="Elon Musk", domain="tesla.com") returned HTTP 200
     with {"contact": {"email": "elon.musk@teslamotors.com", ...}} --
     exactly the documented shape, no surprises.
  2. A deliberate no-match (a nonsense name at a real domain) returned HTTP
     200 -- NOT a 404 -- with a `contact` object still present but
     `"email": null`. This was the one gap the docs never specified;
     find_email()'s `not contact.get("email")` check below already handles
     it correctly (confirmed returning None, not raising), so no code
     change was needed once this was known for certain.
No confidence/verification-status field exists in the response (unlike
Hunter.io/Apollo, which do include one) -- every email this returns is
accepted as-is with no internal quality tier to filter on. That's a real,
permanent limitation of this API, not something left unverified.
============================================================================

Why this exists instead of Hunter.io/Apollo (the more commonly recommended
tools -- see the strategy doc in the repo's Downloads folder): this agent's
LinkedIn discovery (discovery/linkedin.py) already finds real, qualified
companies AND, when available, the founder/decision-maker's name
(analysis/founder.py) -- Findymail's specific strength is turning a
KNOWN person+company into a verified email cheaply, which is exactly this
shape of lookup. Hunter/Apollo are stronger at the opposite direction
(discovering companies you don't already know about), which this agent
doesn't need help with -- it already does that via LinkedIn search.
"""

from __future__ import annotations

import httpx

from agent import config

_BASE_URL = "https://app.findymail.com/api"
_SEARCH_NAME_ENDPOINT = f"{_BASE_URL}/search/name"
_TIMEOUT_SECONDS = 20.0


class FindymailNotConfigured(RuntimeError):
    """Raised when FINDYMAIL_API_KEY isn't set -- callers should treat this
    the same way whatsapp_send.py's WhatsAppNotConfigured is treated: a
    normal, expected "can't do this one thing yet" outcome, not a crash."""


class FindymailLookupFailed(RuntimeError):
    """Raised for a real API error (bad key, no credits, paused
    subscription, network failure) -- distinct from a clean "no email
    found for this person", which is not an error and returns None instead
    (see find_email())."""


def find_email(name: str, domain: str) -> str | None:
    """
    Look up a verified email for `name` at `domain` (e.g. "tesla.com", not
    a full URL -- callers must strip the scheme/path themselves; see
    scheduler.py's call site for how a LinkedIn-discovered company website
    is normalised to a bare domain first).

    Returns the found email, or None if Findymail has no match for this
    person (a normal, expected outcome -- not every discovered lead's
    founder will be findable, same as WhatsApp-number detection already
    expects for a different field). Raises FindymailLookupFailed only for
    a genuine API/network problem (bad key, no credits, timeout) -- the
    caller (scheduler.py) should let that propagate into its existing
    per-lead try/except and log_error() isolation, same as every other
    per-lead external call already does, rather than silently swallowing a
    real configuration problem as if it were just "no email found".
    """
    api_key = config.FINDYMAIL_API_KEY
    if not api_key:
        raise FindymailNotConfigured("FINDYMAIL_API_KEY is not set in agent/.env.")

    try:
        response = httpx.post(
            _SEARCH_NAME_ENDPOINT,
            json={"name": name, "domain": domain},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise FindymailLookupFailed(f"Network error calling Findymail: {exc}") from exc

    # Findymail's docs only specify 402 (no credits) / 423 (paused
    # subscription) as documented non-2xx cases -- any other non-2xx
    # (401 bad key, 429 rate limit, 5xx) is treated the same way: a real
    # failure the caller needs to know about, not a "no email found" result.
    if response.status_code == 402:
        raise FindymailLookupFailed("Findymail account has insufficient credits.")
    if response.status_code == 423:
        raise FindymailLookupFailed("Findymail subscription is paused.")
    if response.status_code >= 400:
        raise FindymailLookupFailed(f"Findymail returned HTTP {response.status_code}: {response.text[:300]}")

    try:
        data = response.json()
    except ValueError as exc:
        raise FindymailLookupFailed(f"Findymail returned a non-JSON response: {response.text[:300]}") from exc

    # LIVE-CONFIRMED 2026-08-22: a no-match still returns HTTP 200 with a
    # `contact` object present but `email: null` (not a 404, not a missing
    # `contact` key) -- see module docstring point 2. The `not contact` half
    # of this check is defensive belt-and-suspenders for a shape Findymail
    # hasn't actually been observed to send; the `not contact.get("email")`
    # half is the one that fires in real use.
    contact = data.get("contact") if isinstance(data, dict) else None
    if not contact or not contact.get("email"):
        return None

    return contact["email"]
