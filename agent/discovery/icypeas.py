"""
Email lookup via Icypeas -- second provider alongside Hunter (see
discovery/hunter.py's own docstring for the two-tier find_email/
find_company_emails shape this module mirrors), added 2026-09-20 per the
platform owner's explicit request after comparing per-month pricing
(50 free credits/month + ~$19/month paid tier -- the cheapest option
found that also uses a genuinely different data source than Hunter's own
crawled database, not just a re-skin of it).

============================================================================
NOT YET LIVE-VERIFIED against a real API key -- built directly from
Icypeas's own official API docs (https://api-doc.icypeas.com/, researched
2026-09-20), not from a live test call. Every endpoint path, field name,
and status value below is taken from their documentation as written; the
one explicitly flagged uncertainty is the exact nesting of a FULLY
populated single-search result (their docs render individual fields on
separate pages, not one complete worked example) -- _parse_search_item()
below is written defensively (every field read with .get(), never assumed
present) specifically because of that gap. The first real call with a real
ICYPEAS_API_KEY should be treated as this module's actual live
verification, the same way hunter.py's blocklist/quality-gate fixes were
each confirmed against real API responses before being trusted.
============================================================================

REAL, IMPORTANT DIFFERENCE FROM HUNTER: Icypeas's endpoints are
ASYNCHRONOUS (submit a search, poll a separate endpoint for the result) --
Hunter's are synchronous (one call, one response). Both find_email() and
find_company_emails() below hide this behind the same synchronous return
shape Hunter's own functions use (poll internally, block until done or
timed out), so scheduler.py's _maybe_find_email() needs zero changes to
call whichever provider is active.

REAL, IMPORTANT SCOPE DIFFERENCE: Icypeas's own "domain-search" endpoint
(this module's find_company_emails()) only ever returns GENERIC/role-based
addresses (info@, contact@, sales@) for a domain -- it does NOT enumerate
real named people at a company the way Hunter's Domain Search sometimes
does. This is actually a fine fit for how _maybe_find_email() uses this
tier already: it's the fallback for when NO founder/decision-maker name
was detected, and the existing "generic-over-personal" preference in
hunter.py's own find_company_emails() already treats a role-based address
as the PREFERRED outcome over a named individual's guess -- so this
narrower scope costs nothing in practice for this codebase's actual usage
pattern, even though it means Icypeas cannot be used as a full drop-in
replacement for every way Hunter's Domain Search could theoretically be
used.
"""

from __future__ import annotations

import time

import httpx

from agent import config

_BASE_URL = "https://app.icypeas.com/api"
_EMAIL_SEARCH_ENDPOINT = f"{_BASE_URL}/email-search"
_DOMAIN_SEARCH_ENDPOINT = f"{_BASE_URL}/domain-search"
_POLL_ENDPOINT = f"{_BASE_URL}/bulk-single-searchs/read"
_TIMEOUT_SECONDS = 20.0

# How long to keep polling for a result before giving up -- Icypeas's docs
# give no SLA for how long a single-item search takes to leave
# NONE/SCHEDULED/IN_PROGRESS, so this is a judgment call: long enough that a
# normal-speed real search isn't cut off early, short enough that one slow
# lookup can't stall an entire discovery cycle's per-lead loop (which is
# already budget-conscious about wall-clock time -- see scheduler.py's own
# pacing comments elsewhere in this codebase).
_POLL_TIMEOUT_SECONDS = 45.0
_POLL_INTERVAL_SECONDS = 3.0

# Verbatim from Icypeas's own /how-works/search_statuses/ docs -- see this
# module's docstring for the one flagged uncertainty (bare vs "DEBITED_"-
# prefixed variants may be aliases of each other across API versions, so
# both forms are checked everywhere a terminal status is tested).
_PENDING_STATUSES = {"NONE", "SCHEDULED", "IN_PROGRESS"}
_FOUND_STATUSES = {"FOUND", "DEBITED"}
_NOT_FOUND_STATUSES = {"NOT_FOUND", "DEBITED_NOT_FOUND"}
_TERMINAL_FAILURE_STATUSES = {"BAD_INPUT", "INSUFFICIENT_FUNDS", "ABORTED"}

# Verbatim from /how-works/certainties/ -- the two highest tiers Icypeas
# itself describes as <=1%/<=5% expected bounce rate. Anything else
# ("not_found", "undeliverable", or an unrecognized future value) is
# treated as not-confident-enough, same conservative posture as hunter.py's
# _MIN_SCORE gate -- a wrong guess here costs a real bounce (see hunter.py's
# own 2026-09-20 fix, prompted by exactly that), so this stays strict rather
# than permissive.
_ACCEPTABLE_CERTAINTIES = {"ultra_sure", "very_sure", "probable"}


class IcypeasNotConfigured(RuntimeError):
    """Raised when ICYPEAS_API_KEY isn't set -- callers should treat this
    the same way hunter.py's HunterNotConfigured / findymail.py's
    FindymailNotConfigured are treated: a normal, expected "can't do this
    one thing yet" outcome, not a crash."""


class IcypeasLookupFailed(RuntimeError):
    """Raised for a real API error (bad key, rate limit, malformed request,
    a poll that never left NONE/SCHEDULED/IN_PROGRESS within
    _POLL_TIMEOUT_SECONDS) -- distinct from a clean "no email found",
    which is not an error and returns None instead (see find_email()/
    find_company_emails() below)."""


def _auth_headers(api_key: str) -> dict[str, str]:
    """Icypeas's own docs show the raw key with NO "Bearer " prefix (unlike
    Hunter's X-API-KEY header or Findymail's Authorization: Bearer -- each
    provider's own convention, not something this codebase gets to choose).
    """
    return {"Authorization": api_key, "Content-Type": "application/json"}


def _submit_search(endpoint: str, payload: dict, api_key: str) -> str:
    """Submits a search task and returns its `_id` for polling. Raises
    IcypeasLookupFailed for a real HTTP-level error or a malformed success
    response (submission accepted but no id came back -- would otherwise
    cause a silent, confusing hang in the poll loop)."""
    try:
        response = httpx.post(endpoint, json=payload, headers=_auth_headers(api_key), timeout=_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise IcypeasLookupFailed(f"Icypeas request failed: {exc}") from exc

    if response.status_code == 401:
        raise IcypeasLookupFailed("Icypeas rejected the API key (401) -- check ICYPEAS_API_KEY.")
    if response.status_code == 429:
        raise IcypeasLookupFailed("Icypeas rate limit reached (429).")
    if response.status_code >= 400:
        raise IcypeasLookupFailed(f"Icypeas returned HTTP {response.status_code}: {response.text[:300]}")

    try:
        body = response.json()
    except ValueError as exc:
        raise IcypeasLookupFailed(f"Icypeas returned a non-JSON response: {response.text[:300]}") from exc

    # A 200 with success=false is Icypeas's own documented validation-error
    # shape (see /how-works/validation-errors/) -- a real, permanent failure
    # for THIS request (bad input shape), not a "no match" outcome.
    if body.get("success") is False:
        raise IcypeasLookupFailed(f"Icypeas rejected the request: {body.get('validationErrors')}")

    search_id = body.get("_id") or (body.get("item") or {}).get("_id")
    if not search_id:
        raise IcypeasLookupFailed(f"Icypeas accepted the search but returned no id to poll: {body}")
    return search_id


def _poll_for_result(search_id: str, api_key: str) -> dict:
    """Polls Icypeas's own read endpoint until the item leaves its pending
    states or _POLL_TIMEOUT_SECONDS elapses. Returns the raw `item` dict on
    a terminal status (found, not-found, or a real failure status);
    raises IcypeasLookupFailed only if the poll window itself expires
    without ever reaching a terminal state (a genuine "we don't know" --
    not the same as a clean not-found)."""
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            response = httpx.post(
                _POLL_ENDPOINT, json={"id": search_id}, headers=_auth_headers(api_key), timeout=_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise IcypeasLookupFailed(f"Icypeas poll request failed: {exc}") from exc

        if response.status_code >= 400:
            raise IcypeasLookupFailed(f"Icypeas poll returned HTTP {response.status_code}: {response.text[:300]}")

        try:
            body = response.json()
        except ValueError as exc:
            raise IcypeasLookupFailed(f"Icypeas poll returned a non-JSON response: {response.text[:300]}") from exc

        item = body.get("item") or {}
        status = item.get("status")
        if status not in _PENDING_STATUSES:
            return item
        time.sleep(_POLL_INTERVAL_SECONDS)

    raise IcypeasLookupFailed(
        f"Icypeas search {search_id!r} never left a pending status within {_POLL_TIMEOUT_SECONDS}s."
    )


def _best_email_from_item(item: dict, *, context: str) -> str | None:
    """Picks the first email in `item["emails"]` whose certainty clears
    _ACCEPTABLE_CERTAINTIES, or None. Defensive against every field being
    absent -- see this module's own docstring for why the exact response
    shape isn't 100% confirmed from docs alone yet."""
    status = item.get("status")
    if status in _TERMINAL_FAILURE_STATUSES:
        # BAD_INPUT / INSUFFICIENT_FUNDS / ABORTED are real problems, not a
        # clean "no email found" -- surfaced as a real error so the caller's
        # per-lead try/except and log_error() isolation sees it, same
        # contract as a Hunter/Findymail API failure.
        raise IcypeasLookupFailed(f"Icypeas search for {context} ended in status={status!r}.")
    if status in _NOT_FOUND_STATUSES:
        return None

    emails = item.get("emails") or []
    for candidate in emails:
        value = candidate.get("email")
        certainty = candidate.get("certainty")
        if value and certainty in _ACCEPTABLE_CERTAINTIES:
            return value
    return None


def find_email(name: str, domain: str) -> str | None:
    """
    Look up an email for `name` at `domain` -- the Icypeas equivalent of
    hunter.py's find_email() / findymail.py's find_email(), same shape and
    error contract so scheduler.py's _maybe_find_email() can call whichever
    provider is active with no other code change.

    Splits `name` into first/last, matching Icypeas's required
    firstname/lastname fields (see this module's docstring's verbatim
    request example) -- same splitting approach hunter.py's find_email()
    already uses for its own first_name/last_name fields.
    """
    api_key = config.ICYPEAS_API_KEY
    if not api_key:
        raise IcypeasNotConfigured("ICYPEAS_API_KEY is not set in agent/.env.")

    parts = name.strip().split(maxsplit=1)
    first_name = parts[0] if parts else name
    last_name = parts[1] if len(parts) > 1 else ""

    search_id = _submit_search(
        _EMAIL_SEARCH_ENDPOINT,
        {"firstname": first_name, "lastname": last_name, "domainOrCompany": domain},
        api_key,
    )
    item = _poll_for_result(search_id, api_key)
    return _best_email_from_item(item, context=f"{name} @ {domain}")


def find_company_emails(domain: str) -> str | None:
    """
    Domain-wide fallback when no founder/decision-maker name was detected --
    the Icypeas equivalent of hunter.py's find_company_emails(). See this
    module's own docstring for the real, important scope difference: this
    only ever returns a GENERIC/role-based address (info@, contact@,
    sales@), never a named individual's -- which is actually the preferred
    outcome for this exact call site already (hunter.py's own
    "generic-over-personal preference" comment), so it costs nothing here
    in practice.
    """
    api_key = config.ICYPEAS_API_KEY
    if not api_key:
        raise IcypeasNotConfigured("ICYPEAS_API_KEY is not set in agent/.env.")

    search_id = _submit_search(_DOMAIN_SEARCH_ENDPOINT, {"domainOrCompany": domain}, api_key)
    item = _poll_for_result(search_id, api_key)
    return _best_email_from_item(item, context=f"domain search on {domain}")
