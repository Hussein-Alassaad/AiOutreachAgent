"""
Runs each account at its own configured time.

Each of the 3 accounts has an independent run time, staggered through the day
and editable from the dashboard. Opens a row in `runs` at start and closes it
with the finish time -- that's what the dashboard Run Status panel displays.

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

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from agent import config
from agent.core import account_pool as pool
from agent.core import health
from agent.core.session import SessionManager
from agent.db import repositories as repo
from agent.discovery import instagram, linkedin
from agent.discovery.qualify import qualify_profile

# Phase 2 has no real discovery yet -- this is a harmless, neutral page used
# purely to prove a session can open, navigate, and be health-checked. Phase 3
# replaces this with the actual LinkedIn/Instagram search entry points.
DEFAULT_TEST_URL = "https://example.com"


def run_cycle(target_url: str = DEFAULT_TEST_URL, force: bool = False) -> list[dict]:
    """
    Run one cycle for whichever accounts are due (or all active accounts, if
    force=True). For each: open an isolated session, visit target_url, check
    its health, and log the outcome as a `runs` row.

    Returns a list of per-account result dicts, mainly so a manual test run can
    print a clear summary of what happened to each account.
    """
    accounts = pool.get_due_accounts(force=force)
    results = []

    if not accounts:
        return results

    with SessionManager() as sessions:
        for account in accounts:
            run = repo.start_run(account["id"])
            context, page = sessions.open(account)

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
                    run["id"], leads_found=0, messages_sent=0,
                    status="completed", finished_at_iso=finished_at,
                )
            else:
                health.record_warning(account["id"], warning_type, reason)
                repo.finish_run(
                    run["id"], leads_found=0, messages_sent=0,
                    status="error", finished_at_iso=finished_at, notes=reason,
                )

            results.append({
                "account": account["label"],
                "ok": ok,
                "warning_type": warning_type,
                "reason": reason,
            })

    return results


def _save_if_qualified(account: dict, platform: str, profile_url: str, raw_profile: dict) -> bool:
    """
    Shared save step for both platforms: skip if this profile is already
    known, qualify it, and insert into `leads` with status "discovered" if it
    passes. Returns True if a new lead was actually saved.
    """
    if repo.lead_profile_url_exists(profile_url):
        return False

    normalised = {**raw_profile, "platform": platform}
    qualifies, reasons = qualify_profile(normalised)
    if not qualifies:
        return False

    repo.insert_lead({
        "account_id": account["id"],
        "platform": platform,
        "business_name": raw_profile.get("display_name") or None,
        "profile_url": profile_url,
        "follower_count": raw_profile.get("follower_or_headcount"),
        "website": raw_profile.get("website"),
        "status": "discovered",
        "notes": " | ".join(reasons),  # keeps the qualification reasoning on the record
    })
    return True


def run_discovery_cycle(force: bool = False) -> list[dict]:
    """
    Discover, qualify, and save new leads on both platforms for whichever
    accounts are due (Phase 3).

    NOT YET LIVE-TESTED: the discovery/linkedin.py and discovery/instagram.py
    scraping selectors are unverified (see the honesty notes in those files) --
    this orchestration is real and ready, but a genuine live run requires a
    logged-in session on each account, which is what the manual-login step
    (agent/core/session.py's persistent storage) is for. Runs against
    LinkedIn/Instagram before login will mostly find login walls, not leads --
    that is expected, not a bug in this function.
    """
    settings = repo.get_settings() or {}
    niche = settings.get("target_niche") or ""
    location = settings.get("target_location") or ""

    accounts = pool.get_due_accounts(force=force)
    summary = []

    with SessionManager() as sessions:
        for account in accounts:
            context, page = sessions.open(account)
            counts = {"linkedin_found": 0, "linkedin_saved": 0,
                      "instagram_found": 0, "instagram_saved": 0, "errors": []}

            try:
                _discover_linkedin(account, page, niche, location, counts)
            except Exception as exc:  # noqa: BLE001 -- a bad selector shouldn't crash the run
                counts["errors"].append(f"linkedin: {exc}")

            try:
                _discover_instagram(account, page, niche, counts)
            except Exception as exc:  # noqa: BLE001
                counts["errors"].append(f"instagram: {exc}")

            sessions.close(account["id"], context)
            summary.append({"account": account["label"], **counts})

    return summary


def _discover_linkedin(account: dict, page, niche: str, location: str, counts: dict) -> None:
    limit = account.get("linkedin_daily_limit", 30)
    page.goto(linkedin.build_search_url(niche, location), timeout=15_000)
    results = linkedin.extract_search_results(page)[:limit]
    counts["linkedin_found"] = len(results)

    for result in results:
        profile_url = result.get("profile_url")
        if not profile_url:
            continue
        page.goto(profile_url, timeout=15_000)
        profile = linkedin.extract_company_profile(page)
        profile["display_name"] = result.get("display_name")
        if _save_if_qualified(account, "linkedin", profile_url, profile):
            counts["linkedin_saved"] += 1


def _discover_instagram(account: dict, page, niche: str, counts: dict) -> None:
    limit = account.get("ig_daily_limit", 20)
    page.goto(instagram.build_hashtag_url(niche), timeout=15_000)
    posts = instagram.extract_hashtag_results(page)[:limit]
    counts["instagram_found"] = len(posts)

    for post in posts:
        profile_url = instagram.resolve_post_to_profile_url(page, post["post_url"])
        if not profile_url:
            continue
        page.goto(profile_url, timeout=15_000)
        profile = instagram.extract_profile(page)
        if _save_if_qualified(account, "instagram", profile_url, profile):
            counts["instagram_saved"] += 1


def build_daily_schedule(accounts: list[dict]) -> BackgroundScheduler:
    """
    Wire up one cron trigger per account at its own configured run_time, in the
    project's configured timezone. Returns the scheduler unstarted -- calling
    code (the always-on server process, from Phase 10) decides when to call
    .start() and keep the process alive.
    """
    scheduler = BackgroundScheduler(timezone=config.TIMEZONE)

    for account in accounts:
        hour, minute = (int(p) for p in account["run_time"].split(":")[:2])
        scheduler.add_job(
            run_cycle,
            trigger=CronTrigger(hour=hour, minute=minute),
            id=f"account-{account['id']}",
            name=f"Daily run: {account['label']}",
            replace_existing=True,
        )

    return scheduler


if __name__ == "__main__":
    print(f"Manual test cycle -- {dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"Target: {DEFAULT_TEST_URL}\n")

    outcomes = run_cycle(force=True)

    if not outcomes:
        print("No active accounts found.")
    for outcome in outcomes:
        status = "OK" if outcome["ok"] else f"WARNING ({outcome['warning_type']})"
        print(f"  {outcome['account']}: {status}")
        if not outcome["ok"]:
            print(f"    reason: {outcome['reason']}")
