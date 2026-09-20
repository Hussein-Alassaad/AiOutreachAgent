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

import logging
from urllib.parse import urlparse

import httpx

from agent import config

_log = logging.getLogger(__name__)

_EMAIL_FINDER_ENDPOINT = "https://api.hunter.io/v2/email-finder"
_DOMAIN_SEARCH_ENDPOINT = "https://api.hunter.io/v2/domain-search"
_TIMEOUT_SECONDS = 20.0


def _auth_headers(api_key: str) -> dict[str, str]:
    """
    Hunter's key sent as a header, never as an `api_key=` query parameter.

    REAL LEAK FOUND 2026-09-19: httpx logs every request's FULL URL at INFO
    level, so the old `params={"api_key": ...}` shape wrote the live key in
    plaintext into docker logs on every single lookup -- e.g.
    "GET https://api.hunter.io/v2/domain-search?domain=...&api_key=<real key>".
    Anyone with log access (or any log shipped elsewhere) had the key.
    Hunter documents this header as an equivalent way to authenticate, so
    moving it out of the URL fixes the leak with no behaviour change.
    """
    return {"X-API-KEY": api_key}

# Quality gate thresholds (added 2026-09-17 after a real bad match: lead
# "Kedemos Education" had website=linktr.ee/KedemosEducation (a bio-link
# page, not their real site), Domain Search was run against linktr.ee
# itself, and Hunter handed back pooya@linktr.ee -- a stranger's email
# totally unrelated to the company being contacted. That's a real,
# confirmed cause behind the account's 5-8% bounce rate (vs. the ~1-2%
# norm for cold email): bad matches were being accepted with zero
# confidence check even though Hunter returns exactly the fields needed to
# catch this (data.score and data.verification.status), both of which
# were being silently discarded before this fix.
#
# Threshold chosen: Hunter's own docs describe `score` (0-100) as its
# confidence that the email is correct/deliverable, built from the same
# signals verification uses. 50 is the lowest score Hunter itself still
# surfaces as an actionable match in its UI/API (below that it considers a
# guess, not a finding); requiring 50 as a FLOOR (not treating anything
# above it as "verified") errs toward keeping the pipeline's email-finding
# rate useful rather than gutting it, while verification.status (checked
# separately, see _passes_quality_gate()) is what actually gates
# deliverability. Raise this if bounce rate is still high after this fix
# ships; 50 is a starting point, not a final answer.
_MIN_SCORE = 50

# Hunter's documented verification.status values. "valid" and
# "accept_all" both mean Hunter believes mail sent to this address will be
# accepted (accept_all = the mail server accepts anything for the domain,
# so it's not a hard guarantee, but it's not a rejection either -- treating
# it as invalid would throw away a large share of real, deliverable
# addresses). "invalid" and "disposable" are hard rejects. "unknown" means
# Hunter couldn't verify either way (e.g. skipped verification, or the
# mail server didn't respond) -- not proof the address is bad, so it's
# accepted but logged, per this task's explicit "don't be so strict this
# returns nothing useful" guidance. "webmail" (gmail.com, etc.) is a real,
# normally-deliverable personal inbox, not a company address, but Hunter
# flags it for visibility rather than deliverability -- accepted like
# "unknown".
_REJECTED_STATUSES = {"invalid", "disposable"}
_CAUTION_STATUSES = {"unknown", "webmail", None}

# Generic bio-link/social platforms are never a company's OWN mail domain
# -- searching Hunter's Domain Search against one of these returns
# whoever's real email Hunter has on file for THAT PLATFORM (e.g.
# linktr.ee's own support address), not anyone related to the lead being
# contacted. This is exactly what happened with the Kedemos Education
# lead above. Domain-search email-finding must be skipped entirely for a
# lead whose website resolves to one of these, rather than "finding" an
# unrelated person's address.
BLOCKLISTED_DOMAINS = {
    "linktr.ee",
    "linktree.com",
    "instagram.com",
    "facebook.com",
    "fb.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "bio.link",
    "beacons.ai",
    "linkin.bio",
    "lnk.bio",
    "campsite.bio",
    "carrd.co",
    "msha.ke",
    "solo.to",
    "linkfree.to",
    "shorby.com",
    "youtube.com",
    "linkedin.com",
    "snapchat.com",
    "pinterest.com",
    "whatsapp.com",
    "telegram.me",
    "t.me",
    # ADDED 2026-09-20, real bad match traced: a Zimmar lead's saved
    # contact_email came back as the platform owner's OWN real personal
    # Gmail address (husseinalasaad5@gmail.com) -- an email lead with no
    # "found via" note at all, meaning _bare_domain() extracted a personal
    # webmail domain from a malformed/mismatched `website` field (the same
    # class of bug that motivated this blocklist in the first place: a
    # LinkedIn "website" field isn't always the company's own domain).
    # Same reasoning as the bio-link platforms above -- Domain Search
    # against a personal webmail PROVIDER's own domain returns whichever
    # real person's address Hunter happens to have crawled for that
    # provider, completely unrelated to the lead being contacted. A
    # company's real contact email is never @gmail.com/@yahoo.com/etc. by
    # definition for this codebase's purposes (cold B2B outreach to a real
    # business), so these are blocked the same way bio-link platforms are.
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "icloud.com",
    "aol.com",
    "live.com",
    "msn.com",
    "protonmail.com",
}


def is_blocklisted_domain(domain: str | None) -> bool:
    """
    True if `domain` (bare host, e.g. "linktr.ee" -- see scheduler.py's
    _bare_domain()) is a generic bio-link/social platform rather than a
    company's own site. Callers must skip Hunter Domain Search entirely
    for such a domain -- see BLOCKLISTED_DOMAINS' docstring above for why
    (real, confirmed bad match: Kedemos Education / linktr.ee).

    Handles a bare host OR a full URL defensively (urlparse only kicks in
    when it looks like one), so this is safe to call directly on a raw
    `website` field as well as on an already-normalized domain.
    """
    if not domain:
        return False
    host = domain.strip().lower()
    if "://" in host or "/" in host:
        parsed = urlparse(host if "://" in host else f"https://{host}")
        host = parsed.netloc or parsed.path.split("/")[0]
    host = host.removeprefix("www.")
    return host in BLOCKLISTED_DOMAINS


def _passes_quality_gate(data: dict, *, context: str) -> bool:
    """
    Applies the score/verification-status quality gate described in this
    module's docstring block above to one Hunter result (`data` is
    Hunter's own per-candidate dict, which for both endpoints carries
    `score` and a nested `verification.status`). Returns False (reject)
    for a hard-bad status or a below-threshold score, True otherwise --
    including the "accept but log" caution cases, so a caller doesn't need
    to duplicate this logic. `context` is just a human-readable label
    (e.g. the email/domain being evaluated) for the log line.
    """
    score = data.get("score")
    verification = data.get("verification") or {}
    status = verification.get("status")

    if status in _REJECTED_STATUSES:
        _log.warning(
            "Hunter candidate rejected for %s: verification.status=%s (score=%s)",
            context, status, score,
        )
        return False

    if isinstance(score, (int, float)) and score < _MIN_SCORE:
        _log.warning(
            "Hunter candidate rejected for %s: score=%s below minimum %s (status=%s)",
            context, score, _MIN_SCORE, status,
        )
        return False

    if status in _CAUTION_STATUSES:
        _log.info(
            "Hunter candidate accepted with caution for %s: status=%s, score=%s",
            context, status, score,
        )

    return True


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

    Quality-gated (2026-09-17): a raw email coming back from Hunter is NOT
    enough on its own -- see _passes_quality_gate()/this module's top
    docstring for why (real bad match this fixes). A candidate that fails
    the gate is treated exactly like "no match": returns None, logged as a
    rejection, never silently swapped for a worse guess.

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

    params = {"domain": domain, "first_name": first_name}
    if last_name:
        params["last_name"] = last_name

    try:
        response = httpx.get(
            _EMAIL_FINDER_ENDPOINT,
            params=params,
            headers=_auth_headers(api_key),
            timeout=_TIMEOUT_SECONDS,
        )
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
    if not email:
        return None

    if not _passes_quality_gate(data, context=f"{email} ({name} @ {domain})"):
        return None
    return email


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

    Returns the single best email Hunter has on file, or None if Hunter
    has nothing usable for this domain. Same HunterLookupFailed/
    HunterNotConfigured error contract as find_email() above, so the
    caller's existing per-lead try/except handles both identically.

    Two checks added 2026-09-17 (see this module's top docstring for the
    real bad match that prompted both):
    1. Refuses to search a domain that's actually a generic bio-link/
       social platform, not the company's own site (BLOCKLISTED_DOMAINS) --
       raises ValueError so callers can distinguish "we deliberately
       didn't search this" from "Hunter had nothing" (None) or a real API
       failure (HunterLookupFailed).
    2. Applies the same score/verification.status quality gate
       find_email() uses to each candidate in Hunter's `emails` array,
       skipping any that don't pass rather than trusting array order
       alone as a confidence signal.

    Generic-over-personal preference (added 2026-09-17, separate from the
    two checks above): Hunter's own `emails` array is pre-sorted by its
    internal confidence score, and each entry carries a `type` field of
    either "generic" (a role-based company address -- info@, contact@,
    sales@, hello@) or "personal" (a named individual). A role address is
    what the owner wants sent to by default when one exists for a domain,
    since it's stable (doesn't break when that person leaves) and isn't
    tied to guessing which specific person is the right contact. So
    instead of returning the first candidate that merely passes the
    quality gate (which could be a named individual ranked above a generic
    address purely on Hunter's confidence score), this collects every
    passing candidate first, then prefers any "generic"-type one over
    "personal"-type ones -- falling back to a personal address only when
    no generic address for the domain passes the gate at all. Relative
    order within each type is preserved (Hunter's own confidence
    pre-sort), so this only changes generic-vs-personal ordering, not
    ordering within a type.

    Uses more Hunter credits per successful lookup than find_email() (this
    endpoint returns a full page of company data, not one targeted match)
    -- see this module's own docstring on the 50 free-credits/month
    ceiling; calling this as a fallback (not the primary path) keeps it to
    only the leads find_email() couldn't already resolve.
    """
    api_key = config.HUNTER_API_KEY
    if not api_key:
        raise HunterNotConfigured("HUNTER_API_KEY is not set in agent/.env.")

    if is_blocklisted_domain(domain):
        _log.warning(
            "Skipping Hunter Domain Search for %s: generic bio-link/social platform, "
            "not the company's own domain (see BLOCKLISTED_DOMAINS).",
            domain,
        )
        raise ValueError(
            f"Refusing to search generic platform domain {domain!r} -- not the "
            "company's own site."
        )

    params = {"domain": domain, "limit": 5}

    try:
        response = httpx.get(
            _DOMAIN_SEARCH_ENDPOINT,
            params=params,
            headers=_auth_headers(api_key),
            timeout=_TIMEOUT_SECONDS,
        )
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

    generic_match: str | None = None
    personal_match: str | None = None
    for candidate in emails:
        value = candidate.get("value")
        if not value:
            continue
        if not _passes_quality_gate(candidate, context=f"{value} (domain search on {domain})"):
            continue
        # ADDED 2026-09-19, real bounce pattern found live: every single
        # bounced address sent so far (elias@globalcom.net.lb,
        # anna.moreno@strategyand.pwc.com, anastasia.bober@unitech-ikk.com,
        # abrar@damacproperties.com, wiamk@arwanlb.com) traced back to THIS
        # function -- Domain Search, a guessed address pattern with no
        # verified person behind it -- and NONE came from find_email()'s
        # founder-verified path. _passes_quality_gate() alone still lets a
        # "unknown"-status candidate through as long as its score clears
        # _MIN_SCORE (see _CAUTION_STATUSES) -- reasonable for find_email(),
        # where a real founder name was already independently confirmed via
        # LinkedIn, but too loose here, where Domain Search's guess is the
        # ONLY evidence this address exists at all. "webmail" stays
        # accepted (a real personal Gmail/Outlook address CAN be correct
        # for a small business, and is a materially different risk from an
        # unverifiable guessed pattern at a real company's own domain).
        if (candidate.get("verification") or {}).get("status") == "unknown":
            _log.warning(
                "Hunter Domain Search candidate rejected for %s (domain search on %s): "
                "verification.status=unknown -- too unverified for a guessed address "
                "with no independently-confirmed person behind it (real bounce pattern, "
                "see this loop's own comment).",
                value, domain,
            )
            continue
        # Prefer the company's own role-based address (info@, contact@,
        # sales@, hello@, ...) over a named individual's -- see this
        # function's docstring ("Generic-over-personal preference") for
        # why. Keep the first passing candidate of each type (array order
        # already reflects Hunter's own confidence ranking within a type).
        if candidate.get("type") == "generic":
            if generic_match is None:
                generic_match = value
        elif personal_match is None:
            personal_match = value

        if generic_match is not None:
            # Already found the best possible outcome (a passing generic
            # address); no need to keep scanning.
            break

    return generic_match if generic_match is not None else personal_match
