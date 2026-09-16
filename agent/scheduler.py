"""
Runs each tenant's accounts at their own configured times.

PORTED 2026-08-20 to be multi-tenant (see PROGRESS.md's dated entry for the
full writeup). Every orchestration function below now loops over
repo.list_active_tenant_ids() -- every tenant with at least one active
LinkedIn or Instagram OutreachAccount row -- and, within each tenant, over
that tenant's own due accounts (see core/account_pool.py). Each tenant's
whole slice of a cycle runs inside `with repo.tenant_scope(tenant_id):`,
which is what lets every downstream call into messaging/*, crm/*,
sending/*, notifications/*, and core/health.py|warmup.py -- none of which
were changed by this port, none of which know tenant_id exists -- resolve
the right tenant's rows without their call signatures changing (see
db/repositories.py's module docstring, "DISCREPANCY FLAGGED" section, for
why that fallback exists).

Error isolation now has ONE MORE level than before the port: one tenant's
failure must not stop other tenants' processing, in addition to the
existing one-account/one-lead/one-message isolation already in place below.

Two ways to use this module:
  - `run_cycle(...)` does the real work for whichever accounts are due right
    now (or a forced list, for manual testing) -- this is what Phase 2 tests.
  - `build_daily_schedule(...)` wires up APScheduler cron jobs at each
    account's real run_time, for the always-on server from Phase 10 onward.
    It is not exercised during local development, where nothing runs a
    permanent background process.

Manual test trigger (what "Hussein can trigger a run" means in Phase 2):

    agent/venv/Scripts/python.exe -m agent.scheduler
"""

from __future__ import annotations

import datetime as dt
import logging
import random
import re
import time

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agent import config
from agent.analysis import analyze
from agent.analysis import founder as founder_detection
from agent.analysis import score as scoring
from agent.analysis import whatsapp_detect
from agent.core import account_pool as pool
from agent.core import health
from agent.core import warmup
from agent.core.session import ProxyIpMismatch, SessionManager
from agent.crm import followup
from agent.db import repositories as repo
from agent.discovery import findymail, hunter, instagram, linkedin
from agent.discovery.qualify import qualify_profile
from agent.discovery.qualify import _is_agency as _lead_is_agency
from agent.messaging import approval
from agent.messaging import generate as message_generate
from agent.messaging import style as message_style
from agent.notifications import whatsapp_notify
from agent.sending import (
    instagram_reply_check,
    instagram_send,
    linkedin_reply_check,
    linkedin_send,
    whatsapp_reply_check,
    whatsapp_send,
)
from agent.sending.instagram_send import NoExistingThread as InstagramNoExistingThread
from agent.sending.instagram_send import NoMessageButtonAvailable as InstagramNoMessageButtonAvailable
from agent.sending.linkedin_send import MessageLengthInvalid, NoMessageButtonAvailable
from agent.sending.linkedin_send import NoExistingThread as LinkedInNoExistingThread
from agent.sending.whatsapp_send import WhatsAppNotConfigured

# Phase 2 has no real discovery yet -- this is a harmless, neutral page used
# purely to prove a session can open, navigate, and be health-checked. Phase 3
# replaces this with the actual LinkedIn/Instagram search entry points.
DEFAULT_TEST_URL = "https://example.com"

# LIVE-VERIFIED 2026-09-01: LinkedIn's company search requires a real,
# non-empty text keyword -- an empty niche (even paired with a real
# companyHqGeo facet) returns 0 results, confirmed against a live run.
# Generic filler words ("companies", "company", "business") also returned 0
# in earlier live testing; only genuine industry/sector terms return real
# results. For a tenant configured to target "any type of company" (empty
# settings.target_niche), one of these is picked at random each discovery
# cycle instead of searching with no keyword at all -- see
# _resolve_search_niche() below.
# 2026-09-02, real instruction from the platform owner (Insurance specifically):
# target companies of EVERY type EXCEPT insurance companies themselves --
# insurance is deliberately absent from this list (it's the tenant's own
# industry, not a prospect). Expanded to a real, broad list per the
# owner's explicit ask ("make a list for them" covering trading/software/
# commercial/general/any type) so rotation genuinely reaches a wide mix of
# real Lebanese companies over many runs, not a narrow handful of sectors.
_RANDOM_INDUSTRY_TERMS = [
    "manufacturing",
    "trading",
    "general trading",
    "construction",
    "technology",
    "software",
    "IT services",
    "retail",
    "logistics",
    "real estate",
    "consulting",
    "healthcare",
    "hospitality",
    "education",
    "transportation",
    "commercial",
    "general commercial",
    "engineering",
    "food and beverage",
    "distribution",
    "import export",
    "textile",
    "pharmaceutical",
    "automotive",
    "media",
    "advertising",
    "telecommunications",
    "energy",
    "agriculture",
    "banking",
]


# A configured niche that describes a SERVICE rather than a kind of company
# is a trap on both platforms: it finds the people who sell that service,
# not the companies who buy it. MJivity's real configured niche was "small
# business marketing", which on Instagram returns marketing coaches and
# agencies -- and on LinkedIn returns marketing agencies -- when what
# MJivity actually wants is product brands that need 3D visuals. Confirmed
# 2026-09-13 against its real returned leads (Indian marketing influencers).
# A niche matching one of these is treated as "no usable niche" and the
# tenant's own target_industry list is rotated instead.
_SERVICE_NOT_SECTOR_NICHES = {
    "small business marketing",
    "business marketing",
    "marketing",
    "digital marketing",
    "social media marketing",
    "advertising",
    "branding",
}


def _industry_rotation_terms(target_industry: str) -> list[str]:
    """
    Split a tenant's free-text target_industry into individual searchable
    sector terms.

    A tenant like MJivity configures this as a real list -- "E-commerce
    brands, consumer products, fashion & apparel, cosmetics & beauty,
    jewelry, technology, automotive, ..." -- which is exactly the rotation
    material _RANDOM_INDUSTRY_TERMS provides for the generic case, but
    specific to what this tenant actually sells to. Splitting on commas and
    slashes keeps each sector intact; "&" is dropped because LinkedIn and
    Instagram both treat it as noise, and over-long prose fragments are
    skipped since they are a description, not a search term.
    """
    if not target_industry:
        return []
    parts = re.split(r"[,/;]| and ", target_industry)
    terms: list[str] = []
    for part in parts:
        cleaned = part.replace("&", " ").strip().strip(".")
        cleaned = re.sub(r"\s+", " ", cleaned)
        # Drop parentheticals like "holding companies (multi-brand groups)".
        cleaned = re.sub(r"\s*\([^)]*\)", "", cleaned).strip()
        if 3 <= len(cleaned) <= 40 and len(cleaned.split()) <= 4:
            terms.append(cleaned)
    return terms


def _resolve_search_niche(niche: str, target_industry: str = "") -> str:
    """
    A configured niche is used as-is. An empty niche means "target any type
    of company" -- but LinkedIn's search has no such mode, so this picks a
    real industry term at random instead of searching with an empty
    keyword (which live-verified returns 0 results). Called once per
    discovery cycle, so a fresh random term is picked each run -- over many
    runs this covers a broad mix of industries rather than the same one
    every time.

    A niche that names a SERVICE rather than a sector (see
    _SERVICE_NOT_SECTOR_NICHES) falls back to the tenant's OWN
    target_industry sectors, since that tenant has told us what it sells to
    even though its niche field says what it sells.

    An EMPTY niche deliberately does NOT use target_industry: the owner's
    standing instruction for Zimmar and Insurance is "all companies", and
    those two tenants fill target_industry with facility types ("Offices,
    warehouses, schools") or prose ("Any industry -- targeting is by company
    size"), neither of which is a usable search term. The broad
    _RANDOM_INDUSTRY_TERMS rotation is the correct behavior there.
    """
    if not niche:
        return random.choice(_RANDOM_INDUSTRY_TERMS)
    if niche.strip().lower() not in _SERVICE_NOT_SECTOR_NICHES:
        return niche
    tenant_terms = _industry_rotation_terms(target_industry)
    if tenant_terms:
        return random.choice(tenant_terms)
    return random.choice(_RANDOM_INDUSTRY_TERMS)


# Ordered smallest-to-largest so _resolve_size_buckets can walk it once and
# stop -- must match linkedin.COMPANY_SIZE_FACETS' own keys exactly.
_COMPANY_SIZE_BUCKET_RANGES: list[tuple[str, int, int | None]] = [
    ("1-10", 1, 10),
    ("11-50", 11, 50),
    ("51-200", 51, 200),
    ("201-500", 201, 500),
    ("501-1000", 501, 1000),
    ("1001-5000", 1001, 5000),
    ("5001-10000", 5001, 10000),
    ("10000+", 10001, None),
]


def _resolve_size_buckets(min_company_size: int | None) -> list[str] | None:
    """
    Turns a tenant's exact targetCompanySizeMin (e.g. Insurance's 51) into
    the LinkedIn companySize facet buckets that could contain a match --
    every bucket whose own range reaches at least that minimum. This is a
    coarse PRE-filter only, run server-side by LinkedIn's own search before
    we ever visit a profile -- the exact cutoff is still enforced precisely
    afterward by qualify_profile()'s own headcount check (a bucket can
    contain companies both above and below the real minimum, e.g. "51-200"
    for a minimum of 51 also contains a 60-employee company, which is
    fine, and would contain a 55-employee one too, also fine -- the only
    bucket ever excluded is one that CAN'T contain a qualifying company at
    all).

    None/0 (no minimum configured, e.g. Zimmar after the owner's explicit
    2026-09-12 "all sizes" instruction) returns None -- no facet added at
    all, searches every size, exactly as build_search_url's own docstring
    describes for that case.
    """
    if not min_company_size:
        return None
    return [
        label for label, _low, high in _COMPANY_SIZE_BUCKET_RANGES
        if high is None or high >= min_company_size
    ]

# Exceptions that represent a normal, expected "can't do this one thing"
# outcome rather than a genuine failure worth flagging -- e.g. a LinkedIn
# company page simply not having Page messaging enabled. Used by log_error()
# below so the dashboard's Errors page can separate real problems from
# routine skip reasons by default (see database/007_add_error_log.sql).
_EXPECTED_EXCEPTIONS = (
    NoMessageButtonAvailable,
    MessageLengthInvalid,
    WhatsAppNotConfigured,
    LinkedInNoExistingThread,
    InstagramNoMessageButtonAvailable,
    InstagramNoExistingThread,
)

# Plain stdout logger for real-time discovery progress -- distinct from
# log_error() below, which writes to the DB error_log table for the
# dashboard's Errors page. Added 2026-09-13 after a real 30+ minute
# discovery run showed zero visible progress in the logs, and the owner
# had to ask "how to check where is the real problem" -- there was
# genuinely no way to tell, from the logs alone, whether a long run was
# stuck or just slow. server.py already calls logging.basicConfig(), so
# this logger's output reaches docker logs the same way that one does.
_progress_log = logging.getLogger("agent.discovery.progress")


def log_error(
    stage: str,
    exc: Exception,
    *,
    channel: str | None = None,
    lead_id: str | None = None,
    account_id: str | None = None,
) -> None:
    """
    Record one caught pipeline failure to error_log so it's visible on the
    dashboard's Errors page instead of only existing in an in-memory results
    list that gets discarded the moment the calling function returns --
    every try/except block below already isolates one bad lead/message from
    stopping a whole run, this just stops the exception's details from
    being silently thrown away once that's done. Never itself raises --
    a logging failure must not turn a handled, isolated error into an
    unhandled one that takes down the whole cycle.

    ALSO logs to stdout (2026-09-16): a SessionBusy on Zimmar LinkedIn's
    sending cron was recorded correctly in error_log, but docker logs
    showed nothing at all for that ~4-minute run -- APScheduler saw the
    job function return its normal results list (one `ok: False` entry)
    and logged "executed successfully", so a real, understood failure
    looked from the logs alone like nothing happened. error_log is the
    dashboard's source of truth and stays the primary record; this stdout
    line exists only so `docker logs` isn't silent about the same event.
    """
    try:
        repo.insert_error({
            "stage": stage,
            "channel": channel,
            "lead_id": lead_id,
            "account_id": account_id,
            "error_message": str(exc),
            "is_expected": isinstance(exc, _EXPECTED_EXCEPTIONS),
        })  # tenant_id resolved from the active tenant_scope(...), see repo.insert_error()'s docstring
    except Exception:  # noqa: BLE001 -- logging itself must never crash the pipeline
        pass
    try:
        _progress_log.warning(
            "[%s] %s%s: %s", stage,
            f"account={account_id} " if account_id else "",
            f"lead={lead_id}" if lead_id else "",
            exc,
        )
    except Exception:  # noqa: BLE001 -- logging itself must never crash the pipeline
        pass


def run_cycle(target_url: str = DEFAULT_TEST_URL, force: bool = False) -> list[dict]:
    """
    Run one cycle for whichever accounts are due (or all active accounts, if
    force=True), across every tenant that currently has active Outreach
    accounts. For each account: open an isolated session, visit target_url,
    check its health, and log the outcome as a `runs` row.

    Returns a list of per-account result dicts (each tagged with its
    tenant_id), mainly so a manual test run can print a clear summary of
    what happened to each account.

    Tenant-level isolation: one tenant raising here (e.g. a DB hiccup while
    loading its accounts) is logged and skipped, same as the existing
    per-account try/except inside the loop already isolated one bad account
    from the rest -- this adds the one more level the port asked for so one
    tenant can never take down another tenant's run.
    """
    results = []

    for tenant_id in repo.list_active_tenant_ids():
        try:
            with repo.tenant_scope(tenant_id):
                results.extend(_run_cycle_for_tenant(tenant_id, target_url, force))
        except Exception as exc:  # noqa: BLE001 -- one bad tenant must not stop the others
            try:
                repo.insert_error({
                    "stage": "run_cycle", "error_message": str(exc), "is_expected": False,
                }, tenant_id=tenant_id)
            except Exception:  # noqa: BLE001 -- logging itself must never crash the pipeline
                pass

    return results


def _run_cycle_for_tenant(tenant_id: str, target_url: str, force: bool) -> list[dict]:
    accounts = pool.get_due_accounts(tenant_id, force=force)
    results = []

    if not accounts:
        return results

    today_start = pool.today_start_iso(tenant_id)
    with SessionManager() as sessions:
        for account in accounts:
            # Atomic claim, not the old separate start_run() -- get_due_accounts()'s
            # own has_run_today() check above happened in an earlier, separate
            # query, leaving a real window for a second overlapping process
            # (e.g. server.py's cron firing the same moment a manual test run
            # is in progress) to also see "not run yet" and duplicate this
            # account's work, corrupting the shared browser_profiles session
            # file and doubling its real daily send volume. claim_account_for_run()
            # closes that window with a transaction-scoped advisory lock; None
            # means someone else already claimed this account for today, in
            # which case skip it exactly like "not due" rather than proceeding.
            run = repo.claim_account_for_run(tenant_id, account["id"], today_start, skip_daily_check=force)
            if run is None:
                continue
            try:
                context, page, new_verified_ip = sessions.open(account)
            except ProxyIpMismatch as exc:
                # Hard stop for THIS account only -- see
                # _run_discovery_cycle_for_tenant's identical handling for
                # the full reasoning. The mismatched context is already
                # closed by open() before this exception reaches here.
                repo.finish_run(
                    tenant_id, run["id"], leads_found=0, messages_sent=0,
                    status="error", finished_at_iso=dt.datetime.now(dt.timezone.utc).isoformat(),
                    notes=str(exc),
                )
                results.append({
                    "tenant_id": tenant_id, "account": account["label"], "ok": False,
                    "warning_type": "proxy_ip_mismatch", "reason": str(exc),
                })
                continue
            if new_verified_ip:
                repo.update_account(account["id"], {"verified_proxy_ip": new_verified_ip}, tenant_id)

            try:
                response = page.goto(target_url, timeout=15_000)
            except Exception as exc:  # noqa: BLE001 -- navigation failures are expected/handled
                response = None
                nav_error = str(exc)
            else:
                nav_error = None

            ok, warning_type, reason = health.check_navigation(response)
            if ok:
                ok, warning_type, reason = health.check_page_content(page)

            if not ok and nav_error and warning_type == "navigation_failed":
                # Surface Playwright's actual exception text instead of the
                # generic default, since it's more specific and more useful in
                # the dashboard later.
                reason = nav_error

            sessions.close(account["id"], context)

            finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
            if ok:
                repo.finish_run(
                    tenant_id, run["id"], leads_found=0, messages_sent=0,
                    status="completed", finished_at_iso=finished_at,
                )
            else:
                health.record_warning(account["id"], warning_type, reason)
                account["warning_type"], account["warning_reason"] = warning_type, reason
                whatsapp_notify.notify_account_warning(account)
                repo.finish_run(
                    tenant_id, run["id"], leads_found=0, messages_sent=0,
                    status="error", finished_at_iso=finished_at, notes=reason,
                )

            results.append({
                "tenant_id": tenant_id,
                "account": account["label"],
                "ok": ok,
                "warning_type": warning_type,
                "reason": reason,
            })

    return results


def _save_if_qualified(
    account: dict, platform: str, profile_url: str, raw_profile: dict, niche: str = ""
) -> bool:
    """
    Shared save step for both platforms: skip if this profile is already
    known, qualify it, and insert into `leads` with status "discovered" if it
    passes. Returns True if a new lead was actually saved.
    """
    if repo.lead_profile_url_exists(account["tenant_id"], profile_url):
        return False

    normalised = {**raw_profile, "platform": platform}
    qualifies, reasons = qualify_profile(normalised, niche)
    if not qualifies:
        return False

    repo.insert_lead(account["tenant_id"], {
        "account_id": account["id"],
        "platform": platform,
        "business_name": raw_profile.get("display_name") or None,
        "profile_url": profile_url,
        "follower_count": raw_profile.get("follower_or_headcount"),
        "website": raw_profile.get("website"),
        # NOTE (2026-08-20 port, real behavior change -- see PROGRESS.md):
        # "bio"/"engagement_sample" have no column on OutreachLead (see
        # db/repositories.py's _LEAD_COLUMNS comment) -- insert_lead()
        # silently drops unknown fields rather than erroring. They're still
        # passed here so the dict shape stays identical to the pre-port
        # version (harmless, just ignored on write), but analysis/*.py
        # (untouched, out of scope) reads lead.get("bio") from a lead
        # re-fetched from the DB in run_analysis_cycle below via
        # leads_by_status() -- that re-fetched row will never have "bio",
        # so analyze.py's bio-dependent analysis now always sees "none
        # available" post-port. Not silently swallowed: flagged here and in
        # PROGRESS.md as a real, intentional-for-now narrowing, not a bug
        # nobody noticed.
        "bio": raw_profile.get("bio"),
        "engagement_sample": raw_profile.get("engagement_sample"),  # Instagram only -- null on LinkedIn leads
        "status": "discovered",
        "notes": " | ".join(reasons),  # keeps the qualification reasoning on the record
    })
    return True


def run_discovery_cycle(force: bool = False) -> list[dict]:
    """
    Discover, qualify, and save new leads for whichever accounts are due,
    across every tenant that currently has active Outreach accounts (Phase
    3, ported multi-tenant 2026-08-20).

    Channel gating (per this port's spec point 3): one OutreachAccount row =
    one platform. LinkedIn discovery only runs for an account whose
    platform == "linkedin"; Instagram discovery only for platform ==
    "instagram" -- a tenant that only has an active LinkedIn account no
    longer implicitly also gets Instagram discovery run against it (the
    pre-port version always tried both for every account, since the
    standalone schema didn't have a platform-per-account concept the same
    way). This is confirmed sufficient by AccountHealthClient.tsx, which
    already lets an owner add/remove one account per platform -- no new
    toggle infrastructure was needed, just this gating fix.

    VERIFIED 2026-07-31/08-02: discovery/linkedin.py and discovery/instagram.py's
    scraping selectors were checked against real, live pages using a real
    captured login session (see each module's own docstring for exactly what
    was confirmed and which bugs that testing caught). This orchestration
    itself -- looping tenants and accounts, widening weak searches, per-lead
    error isolation -- has not had a full end-to-end run recorded live since
    this port (that's a separate, still-open item, see PROGRESS.md), not a
    selector-accuracy concern.

    Tenant-level isolation: one tenant raising here is logged and skipped,
    same reasoning as run_cycle() above.
    """
    summary = []

    for tenant_id in repo.list_active_tenant_ids():
        try:
            with repo.tenant_scope(tenant_id):
                if repo.is_tenant_paused():
                    continue
                summary.extend(_run_discovery_cycle_for_tenant(tenant_id, force))
        except Exception as exc:  # noqa: BLE001 -- one bad tenant must not stop the others
            try:
                repo.insert_error({
                    "stage": "discovery", "error_message": str(exc), "is_expected": False,
                }, tenant_id=tenant_id)
            except Exception:  # noqa: BLE001 -- logging itself must never crash the pipeline
                pass

    return summary


def _run_discovery_cycle_for_tenant(tenant_id: str, force: bool) -> list[dict]:
    settings = repo.get_settings(tenant_id) or {}
    business_name = settings.get("business_name") or ""
    configured_niche = settings.get("target_niche") or ""
    niche = _resolve_search_niche(configured_niche, settings.get("target_industry") or "")
    # True for a tenant like Insurance running with no configured niche at
    # all ("any industry") -- lets _discover_linkedin's widening loop pick
    # a FRESH random industry term on a weak/empty result instead of just
    # dropping location and eventually collapsing to a genuinely empty
    # search (which live-verified always returns 0 results). A tenant with
    # a real configured niche (e.g. Zimmar's "Security and building
    # infrastructure integration") keeps the existing behavior untouched --
    # widening THEIR specific niche to a random unrelated industry would be
    # wrong, not helpful.
    # A service-shaped niche (see _SERVICE_NOT_SECTOR_NICHES) is rotated
    # rather than peeled for the same reason an empty one is: peeling "small
    # business marketing" just yields "marketing", which is the very term
    # that surfaced the wrong audience in the first place.
    niche_is_random = (
        not configured_niche
        or configured_niche.strip().lower() in _SERVICE_NOT_SECTOR_NICHES
    )
    location = settings.get("target_location") or ""
    industry = settings.get("target_industry") or ""
    # The tenant's own configured sectors, used to rotate keywords/hashtags
    # on-sector. ONLY for a tenant whose niche names a service rather than a
    # sector (MJivity): an empty-niche tenant means "all companies" and must
    # keep the broad rotation -- see _resolve_search_niche's docstring.
    tenant_terms = (
        _industry_rotation_terms(industry)
        if configured_niche.strip().lower() in _SERVICE_NOT_SECTOR_NICHES
        else []
    )
    # Found stored but never actually enforced in the 2026-09-12 review --
    # OutreachSettings.targetCompanySizeMin (e.g. Insurance's real "100+
    # employees" requirement, Zimmar's "30+") was set on every tenant's
    # settings row but qualify_profile() only ever applied a small,
    # tenant-agnostic penalty for a LOW headcount, never a real per-tenant
    # floor. Threaded through to _save_if_qualified below so a company
    # under the configured minimum is rejected outright, the same way a
    # location mismatch already is.
    min_company_size = settings.get("target_company_size_min")
    # LinkedIn-side coarse pre-filter derived from the same minimum -- see
    # _resolve_size_buckets' own docstring. Does not replace the precise
    # per-profile check above/below; it just stops LinkedIn from returning
    # (and this agent from wasting a visit on) companies whose entire size
    # bucket is below the minimum in the first place.
    size_buckets = _resolve_size_buckets(min_company_size)

    accounts = pool.get_due_accounts(tenant_id, force=force)
    summary = []

    today_start = pool.today_start_iso(tenant_id)
    with SessionManager() as sessions:
        for account in accounts:
            # Atomic claim -- see _run_cycle_for_tenant()'s identical comment
            # above for why this replaces start_run() directly.
            run = repo.claim_account_for_run(tenant_id, account["id"], today_start, skip_daily_check=force)
            if run is None:
                continue
            counts = {"linkedin_found": 0, "linkedin_saved": 0,
                      "instagram_found": 0, "instagram_saved": 0,
                      "errors": [], "skipped_leads": []}

            try:
                context, page, login_error, new_verified_ip = sessions.open_or_login(account)
            except ProxyIpMismatch as exc:
                # Hard stop for THIS account only -- open_or_login() itself
                # refuses to proceed to login when the proxy's real IP
                # doesn't match what this account verified before (see
                # ProxyIpMismatch's own docstring); the mismatched context is
                # already closed by open() before this exception reaches
                # here. Other accounts in this tenant's batch are
                # unaffected -- only letting this propagate past here would
                # abort the whole tenant's cycle, which one account's proxy
                # problem doesn't warrant.
                counts["errors"].append(f"proxy_ip_mismatch: {exc}")
                log_error("proxy_ip_mismatch", exc, account_id=account["id"])
                repo.finish_run(
                    tenant_id, run["id"], leads_found=0, messages_sent=0,
                    status="error", finished_at_iso=dt.datetime.now(dt.timezone.utc).isoformat(),
                    notes=str(exc),
                )
                summary.append({"tenant_id": tenant_id, "account": account["label"], **counts})
                continue

            if new_verified_ip:
                repo.update_account(account["id"], {"verified_proxy_ip": new_verified_ip}, tenant_id)

            if login_error:
                # A credential login was attempted (no saved session existed
                # yet) and failed -- report it to the account row so the
                # tenant sees why in their dashboard (AccountHealthClient's
                # loginStatus/loginError fields), and skip discovery entirely
                # this run rather than proceeding on a context that never
                # actually got logged in.
                repo.update_account(account["id"], {"login_status": "failed", "login_error": login_error}, tenant_id)
                counts["errors"].append(f"login: {login_error}")
                log_error("login", RuntimeError(login_error), channel=account.get("platform"), account_id=account["id"])
            else:
                if account.get("login_email") and account.get("login_password_enc") and account.get("login_status") != "connected":
                    # Either this run's own login attempt just succeeded, or a
                    # saved session from a prior successful login was reused --
                    # either way, credentials exist and nothing failed, so this
                    # account is (still) genuinely connected.
                    repo.update_account(
                        account["id"],
                        {
                            "login_status": "connected",
                            "login_error": None,
                            "login_connected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        },
                        tenant_id,
                    )

                if account.get("platform") == "linkedin":
                    _progress_log.info("[%s] starting LinkedIn discovery", account.get("label"))
                    try:
                        _discover_linkedin(account, page, niche, location, industry, counts, min_company_size, niche_is_random, size_buckets, tenant_terms, business_name)
                    except Exception as exc:  # noqa: BLE001 -- a whole-platform failure, not one bad lead
                        counts["errors"].append(f"linkedin: {exc}")
                        log_error("discovery", exc, channel="linkedin", account_id=account["id"])
                    _progress_log.info(
                        "[%s] finished LinkedIn discovery: %d found, %d saved",
                        account.get("label"), counts.get("linkedin_found", 0), counts.get("linkedin_saved", 0),
                    )
                elif account.get("platform") == "instagram":
                    _progress_log.info("[%s] starting Instagram discovery", account.get("label"))
                    try:
                        _discover_instagram(account, page, niche, counts, tenant_terms, business_name, location)
                    except Exception as exc:  # noqa: BLE001
                        counts["errors"].append(f"instagram: {exc}")
                        log_error("discovery", exc, channel="instagram", account_id=account["id"])
                    _progress_log.info(
                        "[%s] finished Instagram discovery: %d found, %d saved",
                        account.get("label"), counts.get("instagram_found", 0), counts.get("instagram_saved", 0),
                    )
                else:
                    # platform == "email" (or anything else) -- no browser-automation
                    # discovery exists for that channel; the Next.js app owns email entirely.
                    _progress_log.info(
                        "[%s] platform=%r has no browser-automation discovery -- skipping (handled by the Next.js/SES pipeline)",
                        account.get("label"), account.get("platform"),
                    )

            sessions.close(account["id"], context)

            finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
            # Persist the actual search terms tried, not just the errors.
            # Added 2026-09-13: these were collected in `counts` but thrown
            # away at the end of every run, which meant a 0-lead run gave no
            # way to tell whether the search TERM was bad or the candidates
            # were -- the single biggest blocker to diagnosing real
            # 0-result days. Prefixed onto notes so it shows on the Run
            # Status page next to whatever errors occurred.
            terms = counts.get("linkedin_search_terms") or counts.get("instagram_search_terms") or []
            terms_note = f"searched: {terms}" if terms else None
            note_parts = [p for p in (terms_note, " | ".join(counts["errors"]) or None) if p]
            repo.finish_run(
                tenant_id,
                run["id"],
                leads_found=counts["linkedin_saved"] + counts["instagram_saved"],
                messages_sent=0,
                status="error" if counts["errors"] else "completed",
                finished_at_iso=finished_at,
                notes=" || ".join(note_parts) or None,
                skipped_leads=counts["skipped_leads"],
            )

            summary.append({"tenant_id": tenant_id, "account": account["label"], **counts})

    return summary


# A search that comes back with fewer results than this fraction of its
# target count is "weak" -- worth widening the search terms for, rather than
# quietly accepting a thin batch. Never widen past MAX_SEARCH_ATTEMPTS
# rounds; both platforms' widen functions converge to "as wide as it gets"
# in a small, bounded number of steps anyway.
_WEAK_RESULT_FRACTION = 0.5
# Raised from 4 to 10 on 2026-09-13, then 10 to 20 later the same day. Real
# owner question after the first raise still wasn't enough: "we have 80,000
# companies in Lebanon, why can't we find 5?" The honest answer: every
# candidate must pass 6 checks in sequence (location, competitor, agency,
# company size, has-a-Message-button, AI quality score) -- even if each one
# individually only rejects a defensible ~30-35% of real companies, 6
# stacked checks compound to roughly a 90%+ overall rejection rate, so a
# LIVE-CONFIRMED real run visited 8 real candidates and saved 0. 10 rounds
# x ~10 LinkedIn results/page was never enough volume to survive that
# compounding -- 20 rounds doubles the daily candidate ceiling (~100 to
# ~200/account/day) so the funnel has enough raw volume to reach 5 real
# saves even at a low per-candidate pass rate. Paired with loosening two of
# the weakest-justified checks the same day (message-button pre-check,
# strict no-location-signal reject) -- see each's own comment.
_MAX_SEARCH_ATTEMPTS = 20

# Found in the 2026-09-12 review, real owner complaint ("we have millions
# of companies, it's not okay to not reach 5-10/day"): the raw search loop
# above stopped collecting candidates as soon as it hit roughly HALF the
# account's daily limit (_WEAK_RESULT_FRACTION), before location/company-
# size/personal-account qualification had even run -- so a limit of 5 could
# genuinely end with 1-2 real saved leads if half the raw batch got
# filtered out downstream, with no attempt to go find more. This
# multiplies the raw-collection target so qualification has real headroom:
# search for OVERSAMPLE_MULTIPLIER x the daily limit before the loop is
# satisfied, then still cap the final SAVED count at the real daily limit
# (existing `results[:limit]` truncation, untouched) -- this only makes the
# search look harder for candidates, it does not raise how many get sent.
_DISCOVERY_OVERSAMPLE_MULTIPLIER = 3

# A search round returning fewer than this many RAW candidates is treated
# as a bad keyword rather than a real attempt -- see the check's own
# comment in _discover_linkedin for the live evidence behind it.
_MIN_VIABLE_SEARCH_RESULTS = 3

# LIVE-CONFIRMED 2026-09-01: LinkedIn's own companyHqGeo search facet let a
# UK company (ZAM FM LTD, Manchester) through a Lebanon-filtered search --
# and that company's own About page had no "Headquarters" field at all to
# cross-check against (confirmed live: most company pages don't populate
# it), so a field-based check alone can't catch this. What the page DID
# have was its own bio text stating "your trusted partner in Manchester"
# outright -- this scans the bio (already scraped, no extra request) for a
# real foreign place name as a red flag. Deliberately NOT exhaustive (no
# list can name every place on Earth) -- this only needs to catch the
# common case of a company plainly stating a non-Lebanon city/country in
# its own description, same as ZAM FM did.
_FOREIGN_LOCATION_MARKERS = [
    "manchester", "london", "united kingdom", " uk ", "u.k.",
    "united states", "usa", "u.s.a.", "new york", "california",
    "canada", "toronto", "australia", "sydney", "dubai", "abu dhabi",
    "saudi arabia", "riyadh", "jeddah", "egypt", "cairo", "jordan", "amman",
    "france", "paris", "germany", "berlin", "india", "mumbai", "delhi",
    "pakistan", "nigeria", "kenya", "south africa", "singapore",
    # Added 2026-09-13: "Bhavani Consultants - India" listed its HQ as
    # "Kochi, Kerala" and named no country, so none of the markers above
    # matched -- it was only caught by the separate Headquarters-field
    # check. These are the Indian city/state names that actually appeared
    # in real rejected leads, plus the common ones most likely to recur.
    "kochi", "kerala", "bangalore", "bengaluru", "chennai", "hyderabad",
    "pune", "kolkata", "ahmedabad", "gujarat", "maharashtra", "bihar",
    "madhubani", "noida", "gurgaon", "gurugram",
]

# LIVE-CONFIRMED 2026-09-02: the headquarters-field check below originally
# required the literal word "lebanon" to appear in the company's own
# Headquarters text -- real-tested against Insurance's live discovery and
# it wrongly rejected multiple genuine Lebanese companies (Arabia Insurance
# Company: "Beirut, Beirut"; Adir Insurance: "Dora, Jdeidet Metn"; Insurance
# & Investment Consultant s.a.r.l: "Bsalim, Beirut") because LinkedIn's own
# Headquarters field lists a specific city/district, not the country name,
# in the common case. This is the real fix: a maintained list of actual
# Lebanese cities/districts to accept as a match too, not just the literal
# country name. Deliberately not exhaustive (no list can cover every
# village), but covers the major cities/areas real companies list.
_LEBANON_PLACE_MARKERS = [
    "lebanon", "beirut", "jdeidet", "jdeideh", "metn", "dora", "dbayeh",
    "dbaye", "bsalim", "jounieh", "jbeil", "byblos", "tripoli", "sidon",
    "saida", "tyre", "sour", "zahle", "baabda", "hazmieh", "ashrafieh",
    "achrafieh", "hamra", "verdun", "sin el fil", "sinelfil", "mtayleb",
    "bauchrieh", "antelias", "zalka", "jal el dib", "kaslik", "zouk",
    "keserwan", "chouf", "aley", "batroun", "koura",
]


# Maps each city/abbreviation in _FOREIGN_LOCATION_MARKERS to the wider
# regions that contain it, so a tenant targeting a COUNTRY (or a bloc like
# "GCC" / "MENA" / "Europe") isn't told one of its own cities is foreign.
# See _mentions_foreign_location for the real bug this fixes.
_CITY_PARENT_REGIONS: dict[str, tuple[str, ...]] = {
    "manchester": ("united kingdom", "uk", "britain", "england", "europe"),
    "london": ("united kingdom", "uk", "britain", "england", "europe"),
    " uk ": ("united kingdom", "britain", "england", "europe"),
    "u.k.": ("united kingdom", "britain", "england", "europe"),
    "usa": ("united states", "u.s.", "america"),
    "u.s.a.": ("united states", "america"),
    "new york": ("united states", "usa", "u.s.", "america"),
    "california": ("united states", "usa", "u.s.", "america"),
    "toronto": ("canada",),
    "sydney": ("australia",),
    "dubai": ("uae", "united arab emirates", "gcc", "mena"),
    "abu dhabi": ("uae", "united arab emirates", "gcc", "mena"),
    "riyadh": ("saudi arabia", "saudi", "gcc", "mena"),
    "jeddah": ("saudi arabia", "saudi", "gcc", "mena"),
    "cairo": ("egypt", "mena"),
    "amman": ("jordan", "mena"),
    "paris": ("france", "europe"),
    "berlin": ("germany", "europe"),
    "france": ("europe",),
    "germany": ("europe",),
    "mumbai": ("india",),
    "delhi": ("india",),
}


def _mentions_foreign_location(bio: str, configured_location: str) -> str | None:
    """
    Real, deliberately-imperfect safety net -- see _FOREIGN_LOCATION_MARKERS'
    own comment for why this exists and what it can't cover. Returns the
    matched marker text if the bio plainly names a location that isn't the
    tenant's configured target, or None if nothing in the list matched
    (not proof the company IS in the right place -- just that this specific
    check found no red flag). Case-insensitive; only runs when a location
    is actually configured, since there's nothing to contradict otherwise.
    """
    if not configured_location:
        return None
    configured = configured_location.lower()
    bio_lower = f" {bio.lower()} "
    for marker in _FOREIGN_LOCATION_MARKERS:
        if marker in configured:
            continue  # the marker IS the configured location -- not foreign
        # A CITY in a targeted country is not foreign. LIVE-CONFIRMED
        # 2026-09-13: MJivity targets "GCC (UAE, ...), Egypt, Lebanon, ...
        # United States, United Kingdom, Canada, Europe", but the plain
        # substring check above only protected exact country names -- so a
        # company in Dubai, London, New York, Toronto, Paris or Berlin was
        # rejected as "foreign" even though every one of those sits in a
        # country this tenant explicitly targets. That silently rejected
        # MJivity's entire real market.
        parents = _CITY_PARENT_REGIONS.get(marker)
        if parents and any(p in configured for p in parents):
            continue
        if marker in bio_lower:
            return marker.strip()
    return None


# Every tenant needs "don't pitch my own competitors" -- a company selling
# the same thing the tenant sells is the tenant's competitor, not a
# prospect, no matter how well it otherwise qualifies. Originally built for
# Insurance only (2026-09-02, real instruction: never target other
# insurance companies) and generalized 2026-09-13 after Zimmar's real
# overnight leads included linksecurtysystem, guardify.cloud,
# avtrade.integration, and earthlink_telecommunications -- all security/
# telecom/networking companies, i.e. Zimmar's own competitors, because no
# equivalent check had ever existed for Zimmar. Keyed by BUSINESS NAME
# (settings.business_name) rather than a tenant_id lookup table so a new
# tenant gets this for free the moment its own markers are added here,
# with no scheduler.py code change needed elsewhere.
_COMPETITOR_MARKERS_BY_BUSINESS = {
    "partners insurance consultancy": [
        "insurance", "insurer", "reinsurance", "assurance company",
        "takaful", "underwriter", "underwriting",
    ],
    "zimmar": [
        "cctv", "security camera", "security cameras", "surveillance",
        "video surveillance", "access control", "alarm system",
        "alarm systems", "security system", "security systems",
        "security integrator", "security integration", "burglar alarm",
        "intrusion detection", "video monitoring", "security solutions",
    ],
}


# Added 2026-09-13, real owner instruction: "no agencies only companies and
# businesses" -- given specifically about Zimmar and Insurance (both sell to
# a company's own physical premises/insurable operation, which an agency
# reselling a service doesn't have).
#
# REVERSED the same night, real owner reconsideration during a live test:
# "some agencies have good number of employees" -- an agency with a real
# office and real staff genuinely has real premises to protect (Zimmar) and
# a real insurable operation (Insurance/workers' comp/property/liability)
# just like any other company; being an agency doesn't mean it lacks
# physical presence. Owner confirmed explicitly for BOTH tenants: "Yes,
# both -- remove the no-agency rule entirely for both tenants." Left EMPTY
# rather than deleting the mechanism -- _is_agency() and the wiring in both
# discovery loops stay in place (harmless, always False-gated) in case a
# future tenant genuinely needs this rule the way MJivity's own opposite
# preference (agencies ARE its real market, see
# outreach-tenant-targeting-rules memory) shows real per-tenant judgment
# calls exist here.
_AGENCY_EXCLUDED_BUSINESSES: set[str] = set()


def _is_competitor(business_name: str, bio: str, industry: str | None) -> bool:
    """
    True if the LEAD's own bio or LinkedIn-listed industry plainly
    identifies it as a competitor of the tenant currently running discovery
    (`business_name` = this tenant's own settings.business_name, NOT the
    lead's name). Checked against BOTH bio and industry since either can
    carry the signal. Deliberately simple substring matching, same posture
    as _mentions_foreign_location() above: not exhaustive, but catches the
    common, plain case rather than needing an LLM call for every lead.

    A tenant with no entry here (a future tenant not yet added) matches
    nothing -- fails open rather than raising, since a missing entry means
    "not yet configured," not "this tenant has no competitors."
    """
    markers = _COMPETITOR_MARKERS_BY_BUSINESS.get((business_name or "").strip().lower())
    if not markers:
        return False
    haystack = f" {bio.lower()} {(industry or '').lower()} "
    return any(marker in haystack for marker in markers)


def _is_weak(found: int, limit: int) -> bool:
    return found < max(3, int(limit * _WEAK_RESULT_FRACTION))


# Leading words that make a peeled niche term meaningless as a search
# keyword ("and building infrastructure integration").
_NICHE_STOPWORDS = {"and", "or", "the", "a", "an", "of", "for", "in", "with", "&"}


def _next_search_terms(
    search_niche: str, search_location: str, original_location: str,
    niche_is_random: bool, tried_niches: set[str],
    tenant_terms: list[str] | None = None,
) -> tuple[str, str] | None:
    """
    Pick the NEXT (niche, location) pair to search after a round failed to
    produce enough SAVED leads. Returns None when there is genuinely nothing
    new left to try.

    Deliberately does NOT use linkedin.widen_search_terms' drop-the-location
    behavior as its first move. LIVE-CONFIRMED 2026-09-12 (Zimmar): dropping
    a Lebanon-configured tenant's location made LinkedIn return worldwide
    companies, every one of which then failed the post-visit location check
    (Dubai/India/Manchester/Ontario/Yerevan) -- the widening itself
    manufactured the rejects that produced a 0-lead run. For a
    location-scoped tenant, keeping the location and changing the KEYWORD is
    the move that actually opens up new real candidates.
    """
    # 1. A different keyword, same location -- the productive move.
    if niche_is_random:
        # Prefer the tenant's OWN configured sectors when it listed any
        # (MJivity's e-commerce / fashion / cosmetics / jewelry / automotive
        # list), falling back to the generic Lebanese-industry rotation only
        # for a tenant that genuinely targets every industry.
        pool_terms = tenant_terms or _RANDOM_INDUSTRY_TERMS
        untried = [t for t in pool_terms if t not in tried_niches]
        if not untried and tenant_terms:
            untried = [t for t in _RANDOM_INDUSTRY_TERMS if t not in tried_niches]
        if untried:
            return random.choice(untried), original_location
    else:
        # A real configured niche (e.g. Zimmar's "Security and building
        # infrastructure integration"): peel one leading qualifier word off
        # at a time, which yields genuinely broader but still ON-TOPIC terms
        # ("building infrastructure integration" -> "infrastructure
        # integration" -> "integration"), keeping the location intact.
        #
        # Deliberately NOT linkedin.widen_search_terms() here: that function
        # returns ("", "") once the location argument is already empty, so
        # passing "" to isolate the niche-shortening half of it silently
        # yields an empty niche and kills the round. Caught by this file's
        # own unit check before it ever shipped.
        words = search_niche.split()
        while len(words) > 1:
            words = words[1:]
            # Skip a leading stopword: peeling "Security and building ..."
            # one word at a time otherwise burns a whole round on the junk
            # term "and building infrastructure integration".
            while len(words) > 1 and words[0].lower() in _NICHE_STOPWORDS:
                words = words[1:]
            shorter = " ".join(words)
            if shorter not in tried_niches:
                return shorter, original_location
        # Niche fully peeled down and every step already tried -- fall
        # through to the location-drop check below.

    # 2. Only once keywords are exhausted, fall back to dropping the
    # location -- last resort, and only for a tenant that has no location
    # configured to begin with (otherwise every result fails the location
    # check anyway, per the docstring above).
    if not original_location and search_location:
        return search_niche, ""
    return None


def _discover_linkedin(
    account: dict, page, niche: str, location: str, industry: str, counts: dict,
    min_company_size: int | None = None, niche_is_random: bool = False,
    size_buckets: list[str] | None = None, tenant_terms: list[str] | None = None,
    business_name: str = "",
) -> None:
    limit = warmup.effective_limit(account, "linkedin")
    # Raw-collection target per ROUND, not the final saved count.
    search_target = limit * _DISCOVERY_OVERSAMPLE_MULTIPLIER
    search_niche, search_location = niche, location
    seen_urls: set[str] = set()
    tried_niches: set[str] = set()
    counts["linkedin_search_terms"] = []

    # RESTRUCTURED 2026-09-13 (real owner rule: "if those 15 don't give us
    # 5, go get another 15 after a few minutes"). Before this, search and
    # qualification were two SEPARATE sequential loops: the search loop
    # stopped as soon as it had enough RAW candidates, then qualification
    # ran once over them and whatever survived was the final answer, with no
    # way back. LIVE-CONFIRMED that this produced real 0-lead runs -- Zimmar
    # 2026-09-12 pulled 10 raw candidates (loop satisfied), then all 10 were
    # rejected on location, ending the run at 0 with 3 unused search
    # attempts still on the table. Now one loop does search -> visit ->
    # qualify per round, and only starts another round if the SAVED count is
    # still short, which is what the owner's rule actually asks for.
    for attempt in range(_MAX_SEARCH_ATTEMPTS):
        if counts["linkedin_saved"] >= limit:
            break
        if attempt > 0:
            next_terms = _next_search_terms(
                search_niche, search_location, location, niche_is_random, tried_niches,
                tenant_terms,
            )
            if next_terms is None:
                break  # genuinely nothing new left to try
            search_niche, search_location = next_terms
            # LIVE-CONFIRMED 2026-09-01: LinkedIn force-logged-out a real
            # account after a burst of back-to-back automated searches with
            # no pause between them. A real person doesn't fire searches
            # back to back; randomizing avoids a uniform, itself-suspicious
            # interval. This is also the owner's "wait some minutes before
            # the next batch" rule -- deliberately longer than the old
            # 15-30s now that a round is a whole search+visit+qualify pass.
            page.wait_for_timeout(random.randint(60_000, 150_000))

        tried_niches.add(search_niche)
        results: list[dict] = []
        counts["linkedin_search_terms"].append({"niche": search_niche, "location": search_location})
        _progress_log.info(
            "[%s] LinkedIn round %d/%d: searching %r in %r",
            account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS, search_niche, search_location,
        )
        page.goto(
            linkedin.build_search_url(search_niche, search_location, industry, size_buckets),
            timeout=30_000, wait_until="domcontentloaded",
        )
        # LIVE-CONFIRMED 2026-09-12: no explicit wait existed here at all --
        # LinkedIn's company search results render client-side, well after
        # domcontentloaded fires, the same class of issue instagram.py's own
        # hashtag search already hit and fixed (see that module's docstring:
        # "0 results at 5s, 21 results at 7s" on the identical query). A
        # manual diagnostic run caught this directly: the exact same
        # niche+location search that returned 0 in a real production run
        # returned 10 real companies moments later with a 4s wait added
        # before reading results. This is the likely real explanation for
        # Insurance's intermittent 0-result discovery runs -- not a search
        # or filter problem, a read-too-early problem.
        page.wait_for_timeout(5_000)
        for result in linkedin.extract_search_results(page):
            url = result.get("profile_url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append(result)

        # A round that surfaced almost nothing means the KEYWORD was bad,
        # not that Lebanon has no companies. LIVE-CONFIRMED 2026-09-13: a
        # direct probe of the same Lebanon geo facet returned 10-11 real
        # Lebanese companies for 'trading', 'construction' and
        # 'manufacturing', while that day's actual Insurance run picked a
        # term that surfaced exactly 1 (an Australian Bosch subsidiary) and
        # then burned its whole round qualifying it. Visiting one dud
        # candidate costs a minute and can't reach the daily target, so
        # skip straight to a different keyword instead of paying for it.
        _progress_log.info(
            "[%s] LinkedIn round %d/%d: %d raw result(s) for %r",
            account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS, len(results), search_niche,
        )
        if len(results) < _MIN_VIABLE_SEARCH_RESULTS and attempt < _MAX_SEARCH_ATTEMPTS - 1:
            counts.setdefault("thin_rounds", []).append(
                {"niche": search_niche, "location": search_location, "results": len(results)}
            )
            _progress_log.info(
                "[%s] LinkedIn round %d/%d: thin (<%d results), moving to next keyword after a pause",
                account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS, _MIN_VIABLE_SEARCH_RESULTS,
            )
            continue

        # Raw candidates from THIS round only. Not truncated to `limit`:
        # the real daily cap is enforced by the saved-count checks, not by
        # pre-filtering how many candidates get a chance to qualify.
        results = results[: max(search_target, limit)]
        counts["linkedin_found"] = counts.get("linkedin_found", 0) + len(results)

        for profile_index, result in enumerate(results):
            if counts["linkedin_saved"] >= limit:
                break
            profile_url = result.get("profile_url")
            if not profile_url:
                continue
            # Pace profile visits apart -- see _sleep_between_profile_visits().
            # Before the FIRST visit is skipped deliberately: the search-widening
            # loop above has already paused 15-30s of its own, so the run doesn't
            # need a third wait before any real work starts.
            if profile_index > 0:
                _sleep_between_profile_visits()
            _progress_log.info(
                "[%s] LinkedIn round %d/%d: visiting candidate %d/%d (%s) -- saved so far: %d/%d",
                account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                profile_index + 1, len(results), result.get("display_name") or profile_url,
                counts["linkedin_saved"], limit,
            )
            try:
                # extract_company_profile() reads the /about subpage specifically
                # (not the bare company page) -- see that function's docstring
                # for why, re-verified 2026-08-03 after the bare page stopped
                # carrying Website/Industry/size info.
                page.goto(profile_url.rstrip("/") + "/about/", timeout=30_000, wait_until="domcontentloaded")
                profile = linkedin.extract_company_profile(page)
                profile["display_name"] = result.get("display_name")

                page.goto(profile_url.rstrip("/") + "/posts/", timeout=30_000, wait_until="domcontentloaded")
                posts_info = linkedin.extract_recent_posts(page)
                profile["post_count"] = posts_info["visible_post_count"]
                profile["recent_activity"] = posts_info["recent_activity"]

                # LIVE-CONFIRMED 2026-09-01: LinkedIn's own companyHqGeo search
                # facet let a UK company (ZAM FM LTD, Manchester) through a
                # Lebanon-filtered search -- confirmed the exact search URL
                # really did carry the Lebanon facet, so this is bad/stale
                # location data on LinkedIn's own side, not a bug in how the
                # search was built. Two independent, best-effort checks here
                # (neither alone is sufficient -- see each's own comment):
                # (1) the About page's own "Headquarters" field, when present
                # (LIVE-CONFIRMED: often isn't -- ZAM FM's page had none at
                # all, so this check alone would have missed it); (2) scanning
                # the bio text for a known foreign place name
                # (_FOREIGN_LOCATION_MARKERS) -- this is what actually would
                # have caught ZAM FM, whose bio opened with "your trusted
                # partner in Manchester".
                headquarters = (profile.get("headquarters") or "").strip().lower()
                configured_location = (location or "").strip().lower()
                mismatch_reason = None

                # RELAXED 2026-09-13, real owner-confirmed fix: LIVE-CAUGHT
                # tonight, the strict re-check below rejected Dar (Dar
                # Al-Handasah) and Aramex -- two large, famous, genuinely
                # Lebanese-FOUNDED companies -- purely because their
                # LinkedIn Headquarters field lists their current GLOBAL hq
                # (Singapore, Dubai) rather than Beirut, where they
                # started and still operate. Both were surfaced by THIS
                # SAME SEARCH's own companyHqGeo=Lebanon facet -- LinkedIn
                # itself already confirmed a real Lebanon connection before
                # this code ever saw the result, and the stricter
                # Headquarters/bio re-check then threw that confirmation
                # away. When the search was already geo-faceted (true for
                # Zimmar/Insurance, whose configured_location=="lebanon"
                # always resolves via LOCATION_FACETS), trust LinkedIn's own
                # facet and skip the extra re-verification entirely --
                # applying the same location value the search itself
                # already applied a second time, more strictly, was the
                # actual bug, not a real safety net.
                search_used_geo_facet = bool(linkedin.LOCATION_FACETS.get(configured_location))

                if not search_used_geo_facet:
                    # Fallback for a tenant/location LinkedIn has no verified
                    # geo facet ID for (search fell back to free-text
                    # keywords, so LinkedIn never verified location itself)
                    # -- same checks as before, still needed here.
                    if configured_location and headquarters and configured_location not in headquarters:
                        # LIVE-CONFIRMED 2026-09-02: a bare country-name substring
                        # check alone false-positived on real Lebanese companies
                        # whose Headquarters field lists a city/district instead of
                        # the word "Lebanon" (see _LEBANON_PLACE_MARKERS' own
                        # comment for the real examples this caught) -- for a
                        # Lebanon-configured tenant specifically, also accept a
                        # known Lebanese place name as a match before flagging.
                        is_known_lebanon_place = (
                            configured_location == "lebanon"
                            and any(place in headquarters for place in _LEBANON_PLACE_MARKERS)
                        )
                        if not is_known_lebanon_place:
                            mismatch_reason = (
                                f"configured for {location!r}, company's own About page lists "
                                f"headquarters as {profile.get('headquarters')!r}."
                            )

                    if not mismatch_reason:
                        foreign_marker = _mentions_foreign_location(profile.get("bio") or "", location or "")
                        if foreign_marker:
                            mismatch_reason = (
                                f"configured for {location!r}, company's own bio mentions {foreign_marker!r}."
                            )

                    if not mismatch_reason and configured_location == "lebanon":
                        has_lebanon_signal = any(
                            place in headquarters or place in (profile.get("bio") or "").lower()
                            for place in _LEBANON_PLACE_MARKERS
                        )
                        if not has_lebanon_signal:
                            mismatch_reason = (
                                "configured for 'lebanon', but neither the company's Headquarters "
                                "field nor its bio names any Lebanese location -- no positive signal "
                                "this is a Lebanese company."
                            )

                if mismatch_reason:
                    counts["skipped_leads"].append({
                        "platform": "linkedin",
                        "identifier": result.get("display_name") or profile_url,
                        "reason": f"Location mismatch: {mismatch_reason}",
                    })
                    _progress_log.info(
                        "[%s] LinkedIn round %d/%d: rejected (location) %s -- %s",
                        account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                        result.get("display_name") or profile_url, mismatch_reason,
                    )
                    continue

                # Own competitors -- see _is_competitor()'s own comment.
                if _is_competitor(business_name, profile.get("bio") or "", profile.get("industry")):
                    counts["skipped_leads"].append({
                        "platform": "linkedin",
                        "identifier": result.get("display_name") or profile_url,
                        "reason": f"Excluded: this company competes with {business_name or 'this tenant'}.",
                    })
                    _progress_log.info(
                        "[%s] LinkedIn round %d/%d: rejected (competitor) %s",
                        account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                        result.get("display_name") or profile_url,
                    )
                    continue

                # No agencies -- see _AGENCY_EXCLUDED_BUSINESSES' own comment.
                if (business_name or "").strip().lower() in _AGENCY_EXCLUDED_BUSINESSES and _lead_is_agency(
                    result.get("display_name") or "", profile.get("bio") or ""
                ):
                    counts["skipped_leads"].append({
                        "platform": "linkedin",
                        "identifier": result.get("display_name") or profile_url,
                        "reason": "Excluded: this is an agency, not an operating company/business.",
                    })
                    _progress_log.info(
                        "[%s] LinkedIn round %d/%d: rejected (agency) %s",
                        account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                        result.get("display_name") or profile_url,
                    )
                    continue

                # No headcount listed at all -> benefit of the doubt, let it
                # through (owner's explicit call 2026-09-12: reject-on-unknown
                # was shrinking an already-narrow pool, e.g. Insurance's 100+
                # filter, harder than the owner wants). Only a KNOWN headcount
                # below the configured minimum is a real rejection.
                headcount = profile.get("follower_or_headcount")
                if min_company_size and headcount is not None and headcount < min_company_size:
                    counts["skipped_leads"].append({
                        "platform": "linkedin",
                        "identifier": result.get("display_name") or profile_url,
                        "reason": f"Below configured minimum company size: needs {min_company_size}+ employees, company shows {headcount}.",
                    })
                    _progress_log.info(
                        "[%s] LinkedIn round %d/%d: rejected (company size: %s < %s) %s",
                        account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                        headcount, min_company_size, result.get("display_name") or profile_url,
                    )
                    continue

                # REMOVED 2026-09-13, real owner decision after live testing:
                # this pre-check (added earlier the same night after
                # TEAMWORK ENERGY failed at send time) turned out to have a
                # ~25% false-reject rate on real, reachable companies
                # (LebEx, Bilani Transportation, OPES Software, Arab
                # Software Company all wrongly rejected) even after two
                # rounds of tuning its render-wait timing (3s, then 6s) --
                # the droplet's CPU contention (see session.py's own
                # comment on the 1.9GB RAM/1 vCPU constraint) makes a fixed
                # wait an unreliable signal. Owner's call: losing real,
                # reachable leads to false rejects here is worse than the
                # actual cost of a genuine no-button lead reaching send
                # time -- linkedin_send.py's send path already catches
                # NoMessageButtonAvailable cleanly (no crash, marks the
                # send failed) rather than silently succeeding or losing
                # the lead, and the Approval queue now shows the SPECIFIC
                # reason ("No Message button", not just a generic "Failed")
                # so the owner can tell the two failure modes apart at a
                # glance -- see outreach-approvals.ts's own comment on
                # sendStatusReason.

                if _save_if_qualified(account, "linkedin", profile_url, profile, niche):
                    counts["linkedin_saved"] += 1
                    _progress_log.info(
                        "[%s] LinkedIn round %d/%d: SAVED %s",
                        account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                        result.get("display_name") or profile_url,
                    )
                else:
                    _progress_log.info(
                        "[%s] LinkedIn round %d/%d: rejected by qualify_profile: %s",
                        account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                        result.get("display_name") or profile_url,
                    )
            except Exception as exc:  # noqa: BLE001 -- one bad lead shouldn't stop the rest of the batch
                counts["skipped_leads"].append({
                    "platform": "linkedin",
                    "identifier": result.get("display_name") or profile_url,
                    "reason": str(exc),
                })
                # Added 2026-09-13, real bug found live: this exception path
                # was silently swallowing candidates (a /posts/ page-load
                # timeout, in particular) with no visible trace beyond the
                # DB error_log table -- from the discovery-progress log
                # alone, a candidate lost here looked IDENTICAL to one
                # correctly rejected by qualify_profile, which made a real
                # page-timeout problem look like an over-strict qualify bug.
                _progress_log.info(
                    "[%s] LinkedIn round %d/%d: EXCEPTION on %s -- %s",
                    account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                    result.get("display_name") or profile_url, exc,
                )
                log_error("discovery", exc, channel="linkedin", account_id=account["id"])


# Hashtags that real BUSINESSES tag their own posts with, as opposed to
# hashtags describing a service someone sells. Added 2026-09-13 on the
# owner's explicit instruction ("the hashtags should be related to
# companies, not strictly to cgi or vfx, because we are reaching
# companies"): searching #cgi surfaces other CGI artists showing off their
# work, not the brands who might BUY it. These are the tags a real company
# posts under, so they surface actual businesses.
# REVISED 2026-09-13 after reviewing what these actually returned: the first
# version of this list included "entrepreneur", "business owner", "small
# business" and "startup". On Instagram those are overwhelmingly COACHES,
# consultants and influencers talking ABOUT business -- the exact "we should
# try to not get individuals" problem -- not companies posting their own
# products. Tags kept here are ones a real product business posts under
# while showing its own goods, which is also what makes it a live prospect
# for product visuals, insurance, or security hardware alike.
_BUSINESS_HASHTAG_TERMS = [
    "new collection",
    "new arrival",
    "product launch",
    "now open",
    "our store",
    "showroom",
    "free delivery",
    "order now",
    "shop now",
    "made in lebanon",
    "local business",
    "family business",
    "wholesale",
    "boutique",
]


def _next_hashtag(
    search_niche: str, original_niche: str, tried_tags: set[str],
    tenant_terms: list[str] | None = None,
) -> str | None:
    """
    Pick the next hashtag to try after a round came up short.

    Order: (1) peel a leading qualifier word off the configured niche, which
    keeps the search on-topic while broadening it; (2) once that's exhausted,
    rotate through _BUSINESS_HASHTAG_TERMS, which surface real companies
    rather than people selling the same service (see that list's own
    comment). Returns None when everything has been tried.

    Peeling deliberately STOPS while at least two words remain. Peeling all
    the way down to a single generic word is actively harmful on Instagram:
    MJivity's "small business marketing" degraded to "marketing", and
    #marketing is almost entirely marketing influencers and agencies rather
    than the product brands MJivity is trying to reach. A two-word tag stays
    specific enough to describe a business; the curated list below is a
    better fallback than a one-word tag ever is.
    """
    words = search_niche.split()
    while len(words) > 2:
        words = words[1:]
        while len(words) > 2 and words[0].lower() in _NICHE_STOPWORDS:
            words = words[1:]
        shorter = " ".join(words)
        if shorter not in tried_tags:
            return shorter

    # The tenant's own configured sectors come before the generic list --
    # #jewelry or #cosmetics surfaces actual brands for MJivity far better
    # than any all-purpose business tag can.
    if tenant_terms:
        untried_tenant = [t for t in tenant_terms if t not in tried_tags]
        if untried_tenant:
            return random.choice(untried_tenant)

    untried = [t for t in _BUSINESS_HASHTAG_TERMS if t not in tried_tags]
    if untried:
        return random.choice(untried)
    return None


def _discover_instagram(
    account: dict, page, niche: str, counts: dict,
    tenant_terms: list[str] | None = None, business_name: str = "",
    location: str = "",
) -> None:
    # No min_company_size parameter here on purpose: Instagram's own
    # follower_or_headcount field (instagram.py) is a FOLLOWER count, not
    # an employee count the way LinkedIn's version genuinely is (scraped
    # from the company's About page) -- applying a company-size floor to
    # follower count would filter on social-media popularity, not company
    # size, which is a different and wrong signal. Company-size filtering
    # only makes sense on LinkedIn, where the underlying number is real.
    limit = warmup.effective_limit(account, "instagram")
    # Raw-collection target per ROUND, not the final saved count.
    search_target = limit * _DISCOVERY_OVERSAMPLE_MULTIPLIER
    search_niche = niche
    seen_urls: set[str] = set()
    tried_tags: set[str] = set()
    counts["instagram_search_terms"] = []

    # RESTRUCTURED 2026-09-13, same fix and same reason as
    # _discover_linkedin above: search and qualification used to be two
    # separate sequential loops, so a round whose candidates all got
    # rejected ended the run at 0 with search attempts still unused. One
    # loop now does search -> visit -> qualify per round and only starts
    # another round if the SAVED count is still short.
    for attempt in range(_MAX_SEARCH_ATTEMPTS):
        if counts["instagram_saved"] >= limit:
            break
        if attempt > 0:
            next_tag = _next_hashtag(search_niche, niche, tried_tags, tenant_terms)
            if next_tag is None:
                break
            search_niche = next_tag
            # Owner's "wait some minutes before the next batch" rule, and
            # the same anti-burst reasoning as the LinkedIn loop's own
            # wait -- a real person doesn't fire hashtag searches back to
            # back.
            page.wait_for_timeout(random.randint(60_000, 150_000))

        tried_tags.add(search_niche)
        posts: list[dict] = []
        counts["instagram_search_terms"].append(search_niche)
        _progress_log.info(
            "[%s] Instagram round %d/%d: searching hashtag %r",
            account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS, search_niche,
        )
        # RE-VERIFIED live 2026-08-03: Instagram now redirects
        # /explore/tags/<tag>/ to /explore/search/keyword/?q=%23<tag> (a
        # generic search page, confirmed universal across multiple tags, not
        # a per-tag quirk) -- build_hashtag_url's URL itself still gets
        # there, but that page's results render client-side well after
        # domcontentloaded. The original code had no wait at all here, so it
        # was reading the page before any results existed, which is why every
        # discovery run before this fix found 0 Instagram leads regardless of
        # niche. This delay is genuinely inconsistent across real runs --
        # live testing saw 0 results at 5s, 21 results at 7s and 9s on
        # separate attempts, then 0 again at 7s in a later production run --
        # 10s was chosen for more margin, but this remains network-dependent
        # and worth revisiting if 0-result runs keep happening.
        page.goto(instagram.build_hashtag_url(search_niche), timeout=30_000, wait_until="domcontentloaded")
        page.wait_for_timeout(10_000)
        for post in instagram.extract_hashtag_results(page):
            url = post.get("post_url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                posts.append(post)

        _progress_log.info(
            "[%s] Instagram round %d/%d: %d raw post(s) for %r",
            account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS, len(posts), search_niche,
        )
        # Raw candidates from THIS round only; the daily cap is enforced by
        # the saved-count checks, not by pre-filtering the batch.
        posts = posts[: max(search_target, limit)]
        counts["instagram_found"] = counts.get("instagram_found", 0) + len(posts)

        for post_index, post in enumerate(posts):
            if counts["instagram_saved"] >= limit:
                break
            # Same profile-visit pacing as the LinkedIn loop above -- each
            # iteration here navigates to a post AND then to that poster's
            # profile, so an unpaced loop is two rapid page loads per lead.
            if post_index > 0:
                _sleep_between_profile_visits()
            _progress_log.info(
                "[%s] Instagram round %d/%d: visiting candidate %d/%d -- saved so far: %d/%d",
                account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                post_index + 1, len(posts), counts["instagram_saved"], limit,
            )
            try:
                profile_url = instagram.resolve_post_to_profile_url(page, post["post_url"])
                if not profile_url:
                    continue
                engagement = instagram.extract_post_engagement(page)  # page is still on the post/reel here
                page.goto(profile_url, timeout=30_000, wait_until="domcontentloaded")
                profile = instagram.extract_profile(page)
                profile["engagement_sample"] = engagement
                # RE-VERIFIED 2026-08-03: extract_profile() has no display_name
                # field at all -- Instagram's real display name ("Toi Kruvasan")
                # only exists as plain DOM text with no stable selector or
                # semantic meta tag backing it (og:title only has the @username,
                # same as the URL). Caught via a real supervised discovery run
                # where every saved Instagram lead's business_name came back
                # null. Using the @username (already reliably in profile_url) as
                # business_name instead of leaving it null -- less pretty than a
                # real display name, but always present and never guessed at.
                profile["display_name"] = profile_url.rstrip("/").rsplit("/", 1)[-1]
                # Own competitors -- see _is_competitor()'s own comment. This
                # check previously only existed on the LinkedIn side, so
                # Zimmar's Instagram discovery had no defense against
                # surfacing other CCTV/security-camera/alarm accounts.
                if _is_competitor(business_name, profile.get("bio") or "", None):
                    counts["skipped_leads"].append({
                        "platform": "instagram",
                        "identifier": profile.get("display_name") or profile_url,
                        "reason": f"Excluded: this account competes with {business_name or 'this tenant'}.",
                    })
                    _progress_log.info(
                        "[%s] Instagram round %d/%d: rejected (competitor) %s",
                        account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                        profile.get("display_name") or profile_url,
                    )
                    continue

                # No agencies -- see _AGENCY_EXCLUDED_BUSINESSES' own comment.
                if (business_name or "").strip().lower() in _AGENCY_EXCLUDED_BUSINESSES and _lead_is_agency(
                    profile.get("display_name") or "", profile.get("bio") or ""
                ):
                    counts["skipped_leads"].append({
                        "platform": "instagram",
                        "identifier": profile.get("display_name") or profile_url,
                        "reason": "Excluded: this is an agency, not an operating company/business.",
                    })
                    _progress_log.info(
                        "[%s] Instagram round %d/%d: rejected (agency) %s",
                        account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                        profile.get("display_name") or profile_url,
                    )
                    continue

                # Added 2026-09-13, real owner instruction ("very strict
                # with location... outside lebanon... one in dubai and one
                # in australia"). Instagram discovery had ZERO location
                # checking of any kind before this -- unlike
                # _discover_linkedin, which at least scans a Headquarters
                # field and the bio, Instagram's extract_profile() has no
                # location field at all (see that function's own docstring
                # -- Instagram doesn't expose one on the profile page), so
                # bio text is the only signal available.
                #
                # RELAXED the same day: originally also hard-rejected a bio
                # that named NO Lebanese place at all -- removed after a
                # live A-to-Z run visited 8 real candidates and saved 0,
                # exposing that 6 stacked hard-reject checks compound far
                # more aggressively than any one of them looks in isolation
                # (owner: "loosen the weakest-justified filters first").
                # Only a POSITIVE foreign signal is a hard reject now; a
                # silent bio falls through to qualify_profile's own soft
                # +1 _mentions_a_location() signal instead of an instant
                # reject here.
                bio_text = profile.get("bio") or ""
                foreign_marker = _mentions_foreign_location(bio_text, location or "")
                if foreign_marker:
                    counts["skipped_leads"].append({
                        "platform": "instagram",
                        "identifier": profile.get("display_name") or profile_url,
                        "reason": f"Location mismatch: configured for {location!r}, bio mentions {foreign_marker!r}.",
                    })
                    _progress_log.info(
                        "[%s] Instagram round %d/%d: rejected (foreign location: %s) %s",
                        account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                        foreign_marker, profile.get("display_name") or profile_url,
                    )
                    continue
                # RELAXED 2026-09-13, real owner-approved fix (owner: "loosen
                # the weakest-justified filters first... message-button,
                # strict no-signal reject"). This hard "reject on total
                # bio silence" rule was the LAST location check standing for
                # Instagram after tonight's LinkedIn relaxation (LinkedIn's
                # own geo facet now handles Lebanon verification -- see
                # search_used_geo_facet's own comment above -- but Instagram
                # has no geo facet at all, so this was the only signal). It
                # is now a HARD REJECT only on a POSITIVE foreign signal
                # (foreign_marker, checked above -- this is what actually
                # caught the real Dubai/Australia leads the owner flagged).
                # A bio that just doesn't mention a city is no longer an
                # instant reject -- qualify_profile's own
                # _mentions_a_location() already scores this as a soft +1
                # signal, exactly the "not very very strict" posture the
                # owner asked for on location earlier tonight. Combined
                # with 6 OTHER checks a candidate must already pass, this
                # was one compounding rejection point too many for very
                # little actual precision gain (LinkedIn's much stronger
                # Headquarters-field version of this same rule already
                # remains in place as a genuine safety net there).
                if _save_if_qualified(account, "instagram", profile_url, profile, niche):
                    counts["instagram_saved"] += 1
                    _progress_log.info(
                        "[%s] Instagram round %d/%d: SAVED %s",
                        account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                        profile.get("display_name") or profile_url,
                    )
                else:
                    _progress_log.info(
                        "[%s] Instagram round %d/%d: rejected by qualify_profile: %s",
                        account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                        profile.get("display_name") or profile_url,
                    )
            except Exception as exc:  # noqa: BLE001 -- one bad lead shouldn't stop the rest of the batch
                counts["skipped_leads"].append({
                    "platform": "instagram",
                    "identifier": post.get("post_url"),
                    "reason": str(exc),
                })
                # See the LinkedIn loop's identical comment above -- a
                # silently swallowed exception here was indistinguishable
                # from a real qualify_profile rejection without this line.
                _progress_log.info(
                    "[%s] Instagram round %d/%d: EXCEPTION on %s -- %s",
                    account.get("label"), attempt + 1, _MAX_SEARCH_ATTEMPTS,
                    post.get("post_url"), exc,
                )
                log_error("discovery", exc, channel="instagram", account_id=account["id"])


def run_analysis_cycle(limit: int | None = None) -> list[dict]:
    """
    Analyze every lead currently sitting at status "discovered", across
    every tenant that currently has active Outreach accounts (Phase 4,
    ported multi-tenant 2026-08-20).

    Task 10 from the spec: the full enriched record is saved to
    client_history BEFORE any message is generated (see
    run_message_generation_cycle() below, Phase 5) -- client_history exists
    permanently, whether or not this lead is ever contacted.

    `limit` caps how many leads are processed PER TENANT in one call --
    useful for a careful first test rather than analysing an entire backlog
    at once.

    Tenant-level isolation: one tenant raising here is logged and skipped,
    same reasoning as run_cycle() above.
    """
    results = []
    for tenant_id in repo.list_active_tenant_ids():
        # start_stage_run/finish_run both write to the RLS-protected
        # outreach_runs table -- LIVE-CONFIRMED 2026-09-01, both must run
        # inside tenant_scope() (the earlier version called
        # start_stage_run before entering it, which real-tested as a hard
        # InsufficientPrivilege from Postgres's own RLS policy, not a
        # Python-level bug).
        with repo.tenant_scope(tenant_id):
            if repo.is_tenant_paused():
                continue
            run = repo.start_stage_run(tenant_id, "analysis")
            try:
                tenant_results = _run_analysis_cycle_for_tenant(tenant_id, limit)
                results.extend(tenant_results)
                ok_count = sum(1 for r in tenant_results if r.get("ok"))
                repo.finish_run(
                    tenant_id, run["id"], leads_found=len(tenant_results), messages_sent=0,
                    status="completed", finished_at_iso=dt.datetime.now(dt.timezone.utc).isoformat(),
                    notes=f"{ok_count}/{len(tenant_results)} leads analyzed successfully." if tenant_results else "No leads to analyze.",
                )
            except Exception as exc:  # noqa: BLE001 -- one bad tenant must not stop the others
                repo.finish_run(
                    tenant_id, run["id"], leads_found=0, messages_sent=0,
                    status="error", finished_at_iso=dt.datetime.now(dt.timezone.utc).isoformat(),
                    notes=str(exc),
                )
                try:
                    repo.insert_error({
                        "stage": "analysis", "error_message": str(exc), "is_expected": False,
                    }, tenant_id=tenant_id)
                except Exception:  # noqa: BLE001 -- logging itself must never crash the pipeline
                    pass
    return results


def _bare_domain(website: str | None) -> str | None:
    """
    "https://www.acmesecurity.com/about" -> "acmesecurity.com". Findymail's
    /search/name endpoint takes a bare domain (per its docs, e.g.
    "tesla.com"), not a full URL -- LinkedIn's captured website field is
    whatever a company put in their profile, which can be either shape.
    Returns None for anything that doesn't parse into a real host, rather
    than passing a garbage value to a paid API call.
    """
    if not website:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(website if "://" in website else f"https://{website}")
    host = parsed.netloc or parsed.path.split("/")[0]
    host = host.removeprefix("www.")
    return host or None


def _maybe_find_email(tenant_id: str, lead: dict, founder_name: str | None) -> None:
    """
    Best-effort: if this (LinkedIn) lead has a real website, look up an
    email for it and -- if found -- create a SEPARATE, linked
    `email`-platform OutreachLead for the same company (not a field bolted
    onto the LinkedIn lead itself), so the existing per-platform
    message-generation/approval/sending pipeline (run_message_generation_cycle,
    run_sending_cycle) handles it identically to any other email lead, no
    special-casing needed anywhere downstream. Same company, hit on two
    channels -- the tenant's explicit choice (see PROGRESS.md's dated entry
    on this feature).

    Two-tier lookup (2026-09-01, real behavior change -- see this
    function's own body): a known founder/decision-maker name gets a
    targeted person lookup first (Hunter Email Finder); if that's not
    available or comes up empty, falls back to a domain-wide lookup
    (Hunter Domain Search) that needs no name at all -- so a lead with no
    detected founder still gets a real shot at an email lead instead of
    being a dead end. The saved lead's `notes` records which of the two
    actually found it.

    Silent no-op (not an error) when: no website, HUNTER_API_KEY isn't
    set, or Hunter genuinely has no match on either tier -- every one of
    these is a normal, expected outcome for SOME leads, not a failure.
    Only a real Hunter API error (bad key, no credits) propagates, so the
    caller's existing per-lead try/except and log_error() isolation
    catches it the same way any other per-lead external-service failure
    already is.
    """
    if lead.get("platform") != "linkedin":
        return
    domain = _bare_domain(lead.get("website"))
    if not domain:
        return

    # Hunter is the ACTIVE provider (trialing its 50 free credits/month
    # first, per the tenant's explicit choice -- see discovery/hunter.py's
    # module docstring). Findymail stays wired and importable as the
    # fallback/comparison provider but is not called here; swap this one
    # call if the trial concludes Icypeas or Findymail should be used
    # instead, no other code needs to change either provider's own
    # exception names line up (HunterNotConfigured mirrors
    # FindymailNotConfigured) so this except clause needs no changes on swap.
    #
    # LIVE-CONFIRMED 2026-09-01: this used to require founder_name and
    # return immediately without one -- real behavior change, per the
    # platform owner's explicit request, to give every LinkedIn lead with a
    # real website a SECOND chance at an email lead even when no
    # founder/decision-maker name was ever detected. find_email() (person
    # lookup) stays the first, more targeted try when a name exists;
    # find_company_emails() (domain search, no name needed) is the
    # fallback -- tried only when either no name was found, or the named
    # lookup itself came back with nothing.
    email = None
    found_via = None
    if founder_name:
        try:
            email = hunter.find_email(founder_name, domain)
        except hunter.HunterNotConfigured:
            return  # no API key set yet -- not an error, just not wired up
        if email:
            found_via = f"Hunter Email Finder for {founder_name}"

    if not email:
        try:
            email = hunter.find_company_emails(domain)
        except hunter.HunterNotConfigured:
            return
        if email:
            found_via = "Hunter Domain Search (no founder name identified)"

    if not email:
        return

    # Dedup by the mailto: profile_url (same mechanism discovery already
    # uses for LinkedIn/Instagram profile URLs, see _save_if_qualified) --
    # a Findymail lookup that returns the same email a second time (e.g. a
    # stray re-analysis pass) must not create a duplicate email lead.
    profile_url = f"mailto:{email}"
    if repo.lead_profile_url_exists(tenant_id, profile_url):
        return

    accounts = repo.list_accounts(tenant_id)
    email_account = next((a for a in accounts if a.get("platform") == "email" and a.get("status") == "active"), None)

    repo.insert_lead(tenant_id, {
        "account_id": email_account["id"] if email_account else None,
        "platform": "email",
        "business_name": lead.get("business_name"),
        "profile_url": profile_url,  # no real "profile" for an email lead -- mailto: URI doubles as both display value and the dedup key
        "website": lead.get("website"),
        "contact_email": email,
        "status": "discovered",
        "notes": f"Email found via {found_via}, linked from LinkedIn lead {lead.get('id')}.",
    })


def _run_analysis_cycle_for_tenant(tenant_id: str, limit: int | None) -> list[dict]:
    leads = repo.leads_by_status("discovered", tenant_id=tenant_id)
    if limit is not None:
        leads = leads[:limit]

    results = []
    for lead in leads:
        try:
            analysis = analyze.analyze_lead(lead)
            founder_result = founder_detection.detect_founder(lead)
            whatsapp_result = whatsapp_detect.detect_whatsapp(lead)
            score_result = scoring.score_lead(lead, analysis)
        except Exception as exc:  # noqa: BLE001 -- one bad lead shouldn't stop the batch
            results.append({"lead": lead.get("business_name"), "ok": False, "error": str(exc)})
            log_error("analysis", exc, lead_id=lead.get("id"), account_id=lead.get("account_id"))
            continue

        update_fields = {
            "company_size": analysis.get("company_size"),
            "revenue_tier": analysis.get("revenue_tier"),
            "industry": analysis.get("industry"),
            "ads_running": analysis.get("ads_running"),
            "social_platforms": analysis.get("social_platforms") or [],
            "weak_points": analysis.get("weak_points") or [],
            "ai_opportunities": analysis.get("ai_opportunities") or [],
            "founder_found": founder_result.get("founder_found", False),
            "founder_name": founder_result.get("founder_name"),
            "founder_source_phrase": founder_result.get("founder_source_phrase"),
            "whatsapp_found": whatsapp_result.get("whatsapp_found", False),
            "whatsapp_number": whatsapp_result.get("whatsapp_number"),
            "score": score_result.get("score"),
            "temperature": score_result.get("temperature"),
            "score_reasoning": score_result.get("score_reasoning"),
            "status": "analyzed",
        }
        repo.update_lead(lead["id"], update_fields)

        # Best-effort, same channel-isolation guarantee as every other
        # per-lead step here: an email-lookup problem for THIS lead must not
        # lose the analysis/scoring work already committed above for it,
        # or stop the rest of the batch.
        try:
            _maybe_find_email(tenant_id, lead, founder_result.get("founder_name"))
        except Exception as exc:  # noqa: BLE001 -- e.g. HunterLookupFailed (bad key, no credits)
            log_error("email_lookup", exc, lead_id=lead.get("id"), account_id=lead.get("account_id"))

        repo.insert_client_history(tenant_id, {
            "lead_id": lead["id"],
            "business_name": lead.get("business_name"),
            "platform": lead.get("platform"),
            "industry": analysis.get("industry"),
            "score": score_result.get("score"),
            "temperature": score_result.get("temperature"),
            "weak_points": analysis.get("weak_points") or [],
            "founder_found": founder_result.get("founder_found", False),
            "founder_name": founder_result.get("founder_name"),
            "contacted": False,
            "snapshot": {**lead, **update_fields},
        })

        # Fires immediately per-lead, not batched at the end of the cycle --
        # the spec is explicit that a hot lead alert can't wait for the run
        # to finish (see whatsapp_notify.py's module docstring).
        enriched_lead = {**lead, **update_fields}
        if score_result.get("score", 0) >= 8:
            whatsapp_notify.notify_hot_lead(enriched_lead)
        if founder_result.get("founder_found"):
            whatsapp_notify.notify_founder_found(enriched_lead)
        if whatsapp_result.get("whatsapp_found"):
            whatsapp_notify.notify_number_found(enriched_lead)

        results.append({"lead": lead.get("business_name"), "ok": True, **update_fields})

    return results


def run_message_generation_cycle(limit: int | None = None) -> list[dict]:
    """
    Generate outreach messages for every lead currently sitting at status
    "analyzed", across every tenant that currently has active Outreach
    accounts (Phase 5, ported multi-tenant 2026-08-20): one message on the
    lead's discovery platform, plus an additional WhatsApp message if Phase
    4 found a public WhatsApp number -- WhatsApp is always an ADDITIONAL
    channel, never a replacement for the primary one (see
    whatsapp_detect.py's module docstring).

    The active style (direct/discovery) is read once PER TENANT per cycle,
    not once per lead -- style.get_active_style() only rotates on elapsed
    duration, so every lead in the same tenant's batch gets the same style
    (each tenant has its own settings row and therefore its own style/
    rotation clock).

    `limit` caps how many leads are processed PER TENANT in one call, same
    reasoning as run_analysis_cycle's `limit`.

    Tenant-level isolation: one tenant raising here is logged and skipped,
    same reasoning as run_cycle() above.
    """
    results = []
    for tenant_id in repo.list_active_tenant_ids():
        with repo.tenant_scope(tenant_id):
            if repo.is_tenant_paused():
                continue
            run = repo.start_stage_run(tenant_id, "message_generation")
            try:
                tenant_results = _run_message_generation_cycle_for_tenant(limit)
                results.extend(tenant_results)
                ok_count = sum(1 for r in tenant_results if r.get("ok"))
                repo.finish_run(
                    tenant_id, run["id"], leads_found=len(tenant_results), messages_sent=0,
                    status="completed", finished_at_iso=dt.datetime.now(dt.timezone.utc).isoformat(),
                    notes=f"{ok_count}/{len(tenant_results)} leads got a message generated." if tenant_results else "No leads awaiting a message.",
                )
            except Exception as exc:  # noqa: BLE001 -- one bad tenant must not stop the others
                repo.finish_run(
                    tenant_id, run["id"], leads_found=0, messages_sent=0,
                    status="error", finished_at_iso=dt.datetime.now(dt.timezone.utc).isoformat(),
                    notes=str(exc),
                )
                try:
                    repo.insert_error({
                        "stage": "message_generation", "error_message": str(exc), "is_expected": False,
                    }, tenant_id=tenant_id)
                except Exception:  # noqa: BLE001 -- logging itself must never crash the pipeline
                    pass
    return results


def _run_message_generation_cycle_for_tenant(limit: int | None) -> list[dict]:
    leads = repo.leads_by_status("analyzed")
    if limit is not None:
        leads = leads[:limit]

    active_style = message_style.get_active_style()
    # Read once per tenant per cycle (settings don't change mid-cycle), same
    # reasoning as active_style above -- OutreachSettings.approvalRequired,
    # dashboard-editable (Settings > Contact rules > "Require approval before
    # sending"). Defaults to True (the safe default) if settings can't be
    # read at all, same fallback posture the rest of this module already
    # uses for approval_reminder_hours.
    settings = repo.get_settings() or {}
    approval_required = settings.get("approval_required")
    if approval_required is None:
        approval_required = True
    results = []

    for lead in leads:
        channels = [lead.get("platform")]
        if lead.get("whatsapp_found"):
            channels.append("whatsapp")

        try:
            primary_body = None
            for channel in channels:
                body = message_generate.generate_message(lead, channel, active_style)
                if channel == lead.get("platform"):
                    primary_body = body
                message = repo.insert_message({
                    "lead_id": lead["id"],
                    "channel": channel,
                    "body": body,
                })
                if not approval_required:
                    # Tenant has explicitly turned off the human approval
                    # gate (Settings) -- mark this message approved so it's
                    # eligible for sending, same approval_status a human's
                    # Approve click sets. approved_by is left null (NOT set
                    # to a placeholder string) since OutreachMessage.approvedById
                    # is a real foreign key to User.id -- writing anything
                    # other than a real user id there would violate the FK
                    # constraint outright. The "this was auto-approved, not
                    # a person" fact is what the pipeline-history row below
                    # (changed_by="auto-approved", a plain string field, not
                    # an FK) actually records for the audit trail; a null
                    # approved_by combined with that history entry is enough
                    # to distinguish this from a human approval later if
                    # ever needed. NOT calling approve_message() itself here
                    # -- its _maybe_advance_lead() only fires when the lead
                    # is ALREADY "awaiting_approval", which it isn't yet at
                    # this point in the loop (still "analyzed"), so that
                    # call would silently no-op; the lead's own status
                    # transition is handled explicitly below instead, once,
                    # after every channel's message is in.
                    repo.update_message(message["id"], {
                        "approval_status": "approved",
                        "approved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    })

            new_lead_status = "approved" if not approval_required else "awaiting_approval"
            repo.update_lead(lead["id"], {
                "generated_message": primary_body,
                "message_style_used": active_style,
                "status": new_lead_status,
            })
            if not approval_required:
                repo.record_stage_change(lead["id"], "analyzed", "approved", changed_by="auto-approved")
            results.append({
                "lead": lead.get("business_name"), "ok": True,
                "channels": channels, "style": active_style,
            })
        except Exception as exc:  # noqa: BLE001 -- one bad lead shouldn't stop the batch
            results.append({"lead": lead.get("business_name"), "ok": False, "error": str(exc)})
            log_error("message_generation", exc, lead_id=lead.get("id"), account_id=lead.get("account_id"))

    return results


def run_sending_cycle(limit: int | None = None) -> list[dict]:
    """
    Route every approved, not-yet-sent message to its channel (Phase 7).

    Instagram auto-sends through instagram_send.send_cold_message() -- NOT
    YET LIVE-VERIFIED (see that module's docstring). This replaced the
    original instagram_queue.queue_for_manual_send() manual-only design; the
    platform owner explicitly accepted the higher cold-DM-automation ban
    risk in exchange for the account never being touched from a location
    other than the agent's own consistent proxy. instagram_queue.py is kept
    for its mark_sent() bookkeeping shape reference only and is no longer
    called from this cycle.

    WhatsApp auto-sends through whatsapp_send.send_message() (Twilio's REST
    API) -- if agent/.env's WHATSAPP_* fields are still empty, that call
    raises WhatsAppNotConfigured, which the try/except below turns into a
    normal "ok": False result rather than crashing the cycle.

    LinkedIn auto-sends through linkedin_send.send_message() -- verified
    live 2026-08-03 against real company pages (see that module's
    docstring). Not every lead's page has LinkedIn's Page-messaging feature
    enabled, though; that raises NoMessageButtonAvailable, which the
    try/except below turns into the same normal "ok": False result as a
    missing WhatsApp number does for that channel, rather than crashing the
    cycle.

    `limit` caps how many messages are processed PER TENANT in one call,
    same reasoning as the analysis/message-generation cycles' `limit`.

    Ported multi-tenant 2026-08-20: loops over every tenant with active
    Outreach accounts, same one-more-level tenant isolation as the other
    cycle functions above.
    """
    results = []
    for tenant_id in repo.list_active_tenant_ids():
        with repo.tenant_scope(tenant_id):
            if repo.is_tenant_paused():
                continue
            run = repo.start_stage_run(tenant_id, "sending")
            try:
                tenant_results = _run_sending_cycle_for_tenant(limit, tenant_id=tenant_id)
                results.extend(tenant_results)
                sent_count = sum(1 for r in tenant_results if r.get("ok"))
                repo.finish_run(
                    tenant_id, run["id"], leads_found=0, messages_sent=sent_count,
                    status="completed", finished_at_iso=dt.datetime.now(dt.timezone.utc).isoformat(),
                    notes=f"{sent_count}/{len(tenant_results)} messages sent." if tenant_results else "No approved messages pending.",
                )
            except Exception as exc:  # noqa: BLE001 -- one bad tenant must not stop the others
                repo.finish_run(
                    tenant_id, run["id"], leads_found=0, messages_sent=0,
                    status="error", finished_at_iso=dt.datetime.now(dt.timezone.utc).isoformat(),
                    notes=str(exc),
                )
                try:
                    repo.insert_error({
                        "stage": "sending", "error_message": str(exc), "is_expected": False,
                    }, tenant_id=tenant_id)
                except Exception:  # noqa: BLE001 -- logging itself must never crash the pipeline
                    pass
    return results


def _run_sending_cycle_for_tenant(limit: int | None, account_id: str | None = None, tenant_id: str | None = None) -> list[dict]:
    # Reply-tagged messages (is_reply=True, from "Reply Here") are
    # deliberately excluded here -- run_reply_send_cycle() below picks them
    # up on its own fast ~2-3 min poll instead of waiting for this cycle's
    # normal once-daily cadence, so a reply feels close to real-time. This
    # cycle only ever sees fresh cold-outreach messages.
    messages = [m for m in repo.messages_approved_pending() if not m.get("is_reply")]

    # LIVE-REASONED 2026-09-08: account_id narrows this to ONE account's own
    # messages, so build_daily_schedule() can run each account's cold sends
    # at that account's own configured run_time instead of every tenant's
    # entire message queue firing at one shared clock time (see this
    # function's call sites for the real incident this fixes -- every
    # client's LinkedIn/Instagram sends going out at the exact same minute,
    # every day, forever, which is a stronger automation fingerprint than
    # any single account's own send pattern). messages_approved_pending()
    # doesn't expose account_id directly (it joins leads only for the
    # do_not_contact check, see that function's own docstring), so this
    # resolves it per-message via the lead -- one extra repo.get_lead() per
    # candidate message, on an already-small per-account queue (today's
    # daily limits are 5-15), not a real cost.
    if account_id is not None:
        messages = [m for m in messages if (repo.get_lead(m["lead_id"]) or {}).get("account_id") == account_id]

    # REAL BUG FOUND AND FIXED 2026-09-15: this function had NO daily-limit
    # check at all -- every approved+pending message got sent, however many
    # there were. LIVE-CONFIRMED: one account sent 8 real LinkedIn messages
    # in a single day against its own configured linkedin_daily_limit of 5.
    # warmup.effective_limit() already exists and is used correctly on the
    # DISCOVERY side (caps how many new leads get found per day) but was
    # never applied here on the SENDING side -- the two are separate caps
    # on separate activities and both need enforcing independently. Counts
    # today's real sends per account (cold_sends_today_for_account(),
    # excluding replies -- same reasoning as the is_reply filter just
    # above) and trims the batch to whatever's left of that account's daily
    # allowance. Only meaningful when account_id is known (the daily limit
    # is a property of ONE account, not a whole tenant) -- the
    # tenant-wide call site (run_sending_cycle(), account_id=None) is a
    # legacy/manual path superseded by the per-account scheduling
    # (build_daily_schedule() -- see run_account_sending_cycle's own
    # docstring on why sending moved to per-account jobs), left as-is here.
    if account_id is not None and tenant_id is not None:
        account = repo.get_account(account_id, tenant_id)

        # Second half of the 2026-09-16 paused-account fix (the primary guard
        # is in run_account_sending_cycle() above, which is what the real
        # scheduled jobs call). Repeated here because this function is also
        # reachable from manual/legacy call sites, and the account row is
        # already in hand -- a DB pause must be authoritative on every path
        # into sending, not just the scheduled one. Missing account is
        # treated as not-sendable for the same reason.
        if not account or account.get("status") != "active":
            return []

        if account:
            platform = account.get("platform")
            # sendDailyLimitOverride (2026-09-15): lets Hussein raise just the
            # SENDING cap for an account -- to clear an approved backlog
            # faster -- without also raising warmup.effective_limit(), which
            # would silently speed up DISCOVERY too (a different, separately
            # risky activity that shares nothing with this override). NULL
            # for every account except the handful Hussein explicitly set
            # this on, so this is a no-op everywhere else.
            override = account.get("send_daily_limit_override")
            daily_limit = override if override is not None else warmup.effective_limit(account, platform)
            day_start = pool.today_start_iso(tenant_id)
            already_sent = repo.cold_sends_today_for_account(account_id, day_start, tenant_id=tenant_id)
            remaining = max(0, daily_limit - already_sent)
            messages = messages[:remaining]

    if limit is not None:
        messages = messages[:limit]

    results = []
    for index, message in enumerate(messages):
        # Space COLD sends out instead of firing an account's whole daily
        # allowance in one burst -- see _sleep_between_sends(). Applied here
        # and deliberately NOT in run_reply_send_cycle(): a reply to someone
        # who just messaged you is expected to arrive promptly, and delaying
        # it by up to 25 minutes would make the product feel broken while
        # protecting nothing (replying inside an existing conversation isn't
        # the pattern platforms flag -- unsolicited first contact is).
        #
        # Placed BEFORE each send except the first, so the cycle starts work
        # immediately at its scheduled run_time and no gap is wasted after
        # the final message.
        if index > 0:
            _sleep_between_sends()
        channel = message.get("channel")
        try:
            if channel == "instagram":
                # DELIBERATE PRODUCT DECISION: Instagram cold sends used to
                # queue for manual send (instagram_queue.queue_for_manual_send)
                # specifically because automating unsolicited first-contact
                # DMs is Instagram's highest-risk automation pattern -- see
                # instagram_send.py's module docstring for why that tradeoff
                # was explicitly accepted anyway (keeping every send on the
                # agent's own consistent proxy/location, never the account
                # owner's real device).
                instagram_send.send_cold_message(message)
                results.append({
                    "message_id": message["id"], "channel": channel,
                    "ok": True, "action": "sent",
                })
            elif channel == "whatsapp":
                whatsapp_send.send_message(message)
                results.append({
                    "message_id": message["id"], "channel": channel,
                    "ok": True, "action": "sent",
                })
            elif channel == "linkedin":
                linkedin_send.send_message(message)
                results.append({
                    "message_id": message["id"], "channel": channel,
                    "ok": True, "action": "sent",
                })
            else:
                # Unreachable in practice, not a missing feature: repo.messages_approved_pending()
                # only ever queries channel IN ('linkedin', 'instagram', 'whatsapp') -- email is
                # deliberately excluded there since the Next.js app's own SES path (src/lib/
                # outreach/ses.ts) owns sending it entirely, this agent never touches email
                # messages at all. Kept as a defensive branch (a future channel value slipping
                # through here should be a visible "ok": False, not a silent KeyError) rather
                # than an assert, since one malformed message shouldn't crash the whole cycle.
                results.append({
                    "message_id": message["id"], "channel": channel, "ok": False,
                    "reason": f"unrecognized channel {channel!r} -- expected linkedin/instagram/whatsapp",
                })
        except Exception as exc:  # noqa: BLE001 -- one bad message shouldn't stop the rest
            results.append({"message_id": message["id"], "channel": channel, "ok": False, "error": str(exc)})
            log_error("sending", exc, channel=channel, lead_id=message.get("lead_id"))

    return results


def run_reply_send_cycle() -> list[dict]:
    """
    Delivers tenant-written replies (is_reply=True, from the "Reply Here"
    dashboard page -- src/lib/actions/outreach-replies.ts's
    sendReplyAction()) into each lead's EXISTING conversation thread, on a
    fast poll separate from run_sending_cycle()'s once-daily cadence (see
    build_daily_schedule()'s IntervalTrigger job for this function) so a
    reply feels close to real-time instead of waiting for the next full
    cycle.

    Deliberately calls each channel's send_reply() (thread-reply delivery),
    NOT send_message()/send_cold_message() (fresh connection request / new
    thread) -- a reply must land in the conversation the lead already
    started, not open a new one. Same per-tenant, per-message error
    isolation as _run_sending_cycle_for_tenant() above; email is excluded
    the same way (repo.replies_pending() only ever queries channel IN
    ('linkedin', 'instagram', 'whatsapp'), matching
    messages_approved_pending()'s own scoping).
    """
    results = []
    for tenant_id in repo.list_active_tenant_ids():
        try:
            with repo.tenant_scope(tenant_id):
                results.extend(_run_reply_send_cycle_for_tenant())
        except Exception as exc:  # noqa: BLE001 -- one bad tenant must not stop the others
            try:
                repo.insert_error({
                    "stage": "reply_sending", "error_message": str(exc), "is_expected": False,
                }, tenant_id=tenant_id)
            except Exception:  # noqa: BLE001 -- logging itself must never crash the pipeline
                pass
    return results


def run_reply_detection_poll() -> dict:
    """
    Real gap fixed 2026-09-07: check_linkedin_replies()/check_instagram_
    replies()/check_whatsapp_replies() only ever ran once daily, inside
    run_full_pipeline_cycle() -- live-confirmed the same night, a reply
    genuinely sent on Instagram sat completely undetected for hours with
    nothing to notice it, since nothing re-checked until the next scheduled
    downstream-pipeline run. This is the fast-poll counterpart, same
    reasoning and shape as run_reply_send_cycle() above (which already
    solved the identical problem for DELIVERING a tenant-written reply,
    not detecting an incoming one) -- see build_daily_schedule()'s
    IntervalTrigger job for this function's real cadence.

    Deliberately its own function, not folded into run_reply_send_cycle():
    that function's per-tenant loop already calls repo.replies_pending()
    (SENDING direction) -- mixing SENDING and DETECTING into one loop body
    would make one slow/failing channel's detection block another
    channel's send on the same tick, for no real benefit since they don't
    share any state.
    """
    results: dict[str, dict] = {}
    for tenant_id in repo.list_active_tenant_ids():
        with repo.tenant_scope(tenant_id):
            tenant_result: dict = {}
            try:
                tenant_result["whatsapp"] = whatsapp_reply_check.check_whatsapp_replies()
            except Exception as exc:  # noqa: BLE001 -- e.g. WhatsAppNotConfigured; don't lose the other channels
                tenant_result["whatsapp"] = {"ok": False, "error": str(exc)}
                log_error("reply_check", exc, channel="whatsapp")
            try:
                tenant_result["linkedin"] = linkedin_reply_check.check_linkedin_replies()
            except Exception as exc:  # noqa: BLE001 -- e.g. unverified selector mismatch; don't lose the other channels
                tenant_result["linkedin"] = {"ok": False, "error": str(exc)}
                log_error("reply_check", exc, channel="linkedin")
            try:
                tenant_result["instagram"] = instagram_reply_check.check_instagram_replies()
            except Exception as exc:  # noqa: BLE001 -- e.g. unverified selector mismatch; don't lose the other channels
                tenant_result["instagram"] = {"ok": False, "error": str(exc)}
                log_error("reply_check", exc, channel="instagram")
            results[tenant_id] = tenant_result
    return results


def _run_reply_send_cycle_for_tenant() -> list[dict]:
    messages = repo.replies_pending()

    results = []
    for message in messages:
        channel = message.get("channel")
        try:
            if channel == "instagram":
                instagram_send.send_reply(message)
            elif channel == "whatsapp":
                whatsapp_send.send_message(message)
            elif channel == "linkedin":
                linkedin_send.send_reply(message)
            else:
                results.append({
                    "message_id": message["id"], "channel": channel, "ok": False,
                    "reason": f"unrecognized channel {channel!r} -- expected linkedin/instagram/whatsapp",
                })
                continue
            results.append({"message_id": message["id"], "channel": channel, "ok": True, "action": "sent"})
        except Exception as exc:  # noqa: BLE001 -- one bad reply shouldn't stop the rest
            results.append({"message_id": message["id"], "channel": channel, "ok": False, "error": str(exc)})
            log_error("reply_sending", exc, channel=channel, lead_id=message.get("lead_id"))

    return results


_LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"
_INSTAGRAM_HOME_URL = "https://www.instagram.com/"


def run_account_health_check_cycle() -> list[dict]:
    """
    Real gap fixed 2026-09-06: linkedin_send.py's/instagram_send.py's own
    _raise_if_logged_out() only ever runs AT THE MOMENT of a real send --
    live-confirmed the same night, an account can sit genuinely logged out
    (dashboard still showing "Connected") for hours with nothing to notice,
    simply because nothing happened to try sending through it in that
    window. This is the periodic counterpart: visits each active,
    currently-"connected" LinkedIn/Instagram account's own feed/home page
    (no send, no lead involved) on a schedule (see
    build_daily_schedule()'s IntervalTrigger job for this function) purely
    to catch a session going bad BETWEEN sends, not just during one.

    Deliberately reuses linkedin_send._raise_if_logged_out() /
    instagram_send._raise_if_logged_out() rather than a third copy of the
    same detection logic -- same reasoning that made those into shared
    per-module helpers in the first place. Only visits accounts already
    "connected" (not_connected/failed/connecting accounts have nothing
    useful to re-check here), and only linkedin/instagram (email has no
    concept of a browser session to go stale; whatsapp's health is a
    separate, already-existing concern).
    """
    results = []
    for tenant_id in repo.list_active_tenant_ids():
        try:
            with repo.tenant_scope(tenant_id):
                for account in pool.load_accounts(tenant_id):
                    if account.get("platform") not in ("linkedin", "instagram"):
                        continue
                    if account.get("login_status") != "connected":
                        continue
                    try:
                        with SessionManager() as sessions:
                            context, page, new_verified_ip = sessions.open(account)
                            if new_verified_ip and not account.get("verified_proxy_ip"):
                                repo.update_account(account["id"], {"verified_proxy_ip": new_verified_ip})
                            try:
                                if account["platform"] == "linkedin":
                                    page.goto(_LINKEDIN_FEED_URL, timeout=30_000, wait_until="domcontentloaded")
                                    linkedin_send._raise_if_logged_out(page, account)
                                else:
                                    page.goto(_INSTAGRAM_HOME_URL, timeout=30_000, wait_until="domcontentloaded")
                                    instagram_send._raise_if_logged_out(page, account)
                            finally:
                                sessions.close(account["id"], context)
                        results.append({"account_id": account["id"], "platform": account["platform"], "ok": True})
                    except (linkedin_send.SessionLoggedOut, instagram_send.SessionLoggedOut) as exc:
                        # Already persisted login_status: "failed" by
                        # _raise_if_logged_out itself -- nothing more to do
                        # here than record the outcome.
                        results.append({"account_id": account["id"], "platform": account["platform"], "ok": False, "reason": str(exc)})
                    except Exception as exc:  # noqa: BLE001 -- a network hiccup here shouldn't be mistaken for a real logout
                        results.append({"account_id": account["id"], "platform": account["platform"], "ok": False, "error": str(exc)})
                        log_error("account_health_check", exc, account_id=account["id"])
        except Exception as exc:  # noqa: BLE001 -- one bad tenant must not stop the others
            try:
                repo.insert_error({
                    "stage": "account_health_check", "error_message": str(exc), "is_expected": False,
                }, tenant_id=tenant_id)
            except Exception:  # noqa: BLE001 -- logging itself must never crash the pipeline
                pass
    return results


def run_approval_reminder_check() -> dict:
    """
    Phase 8's approval reminder trigger, across every tenant that currently
    has active Outreach accounts (ported multi-tenant 2026-08-20): if any
    tenant has a message that's sat "awaiting" longer than that tenant's
    settings.approval_reminder_hours, notify once with the count. Returns a
    dict keyed by tenant_id -> the logged notification (or None if nothing
    was overdue for that tenant).

    Tenant-level isolation: one tenant raising here is logged and skipped,
    same reasoning as run_cycle() above.
    """
    results: dict[str, dict | None] = {}
    for tenant_id in repo.list_active_tenant_ids():
        try:
            with repo.tenant_scope(tenant_id):
                pending = approval.messages_needing_reminder()
                results[tenant_id] = (
                    whatsapp_notify.notify_approval_reminder(len(pending)) if pending else None
                )
        except Exception as exc:  # noqa: BLE001 -- one bad tenant must not stop the others
            try:
                repo.insert_error({
                    "stage": "approval_reminder", "error_message": str(exc), "is_expected": False,
                }, tenant_id=tenant_id)
            except Exception:  # noqa: BLE001 -- logging itself must never crash the pipeline
                pass
            results[tenant_id] = None
    return results


def run_account_sending_cycle(tenant_id: str, account_id: str, limit: int | None = None) -> list[dict]:
    """
    Cold-outreach sending for ONE account, scoped to messages whose lead
    belongs to it -- the per-account counterpart to run_sending_cycle()
    below, meant to be scheduled at THIS account's own run_time rather than
    one shared clock time for every tenant.

    Real incident this fixes, live-reasoned 2026-09-08: build_daily_schedule
    originally called run_sending_cycle() (no account scoping) from ONE
    fixed daily job (_DOWNSTREAM_HOUR/_DOWNSTREAM_MINUTE, 20:00 for every
    tenant) -- so every client's entire day of LinkedIn/Instagram cold
    sends went out at the exact same clock minute, every single day,
    indefinitely. That's a stronger, more mechanical automation fingerprint
    than any single account's own send pattern: the whole point of staggering
    each account's run_time earlier in this session (see AccountHealthClient
    settings) was undone downstream by sending still happening on one shared
    schedule. This function is what build_daily_schedule() now schedules
    once per account instead, right after that account's own discovery run,
    so a given account's cold sends happen in its own morning window and
    never at the same minute another tenant's account sends.

    Still respects the tenant-level pause: a paused tenant's per-account
    sending jobs are no-ops, same as run_sending_cycle()'s own check -- and,
    since 2026-09-16, the per-ACCOUNT status too (see the guard below).
    """
    with repo.tenant_scope(tenant_id):
        if repo.is_tenant_paused():
            return []

        # REAL INCIDENT 2026-09-16: LinkedIn served a security checkpoint on
        # Zimmar's account ("unusual activity / high volume of profile data
        # access") and both that account and its Instagram account were set
        # to status='paused' in the DB. That pause was NOT authoritative for
        # sending: account status was only ever consulted at schedule-BUILD
        # time (build_daily_schedule() below, where the status == "active"
        # filter lives), so jobs registered at the previous boot -- Zimmar's
        # 08:41 and 10:05 Beirut sends -- were still holding a live closure
        # and would have kept sending on a checkpointed account until the
        # next redeploy rebuilt the schedule. Pausing an account has to take
        # effect on the very next run, not at the next process restart.
        #
        # So the status is re-read here, at RUN time, before any browser work
        # or stage_run row -- mirroring how the discovery side has always
        # done it (pool.get_due_accounts(), which skips any account whose
        # status != "active"; core rule R9: a paused account never runs
        # itself back in, resuming is Hussein's manual call).
        #
        # "warned" is blocked alongside "paused" to match the Next.js/email
        # side, which gates on `status === "paused" || status === "warned"`
        # (sendIfEmailChannel in src/lib/actions/outreach-approvals.ts) -- an
        # account auto-warned for e.g. bounce rate must not keep sending on
        # LinkedIn/Instagram just because the block was written for email.
        account = repo.get_account(account_id, tenant_id)
        if not account or account.get("status") != "active":
            return []

        run = repo.start_stage_run(tenant_id, "sending")
        try:
            results = _run_sending_cycle_for_tenant(limit, account_id=account_id, tenant_id=tenant_id)
            sent_count = sum(1 for r in results if r.get("ok"))
            repo.finish_run(
                tenant_id, run["id"], leads_found=0, messages_sent=sent_count,
                status="completed", finished_at_iso=dt.datetime.now(dt.timezone.utc).isoformat(),
                notes=f"{sent_count}/{len(results)} messages sent." if results else "No approved messages pending.",
            )
            return results
        except Exception as exc:  # noqa: BLE001 -- one bad account must not stop this tenant's other accounts
            repo.finish_run(
                tenant_id, run["id"], leads_found=0, messages_sent=0,
                status="error", finished_at_iso=dt.datetime.now(dt.timezone.utc).isoformat(),
                notes=str(exc),
            )
            try:
                repo.insert_error({
                    "stage": "sending", "error_message": str(exc), "is_expected": False, "account_id": account_id,
                }, tenant_id=tenant_id)
            except Exception:  # noqa: BLE001 -- logging itself must never crash the pipeline
                pass
            return []


def run_full_pipeline_cycle() -> dict:
    """
    Runs the shared (not per-account) downstream steps once daily, in spec
    order: analysis -> message generation -> approval-reminder check -> due
    follow-up dispatch. This is what build_daily_schedule() schedules once
    daily (see its docstring for why this isn't per-account, unlike
    discovery and, as of 2026-09-08, sending).

    Cold-outreach SENDING moved OUT of this cycle 2026-09-08 onto
    run_account_sending_cycle(), scheduled per-account at that account's
    own run_time -- see that function's docstring for the real incident
    this fixes (every tenant's sends firing at one shared clock time).
    Analysis and message-generation stay here: neither ever touches
    LinkedIn/Instagram/Instagram directly (they read/score leads and call
    Claude to draft text), so they carry none of the automation-fingerprint
    risk that motivated splitting sending out -- there's no safety reason to
    scatter them across every account's own schedule too, only added
    complexity.

    Reply checking (WhatsApp/LinkedIn/Instagram) moved OUT of this cycle
    2026-09-07 onto its own fast poll -- run_reply_detection_poll(), see
    that function's own docstring for the real gap this fixes (a reply
    sitting undetected for up to 24h waiting on this once-daily cycle).
    Follow-up dispatch staying here, on the daily cadence, is still safe
    despite that split: the fast reply-detection poll runs far more often
    than this daily dispatch, so by the time dispatch_due_followups() runs,
    any reply from today has already had many chances to be detected and
    cancel its own follow-up (reply_detection.handle_reply_detected ->
    followup.cancel_pending) well before this step would otherwise
    generate one for someone who already responded.

    Ported multi-tenant 2026-08-20: run_analysis_cycle/run_message_generation
    _cycle/run_approval_reminder_check each already loop over every active
    tenant internally (see their own docstrings) -- called plainly here,
    same as before the port. The follow-up dispatch step below does NOT loop
    internally (crm/followup.py is out of scope for this port -- it only
    calls repo.*, never the DB directly), so this function wraps it in its
    own per-tenant tenant_scope(...) loop instead.
    """
    # config.MAX_LEADS_PER_CYCLE caps per-tenant Claude-call volume per run --
    # see its own comment in config.py for why this must not be left
    # uncapped (a real production safety valve, not a dev-only brake).
    analysis = run_analysis_cycle(limit=config.MAX_LEADS_PER_CYCLE)
    messages = run_message_generation_cycle(limit=config.MAX_LEADS_PER_CYCLE)
    reminder = run_approval_reminder_check()

    followups_dispatched: dict[str, list] = {}

    for tenant_id in repo.list_active_tenant_ids():
        with repo.tenant_scope(tenant_id):
            try:
                followups_dispatched[tenant_id] = followup.dispatch_due_followups()
            except Exception as exc:  # noqa: BLE001 -- don't lose the steps above over one bad batch
                followups_dispatched[tenant_id] = {"ok": False, "error": str(exc)}
                log_error("followup", exc)

    return {
        "analysis": analysis,
        "messages": messages,
        "approval_reminder": reminder,
        "followups_dispatched": followups_dispatched,
    }


# Fixed daily time for run_full_pipeline_cycle (analysis -> message
# generation -> reminders -> follow-up dispatch).
#
# Moved to 00:30 on 2026-09-16, when discovery moved to the 20:00-24:00
# night window: this cycle GENERATES the messages for leads discovery just
# found, so it has to run after that window closes, not before it opens.
# At the old 20:00 it would have raced the very discovery run it depends
# on, and every newly-found lead's message would have waited a full extra
# day before it even existed to be approved.
#
# 00:30 also puts generated messages in the approval queue overnight, so
# they are reviewable before the 08:00-12:00 sending window the same
# morning -- the shortest honest path from "lead discovered" to "message
# sent" that still keeps a human approval step in the middle.
_DOWNSTREAM_HOUR = 0
_DOWNSTREAM_MINUTE = 30

# How often run_reply_send_cycle() polls for tenant-written replies waiting
# to go out -- see build_daily_schedule()'s IntervalTrigger job. Short
# enough that a reply feels close to real-time, long enough not to hammer
# LinkedIn/Instagram with constant inbox-open requests across every tenant
# with a reply-less-empty queue.
_REPLY_POLL_INTERVAL_MINUTES = 3

# How often run_reply_detection_poll() re-checks every "contacted"/"replied"
# lead's real inbox for a new incoming reply -- same real-time-feel
# reasoning as _REPLY_POLL_INTERVAL_MINUTES above (this is the DETECTING
# counterpart to that SENDING poll), same interval so a reply and its
# eventual delivery both surface on a similarly fast cadence, live-fixed
# 2026-09-07 (see run_reply_detection_poll()'s own docstring for the real
# gap this closes -- a reply sitting undetected for up to 24h waiting on
# the old once-daily check).
_REPLY_DETECTION_POLL_INTERVAL_MINUTES = 3

# How often run_account_health_check_cycle() re-visits each connected
# LinkedIn/Instagram account -- hours, not minutes, deliberately: this is
# purely a "is the dashboard's status still true" check with no real work
# behind it, so it doesn't need reply-poll urgency, and a real browser
# visit per connected account per tenant adds up in request volume that's
# worth keeping infrequent for accounts that are, in the overwhelming
# majority of checks, going to come back genuinely fine.
_ACCOUNT_HEALTH_CHECK_INTERVAL_HOURS = 4

# Randomized gap between two consecutive COLD sends in one sending cycle.
#
# Without this, _run_sending_cycle_for_tenant() sent an account's entire
# daily allowance back to back -- 10 cold LinkedIn messages inside a few
# minutes at 08:00, then nothing for 24h. That burst shape is one of the
# clearest automation signals both platforms watch for; a real person
# messaging 10 strangers spreads it across the morning. pacing.human_delay()
# already covers the seconds-scale rhythm WITHIN one send (typing, clicking)
# -- this is the minutes-scale rhythm BETWEEN sends, which nothing covered.
#
# Randomized rather than a fixed gap on purpose: a message every exactly-15
# minutes is its own detectable fingerprint. With warm-up caps currently at
# 5-10 messages/day this spreads a real run across roughly 1-3 hours, which
# is why a long-running job is acceptable here -- see _sleep_between_sends().
_SEND_GAP_MIN_SECONDS = 8 * 60
_SEND_GAP_MAX_SECONDS = 25 * 60

# Two non-overlapping daily windows, Beirut time, owner's design
# (2026-09-16): ALL sending happens 08:00-12:00, ALL discovery happens
# 20:00-24:00. Accounts are spread evenly across their window rather than
# all firing at one clock time.
#
# Why two separated windows at all: discovery and sending both open a real
# browser session for the SAME account and contend for core/session.py's
# per-account lock. Every previous scheme tied sending to discovery's own
# run_time plus an offset (20 min, then 45), and each time a discovery run
# overran its offset the sending job spent _SESSION_LOCK_TIMEOUT_SECONDS
# (240s) waiting, gave up, and silently sent nothing -- which is exactly
# how a real approved message sat unsent for a full day on 2026-09-16.
# Putting the two activities in windows 8 hours apart removes the
# contention entirely rather than trying to predict how long discovery
# takes on a bad day.
#
# Discovery at night, sending in the morning (not the reverse) is
# deliberate: overnight discovery feeds the 20:00 downstream pipeline
# (analysis -> message generation), so a lead found at night has its
# message generated the same night and is ready for approval before the
# next morning's sending window.
_SENDING_WINDOW_START_HOUR = 8
_SENDING_WINDOW_END_HOUR = 12
_DISCOVERY_WINDOW_START_HOUR = 20
_DISCOVERY_WINDOW_END_HOUR = 24


def _spread_within_window(index: int, total: int, start_hour: int, end_hour: int) -> tuple[int, int]:
    """
    Place job `index` of `total` evenly inside [start_hour, end_hour), then
    jitter it -- so N accounts fill the window instead of stacking on one
    minute, and no account sits at the identical wall-clock minute forever
    (see _RUN_TIME_JITTER_MINUTES).

    Clamped to stay strictly inside the window: the jitter must never push
    a job past the boundary, or a "sending" job could drift into the
    discovery window and reintroduce exactly the session-lock contention
    these windows exist to prevent.
    """
    window_minutes = (end_hour - start_hour) * 60
    # Evenly spaced slots, offset by half a slot so the first job isn't at
    # the very edge of the window and the last isn't at the very end.
    slot = window_minutes // max(total, 1)
    base = start_hour * 60 + slot * index + slot // 2
    jittered = base + random.randint(-_RUN_TIME_JITTER_MINUTES, _RUN_TIME_JITTER_MINUTES)
    earliest = start_hour * 60
    latest = end_hour * 60 - 1
    return divmod(max(earliest, min(latest, jittered)), 60)

# Randomized jitter applied to every per-account discovery and sending job's
# scheduled time, re-drawn each time build_daily_schedule() runs.
#
# Without it every account fires at a fixed wall-clock minute (Zimmar 08:00
# discovery / 08:20 sending) every single day, indefinitely -- a real person
# does not start prospecting at exactly 08:00:00 daily for months. The
# per-account staggering added earlier removed the "every tenant at once"
# fingerprint; this removes the "same minute forever" one that remained.
#
# Applied at SCHEDULE-BUILD time, not per-run: APScheduler's CronTrigger
# fires on a fixed expression, so the jitter is baked into each job's cron
# minute when the schedule is constructed. The scheduler process restarting
# (deploy, reboot, crash-restart) re-draws it, which is the intended
# behaviour -- it re-randomizes without needing a moving trigger.
_RUN_TIME_JITTER_MINUTES = 15


# EXPLICIT OWNER REQUEST (2026-09-16): Insurance specifically must run at
# times ANCHORED near a fixed hour every day, not spread across the whole
# window the way every other tenant (Zimmar, mjivity1, future tenants) is
# via _spread_within_window() above. Zimmar is explicitly UNCHANGED by this
# -- only Insurance gets anchored times.
#
# FOLLOW-UP OWNER REQUEST (2026-09-17): the original version of this made
# Insurance's times perfectly FIXED (byte-identical every rebuild), which
# the owner then realized is itself a bot-detection fingerprint -- exactly
# the "same wall-clock minute forever" pattern documented on
# _RUN_TIME_JITTER_MINUTES above as what got Zimmar's LinkedIn account
# checkpointed. Insurance's times are now jittered by +/-10 minutes around
# these anchors, re-drawn on every build_daily_schedule() call same as
# every other tenant's jitter -- see _insurance_jittered_minutes() below.
# The anchors stay put; only the byte-identical-every-day property is gone.
#
# Keyed by business_name (settings.business_name), the same identifier
# messaging/generate.py already uses for Insurance's own fixed message
# templates (see that module's _INSURANCE_BUSINESS_NAME) -- reusing it here
# instead of inventing a second hardcoded tenant_id constant means both
# "this is Insurance" checks in the codebase stay in sync automatically if
# the tenant is ever renamed/recreated.
_INSURANCE_BUSINESS_NAME = "Partners Insurance Consultancy"

# Insurance discovery (LinkedIn AND Email) -- owner: "Insurance discovering
# at 11" (23:00 Beirut). Applied to BOTH of Insurance's discovery jobs for
# consistency, since the owner's request didn't distinguish by channel.
# Email is anchored 2 minutes after LinkedIn (23:02) so the two jobs don't
# fire at the exact same instant as each other -- Insurance's Email account
# has discovery-only wiring (no sending; see the "email" branch in
# _run_discovery_cycle_for_tenant), but it still gets its own cron job here.
# Each is jittered independently (see _INSURANCE_JITTER_MINUTES below), so
# the 2-minute gap is a starting offset between anchors, not a guarantee --
# the two jobs can end up a minute or so closer together on any given
# rebuild, which is fine (this file's own _spread_within_window() jobs
# already tolerate the same thing); they just won't land on the identical
# minute as a rule the way pure fixed times would.
#
# 23:00 was chosen specifically because it does NOT match any of Zimmar's
# own live-scheduled discovery times (verified against the actual running
# schedule on the droplet before picking this: Zimmar's discovery jobs sat
# at 20:35, 21:04, and 22:11 Beirut that night, spread+jittered inside the
# same 20:00-24:00 window) -- 23:00/23:02 sits well clear of all three, and
# with the new +/-10 min jitter the resulting 22:50-23:12 range still sits
# well clear of Zimmar's live discovery times re-verified 2026-09-17
# (20:39, 21:25, 21:59).
_INSURANCE_DISCOVERY_HOUR = 23
_INSURANCE_DISCOVERY_MINUTE = 0
_INSURANCE_DISCOVERY_MINUTE_EMAIL = 2

# Insurance sending (LinkedIn only) -- owner: anchored time BEFORE 11:00 AM
# Beirut. 10:00 was chosen as a reasonable anchor for the morning slot that
# clears Zimmar's own live-scheduled sending times with comfortable margin
# on both sides, while still leaving room before the 11:00 upper bound the
# owner specified even after +/-10 min jitter (9:50-10:10, re-verified
# against Zimmar's live sending times 2026-09-17: 8:37, 9:46).
_INSURANCE_SENDING_HOUR = 10
_INSURANCE_SENDING_MINUTE = 0

# FOLLOW-UP OWNER REQUEST (2026-09-17): +/-10 minutes for Insurance's three
# anchored times specifically. Deliberately a SEPARATE constant from
# _RUN_TIME_JITTER_MINUTES (=15) above rather than reusing it -- the owner
# asked for exactly 10 minutes here, tighter than the spread-window jitter
# every other tenant gets, so Insurance's times stay clustered near its
# chosen anchors instead of drifting as far as the general-purpose jitter
# would allow.
_INSURANCE_JITTER_MINUTES = 10


def _insurance_jittered_minutes(hour: int, minute: int) -> tuple[int, int]:
    """
    Apply independent +/-_INSURANCE_JITTER_MINUTES jitter to one of
    Insurance's anchored (hour, minute) times, re-drawn fresh each call --
    same "re-drawn every time build_daily_schedule() runs" behaviour as
    _spread_within_window() above, just centered on a fixed anchor instead
    of an evenly-spaced slot.

    Each of Insurance's 3 jobs (LinkedIn discovery, Email discovery,
    LinkedIn sending) calls this separately with its own anchor, so the
    three draws are independent -- e.g. the two discovery jobs' 2-minute
    anchor gap is not preserved exactly, only approximately (see
    _INSURANCE_DISCOVERY_MINUTE_EMAIL's own comment).

    No window-boundary clamping here unlike _spread_within_window(): the
    anchors themselves (23:00/23:02/10:00) already sit with enough margin
    from the 20:00-24:00 / 08:00-12:00 window edges that +/-10 minutes
    cannot push a job across a window boundary.
    """
    total_minutes = hour * 60 + minute + random.randint(-_INSURANCE_JITTER_MINUTES, _INSURANCE_JITTER_MINUTES)
    return divmod(total_minutes, 60)


def _is_insurance_tenant(tenant_id: str) -> bool:
    """
    True if `tenant_id` is Insurance's own tenant -- resolved by
    business_name (see _INSURANCE_BUSINESS_NAME's docstring above) rather
    than a second hardcoded tenant_id constant. get_settings() accepts an
    explicit tenant_id directly (see its own docstring), so no ambient
    tenant_scope() is needed here even though build_daily_schedule() itself
    iterates tenant_ids outside of one.
    """
    settings = repo.get_settings(tenant_id) or {}
    return (settings.get("business_name") or "").strip() == _INSURANCE_BUSINESS_NAME


# Randomized pause between two consecutive PROFILE visits during discovery.
#
# Real gap found 2026-09-08: the widening-search loop above already paused
# 15-30s between search queries (added after LinkedIn force-logged-out a
# real account over back-to-back searches -- see its own comment), but the
# per-profile loops that follow it visited every result's /about/ and
# /posts/ pages (LinkedIn) or post -> profile (Instagram) with no delay at
# all. Rapidly viewing many profiles in sequence is the classic scraping
# fingerprint on both platforms -- arguably watched more closely than
# messaging, since that IS what scrapers do. Same reasoning and shape as
# _sleep_between_sends() below, shorter because browsing several profiles
# in a few minutes is normal human behaviour, whereas sending several cold
# messages that fast is not.
_PROFILE_VISIT_GAP_MIN_SECONDS = 20
_PROFILE_VISIT_GAP_MAX_SECONDS = 75


def _sleep_between_profile_visits() -> float:
    """Block for a random gap between discovery profile visits; returns seconds waited."""
    gap = random.uniform(_PROFILE_VISIT_GAP_MIN_SECONDS, _PROFILE_VISIT_GAP_MAX_SECONDS)
    time.sleep(gap)
    return gap


def _sleep_between_sends() -> float:
    """
    Block for a random inter-send gap, returning the seconds actually
    waited (so callers/tests can assert on real pacing rather than guess).

    Deliberately a plain time.sleep on the scheduler's own worker thread:
    APScheduler runs each job in its own thread, so a cycle sitting idle
    here blocks only itself, not the fast reply-send/reply-detection polls
    that must stay responsive. Making this async or job-splitting instead
    would buy nothing while adding real complexity.
    """
    gap = random.uniform(_SEND_GAP_MIN_SECONDS, _SEND_GAP_MAX_SECONDS)
    time.sleep(gap)
    return gap


def build_daily_schedule() -> BackgroundScheduler:
    """
    Wire up the full daily pipeline for the always-on server (Phase 10),
    across every tenant that currently has active Outreach accounts: one
    discovery cron trigger per (tenant, account) pair at that account's own
    configured run_time, replacing run_cycle's Phase 2 placeholder role now
    that Phase 3's real discovery exists (run_cycle itself is untouched and
    still used for the manual test entry point below) -- plus one shared
    downstream-pipeline trigger (run_full_pipeline_cycle) that runs once
    daily and already loops over every active tenant internally.

    Ported multi-tenant 2026-08-20: no longer takes an `accounts` list --
    it discovers tenants and their accounts itself via
    repo.list_active_tenant_ids() + account_pool.load_accounts(tenant_id),
    since this is now a per-tenant, not a fixed, account set. Each job
    closure captures its own tenant_id so a later add_job for a different
    tenant can't shadow an earlier one's discovery run.

    The downstream steps are scheduled just once, not per account, because
    each of them already loops over every tenant AND processes every
    pending record within that tenant in a single pass (see
    run_analysis_cycle/run_message_generation_cycle/run_sending_cycle/
    run_approval_reminder_check's own docstrings) -- unlike discovery, they
    aren't scoped to "this one account's turn" to begin with.

    Returns the scheduler unstarted -- calling code (the always-on server
    process, from Phase 10) decides when to call .start() and keep the
    process alive.
    """
    # Found un-tuned in the 2026-09-09 platform review: this previously ran
    # on APScheduler's implicit defaults (a 10-thread pool nobody chose for
    # this job mix, and no explicit max_instances/misfire_grace_time on any
    # job). Not broken by accident -- max_instances defaults to 1 per job
    # already, so two runs of the SAME job id could never literally overlap
    # -- but relying on an unstated default is fragile, and a job that's
    # still running past its own interval (very plausible for the 3-minute
    # reply polls, which open real Playwright browser sessions per account)
    # would previously misfire silently with no grace window at all. Made
    # explicit below on every job instead of changing behavior.
    scheduler = BackgroundScheduler(
        timezone=config.TIMEZONE,
        executors={"default": ThreadPoolExecutor(max_workers=20)},
        job_defaults={"max_instances": 1, "misfire_grace_time": 60},
    )

    # Every active account across every tenant, collected first so each can
    # be given its own evenly-spaced slot inside the shared windows (see
    # _spread_within_window) -- the old scheme derived each job's time from
    # that account's own configured run_time, which is what allowed
    # discovery and sending for one account to land close together.
    scheduled_accounts: list[tuple[str, str, dict]] = []
    for tenant_id in repo.list_active_tenant_ids():
        # Resolved once per tenant, not per account -- every account for a
        # tenant shares the same scheduling timezone (see
        # OutreachSettings.timezone / repo.get_outreach_timezone()'s own
        # docstring). Falls back to config.TIMEZONE if unset, matching the
        # scheduler-level default above.
        tenant_tz = repo.get_outreach_timezone(tenant_id)
        for account in pool.load_accounts(tenant_id):
            if account.get("status") == "active":
                scheduled_accounts.append((tenant_id, tenant_tz, account))

    sending_accounts = [
        entry for entry in scheduled_accounts
        if entry[2].get("platform") in ("linkedin", "instagram")
    ]

    # Resolved once per distinct tenant_id (not per account) so
    # _is_insurance_tenant's get_settings() call doesn't run twice for
    # Insurance's two accounts (LinkedIn + Email) -- cheap either way, but
    # there's no reason to hit the DB more than once per tenant here.
    _insurance_tenant_cache: dict[str, bool] = {}

    def _is_insurance(tenant_id: str) -> bool:
        if tenant_id not in _insurance_tenant_cache:
            _insurance_tenant_cache[tenant_id] = _is_insurance_tenant(tenant_id)
        return _insurance_tenant_cache[tenant_id]

    # DISCOVERY -- night window (20:00-24:00 Beirut), every active account.
    for index, (tenant_id, tenant_tz, account) in enumerate(scheduled_accounts):
        if _is_insurance(tenant_id):
            # EXPLICIT OWNER REQUEST (2026-09-16), Insurance only -- see
            # _INSURANCE_DISCOVERY_HOUR's own comment above for why 23:00/
            # 23:02 and why Email is anchored 2 minutes after LinkedIn.
            # FOLLOW-UP 2026-09-17: each anchor now gets its own independent
            # +/-10 min jitter via _insurance_jittered_minutes() rather than
            # firing at the byte-identical minute every day (see that
            # function's own docstring and _INSURANCE_JITTER_MINUTES above).
            # Every other tenant (Zimmar, mjivity1, future tenants) falls
            # through to the unchanged _spread_within_window() call below.
            anchor_minute = (
                _INSURANCE_DISCOVERY_MINUTE_EMAIL
                if account.get("platform") == "email"
                else _INSURANCE_DISCOVERY_MINUTE
            )
            hour, minute = _insurance_jittered_minutes(_INSURANCE_DISCOVERY_HOUR, anchor_minute)
        else:
            hour, minute = _spread_within_window(
                index, len(scheduled_accounts),
                _DISCOVERY_WINDOW_START_HOUR, _DISCOVERY_WINDOW_END_HOUR,
            )

        def _run_discovery_for_this_account(tenant_id: str = tenant_id, account_id: str = account["id"]) -> None:
            # Runs the FULL tenant-wide discovery cycle (run_discovery_cycle
            # already loops over every due account for the tenant and gates
            # linkedin/instagram per account's own platform) -- scheduling
            # granularity is per-account, but the work itself reuses the same
            # tenant-scoped cycle function rather than a separate
            # single-account code path, so there is exactly one discovery
            # implementation, not two.
            run_discovery_cycle()

        scheduler.add_job(
            _run_discovery_for_this_account,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=tenant_tz),
            id=f"discovery-{tenant_id}-{account['id']}",
            name=f"Nightly discovery: tenant {tenant_id} / {account['label']}",
            replace_existing=True,
        )

    # SENDING -- morning window (08:00-12:00 Beirut), LinkedIn/Instagram only.
    # Email is sent entirely by the Next.js/Resend pipeline (see
    # sendIfEmailChannel), never by this Python agent, so an email account
    # simply has nothing for this cycle to do.
    for index, (tenant_id, tenant_tz, account) in enumerate(sending_accounts):
        if _is_insurance(tenant_id):
            # EXPLICIT OWNER REQUEST (2026-09-16), Insurance only -- anchored
            # 10:00 Beirut (before the owner's stated 11:00 upper bound; see
            # _INSURANCE_SENDING_HOUR's own comment for why 10:00 clears
            # Zimmar's live sending times). FOLLOW-UP 2026-09-17: jittered
            # +/-10 min via _insurance_jittered_minutes() rather than fixed
            # (see that function's docstring and _INSURANCE_JITTER_MINUTES
            # above). Zimmar and every other tenant keep the unchanged
            # spread/jitter below.
            send_hour, send_minute = _insurance_jittered_minutes(
                _INSURANCE_SENDING_HOUR, _INSURANCE_SENDING_MINUTE
            )
        else:
            send_hour, send_minute = _spread_within_window(
                index, len(sending_accounts),
                _SENDING_WINDOW_START_HOUR, _SENDING_WINDOW_END_HOUR,
            )

        def _run_sending_for_this_account(tenant_id: str = tenant_id, account_id: str = account["id"]) -> None:
            run_account_sending_cycle(tenant_id, account_id)

        scheduler.add_job(
            _run_sending_for_this_account,
            trigger=CronTrigger(hour=send_hour, minute=send_minute, timezone=tenant_tz),
            id=f"sending-{tenant_id}-{account['id']}",
            name=f"Morning cold-outreach sending: tenant {tenant_id} / {account['label']}",
            replace_existing=True,
        )

    # timezone=config.TIMEZONE is load-bearing (added 2026-09-16): without
    # it APScheduler falls back to the scheduler's own default, and since
    # the container's system clock is UTC this job was firing at 20:00 UTC
    # = 23:00 Beirut -- three hours later than intended, every night, with
    # nothing in the logs to show for it. Every other job here already
    # passed an explicit timezone; this one was simply missed.
    scheduler.add_job(
        run_full_pipeline_cycle,
        trigger=CronTrigger(
            hour=_DOWNSTREAM_HOUR, minute=_DOWNSTREAM_MINUTE, timezone=config.TIMEZONE,
        ),
        id="downstream-pipeline",
        name="Daily analysis -> messages -> reminders -> follow-up dispatch",
        replace_existing=True,
    )

    # Fast poll for tenant-written replies ("Reply Here") -- deliberately
    # NOT on the once-daily cadence above, so a reply a tenant sends from
    # the dashboard feels close to real-time rather than waiting up to 24h
    # for the next downstream-pipeline run. See run_reply_send_cycle()'s
    # own docstring.
    scheduler.add_job(
        run_reply_send_cycle,
        trigger=IntervalTrigger(minutes=_REPLY_POLL_INTERVAL_MINUTES),
        id="reply-send-poll",
        name="Fast poll: deliver tenant-written replies",
        replace_existing=True,
    )

    # Fast poll for DETECTING an incoming reply (the counterpart to the
    # send-poll above) -- moved off the once-daily downstream-pipeline
    # cadence 2026-09-07, see run_reply_detection_poll()'s own docstring
    # for the real gap this closes.
    scheduler.add_job(
        run_reply_detection_poll,
        trigger=IntervalTrigger(minutes=_REPLY_DETECTION_POLL_INTERVAL_MINUTES),
        id="reply-detection-poll",
        name="Fast poll: detect incoming WhatsApp/LinkedIn/Instagram replies",
        replace_existing=True,
    )

    # Catches a session going bad BETWEEN sends -- see
    # run_account_health_check_cycle()'s own docstring for why this exists
    # as a separate job rather than folding into the sends above (those
    # only ever check the account they're already about to use, on their
    # own schedule, not every connected account on a schedule of its own).
    scheduler.add_job(
        run_account_health_check_cycle,
        trigger=IntervalTrigger(hours=_ACCOUNT_HEALTH_CHECK_INTERVAL_HOURS),
        id="account-health-check",
        name="Periodic check: is each connected LinkedIn/Instagram account still actually logged in",
        replace_existing=True,
    )

    return scheduler


if __name__ == "__main__":
    print(f"Manual test cycle -- {dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"Target: {DEFAULT_TEST_URL}\n")

    outcomes = run_cycle(force=True)

    if not outcomes:
        print("No active accounts found across any tenant.")
    for outcome in outcomes:
        status = "OK" if outcome["ok"] else f"WARNING ({outcome['warning_type']})"
        print(f"  [{outcome['tenant_id']}] {outcome['account']}: {status}")
        if not outcome["ok"]:
            print(f"    reason: {outcome['reason']}")
