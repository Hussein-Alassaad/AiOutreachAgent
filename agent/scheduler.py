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
import random
import time

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


def _resolve_search_niche(niche: str) -> str:
    """
    A configured niche is used as-is. An empty niche means "target any type
    of company" -- but LinkedIn's search has no such mode, so this picks a
    real industry term at random instead of searching with an empty
    keyword (which live-verified returns 0 results). Called once per
    discovery cycle, so a fresh random term is picked each run -- over many
    runs this covers a broad mix of industries rather than the same one
    every time.
    """
    return niche or random.choice(_RANDOM_INDUSTRY_TERMS)

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
    niche = _resolve_search_niche(settings.get("target_niche") or "")
    location = settings.get("target_location") or ""
    industry = settings.get("target_industry") or ""

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
                    try:
                        _discover_linkedin(account, page, niche, location, industry, counts)
                    except Exception as exc:  # noqa: BLE001 -- a whole-platform failure, not one bad lead
                        counts["errors"].append(f"linkedin: {exc}")
                        log_error("discovery", exc, channel="linkedin", account_id=account["id"])
                elif account.get("platform") == "instagram":
                    try:
                        _discover_instagram(account, page, niche, counts)
                    except Exception as exc:  # noqa: BLE001
                        counts["errors"].append(f"instagram: {exc}")
                        log_error("discovery", exc, channel="instagram", account_id=account["id"])
                # else: platform == "email" (or anything else) -- no browser-automation
                # discovery exists for that channel; the Next.js app owns email entirely.

            sessions.close(account["id"], context)

            finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
            repo.finish_run(
                tenant_id,
                run["id"],
                leads_found=counts["linkedin_saved"] + counts["instagram_saved"],
                messages_sent=0,
                status="error" if counts["errors"] else "completed",
                finished_at_iso=finished_at,
                notes=" | ".join(counts["errors"]) or None,
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
_MAX_SEARCH_ATTEMPTS = 4

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
    bio_lower = f" {bio.lower()} "
    for marker in _FOREIGN_LOCATION_MARKERS:
        if marker in configured_location.lower():
            continue  # the marker IS the configured location -- not foreign
        if marker in bio_lower:
            return marker.strip()
    return None


# 2026-09-02, real instruction from the platform owner: Insurance's
# discovery must never target other insurance companies (their own
# industry, not a prospect) -- searching real industry terms (trading,
# commercial, etc.) can still surface an insurance company incidentally,
# since these terms aren't insurance-exclusive. This is a real, positive
# exclusion check on the company's own bio/industry text, independent of
# which search term found it.
_INSURANCE_COMPANY_MARKERS = [
    "insurance", "insurer", "reinsurance", "assurance company",
    "takaful", "underwriter", "underwriting",
]


def _is_insurance_company(bio: str, industry: str | None) -> bool:
    """
    True if the company's own bio or LinkedIn-listed industry plainly
    identifies it as an insurance company -- checked against BOTH fields
    since either can carry the signal (a company's stated industry is
    often more reliable than its bio, but not every profile has one
    filled in). Deliberately simple substring matching, same posture as
    _mentions_foreign_location() above: not exhaustive, but catches the
    common, plain case rather than needing an LLM call for every lead.
    """
    haystack = f" {bio.lower()} {(industry or '').lower()} "
    return any(marker in haystack for marker in _INSURANCE_COMPANY_MARKERS)


def _is_weak(found: int, limit: int) -> bool:
    return found < max(3, int(limit * _WEAK_RESULT_FRACTION))


def _discover_linkedin(account: dict, page, niche: str, location: str, industry: str, counts: dict) -> None:
    limit = warmup.effective_limit(account, "linkedin")
    search_niche, search_location = niche, location
    results: list[dict] = []
    seen_urls: set[str] = set()
    counts["linkedin_search_terms"] = []

    for attempt in range(_MAX_SEARCH_ATTEMPTS):
        if attempt > 0:
            # LIVE-CONFIRMED 2026-09-01: LinkedIn force-logged-out a real
            # account after a burst of back-to-back automated searches with
            # no pause between them (confirmed via a real session that
            # returned real results, then got redirected to /uas/login
            # after several more rapid searches in the same run) -- this
            # widening loop had zero delay between attempts. A real person
            # widening a search takes a few seconds between each try, not
            # zero; randomizing (not a fixed constant) avoids a perfectly
            # uniform, itself-suspicious interval.
            page.wait_for_timeout(random.randint(15_000, 30_000))
        counts["linkedin_search_terms"].append({"niche": search_niche, "location": search_location})
        page.goto(
            linkedin.build_search_url(search_niche, search_location, industry),
            timeout=30_000, wait_until="domcontentloaded",
        )
        for result in linkedin.extract_search_results(page):
            url = result.get("profile_url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append(result)

        if not _is_weak(len(results), limit):
            break

        next_niche, next_location = linkedin.widen_search_terms(search_niche, search_location)
        if (next_niche, next_location) == (search_niche, search_location):
            break  # already as wide as it gets -- widening further would just repeat the same search
        search_niche, search_location = next_niche, next_location

    results = results[:limit]
    counts["linkedin_found"] = len(results)

    for result in results:
        profile_url = result.get("profile_url")
        if not profile_url:
            continue
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
            else:
                foreign_marker = _mentions_foreign_location(profile.get("bio") or "", location or "")
                if foreign_marker:
                    mismatch_reason = (
                        f"configured for {location!r}, company's own bio mentions {foreign_marker!r}."
                    )

            if mismatch_reason:
                counts["skipped_leads"].append({
                    "platform": "linkedin",
                    "identifier": result.get("display_name") or profile_url,
                    "reason": f"Location mismatch: {mismatch_reason}",
                })
                continue

            # 2026-09-02, real instruction: never target other insurance
            # companies -- see _is_insurance_company()'s own comment.
            if _is_insurance_company(profile.get("bio") or "", profile.get("industry")):
                counts["skipped_leads"].append({
                    "platform": "linkedin",
                    "identifier": result.get("display_name") or profile_url,
                    "reason": "Excluded: this company is itself an insurance company.",
                })
                continue

            if _save_if_qualified(account, "linkedin", profile_url, profile, niche):
                counts["linkedin_saved"] += 1
        except Exception as exc:  # noqa: BLE001 -- one bad lead shouldn't stop the rest of the batch
            counts["skipped_leads"].append({
                "platform": "linkedin",
                "identifier": result.get("display_name") or profile_url,
                "reason": str(exc),
            })
            log_error("discovery", exc, channel="linkedin", account_id=account["id"])


def _discover_instagram(account: dict, page, niche: str, counts: dict) -> None:
    limit = warmup.effective_limit(account, "instagram")
    search_niche = niche
    posts: list[dict] = []
    seen_urls: set[str] = set()
    counts["instagram_search_terms"] = []

    for _ in range(_MAX_SEARCH_ATTEMPTS):
        counts["instagram_search_terms"].append(search_niche)
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

        if not _is_weak(len(posts), limit):
            break

        next_niche = instagram.widen_hashtag_terms(search_niche)
        if next_niche == search_niche:
            break  # already down to one word -- as wide as it gets
        search_niche = next_niche

    posts = posts[:limit]
    counts["instagram_found"] = len(posts)

    for post in posts:
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
            if _save_if_qualified(account, "instagram", profile_url, profile, niche):
                counts["instagram_saved"] += 1
        except Exception as exc:  # noqa: BLE001 -- one bad lead shouldn't stop the rest of the batch
            counts["skipped_leads"].append({
                "platform": "instagram",
                "identifier": post.get("post_url"),
                "reason": str(exc),
            })
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
                tenant_results = _run_sending_cycle_for_tenant(limit)
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


def _run_sending_cycle_for_tenant(limit: int | None) -> list[dict]:
    # Reply-tagged messages (is_reply=True, from "Reply Here") are
    # deliberately excluded here -- run_reply_send_cycle() below picks them
    # up on its own fast ~2-3 min poll instead of waiting for this cycle's
    # normal once-daily cadence, so a reply feels close to real-time. This
    # cycle only ever sees fresh cold-outreach messages.
    messages = [m for m in repo.messages_approved_pending() if not m.get("is_reply")]
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


def run_full_pipeline_cycle() -> dict:
    """
    Runs every downstream step once, in spec order: analysis -> message
    generation -> sending -> approval-reminder check -> due follow-up
    dispatch. This is what build_daily_schedule() schedules once daily (see
    its docstring for why this isn't per-account, unlike discovery).

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
    _cycle/run_sending_cycle/run_approval_reminder_check each already loop
    over every active tenant internally (see their own docstrings) -- called
    plainly here, same as before the port. The follow-up dispatch step
    below does NOT loop internally (crm/followup.py is out of scope for
    this port -- it only calls repo.*, never the DB directly), so this
    function wraps it in its own per-tenant tenant_scope(...) loop instead.
    """
    analysis = run_analysis_cycle()
    messages = run_message_generation_cycle()
    sending = run_sending_cycle()
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
        "sending": sending,
        "approval_reminder": reminder,
        "followups_dispatched": followups_dispatched,
    }


# Fixed daily time (in config.TIMEZONE) for run_full_pipeline_cycle -- picked
# to land after every account's own run_time so discovery has had its whole
# day's chance to run first. A first-pass choice, not a tuned one: revisit
# once real cycle durations are known from actual live runs.
_DOWNSTREAM_HOUR = 20
_DOWNSTREAM_MINUTE = 0

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
    scheduler = BackgroundScheduler(timezone=config.TIMEZONE)

    for tenant_id in repo.list_active_tenant_ids():
        # Resolved once per tenant, not per account -- every account for a
        # tenant shares the same scheduling timezone (see
        # OutreachSettings.timezone / repo.get_outreach_timezone()'s own
        # docstring). Falls back to config.TIMEZONE if unset, matching the
        # scheduler-level default above.
        tenant_tz = repo.get_outreach_timezone(tenant_id)

        for account in pool.load_accounts(tenant_id):
            if account.get("status") != "active":
                continue
            hour, minute = (int(p) for p in account["run_time"].split(":")[:2])

            def _run_discovery_for_this_account(tenant_id: str = tenant_id, account_id: str = account["id"]) -> None:
                # Runs the FULL tenant-wide discovery cycle (run_discovery_cycle
                # already loops over every due account for the tenant and gates
                # linkedin/instagram per account's own platform) -- scheduling
                # granularity is per-account (this account's own run_time), but
                # the work itself reuses the same tenant-scoped cycle function
                # rather than a separate single-account code path, so there is
                # exactly one discovery implementation, not two.
                run_discovery_cycle()

            scheduler.add_job(
                _run_discovery_for_this_account,
                trigger=CronTrigger(hour=hour, minute=minute, timezone=tenant_tz),
                id=f"discovery-{tenant_id}-{account['id']}",
                name=f"Daily discovery: tenant {tenant_id} / {account['label']}",
                replace_existing=True,
            )

    scheduler.add_job(
        run_full_pipeline_cycle,
        trigger=CronTrigger(hour=_DOWNSTREAM_HOUR, minute=_DOWNSTREAM_MINUTE),
        id="downstream-pipeline",
        name="Daily analysis -> messages -> sending -> reminders -> follow-up dispatch",
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
