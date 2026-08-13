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
from agent.analysis import analyze
from agent.analysis import founder as founder_detection
from agent.analysis import score as scoring
from agent.analysis import whatsapp_detect
from agent.core import account_pool as pool
from agent.core import health
from agent.core import warmup
from agent.core.session import SessionManager
from agent.crm import followup
from agent.db import repositories as repo
from agent.discovery import instagram, linkedin
from agent.discovery.qualify import qualify_profile
from agent.messaging import approval
from agent.messaging import generate as message_generate
from agent.messaging import style as message_style
from agent.notifications import whatsapp_notify
from agent.sending import (
    instagram_queue,
    linkedin_reply_check,
    linkedin_send,
    whatsapp_reply_check,
    whatsapp_send,
)

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
                account["warning_type"], account["warning_reason"] = warning_type, reason
                whatsapp_notify.notify_account_warning(account)
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


def _save_if_qualified(
    account: dict, platform: str, profile_url: str, raw_profile: dict, niche: str = ""
) -> bool:
    """
    Shared save step for both platforms: skip if this profile is already
    known, qualify it, and insert into `leads` with status "discovered" if it
    passes. Returns True if a new lead was actually saved.
    """
    if repo.lead_profile_url_exists(profile_url):
        return False

    normalised = {**raw_profile, "platform": platform}
    qualifies, reasons = qualify_profile(normalised, niche)
    if not qualifies:
        return False

    repo.insert_lead({
        "account_id": account["id"],
        "platform": platform,
        "business_name": raw_profile.get("display_name") or None,
        "profile_url": profile_url,
        "follower_count": raw_profile.get("follower_or_headcount"),
        "website": raw_profile.get("website"),
        "bio": raw_profile.get("bio"),  # Phase 4's analysis pipeline reads this
        "engagement_sample": raw_profile.get("engagement_sample"),  # Instagram only -- null on LinkedIn leads
        "status": "discovered",
        "notes": " | ".join(reasons),  # keeps the qualification reasoning on the record
    })
    return True


def run_discovery_cycle(force: bool = False) -> list[dict]:
    """
    Discover, qualify, and save new leads on both platforms for whichever
    accounts are due (Phase 3).

    VERIFIED 2026-07-31/08-02: discovery/linkedin.py and discovery/instagram.py's
    scraping selectors were checked against real, live pages using a real
    captured login session (see each module's own docstring for exactly what
    was confirmed and which bugs that testing caught). This orchestration
    itself -- looping accounts, widening weak searches, per-lead error
    isolation -- has not had a full end-to-end run recorded yet; that's a
    separate, still-open item (see PROGRESS.md), not a selector-accuracy
    concern.
    """
    settings = repo.get_settings() or {}
    niche = settings.get("target_niche") or ""
    location = settings.get("target_location") or ""
    industry = settings.get("target_industry") or ""

    accounts = pool.get_due_accounts(force=force)
    summary = []

    with SessionManager() as sessions:
        for account in accounts:
            run = repo.start_run(account["id"])
            context, page = sessions.open(account)
            counts = {"linkedin_found": 0, "linkedin_saved": 0,
                      "instagram_found": 0, "instagram_saved": 0,
                      "errors": [], "skipped_leads": []}

            try:
                _discover_linkedin(account, page, niche, location, industry, counts)
            except Exception as exc:  # noqa: BLE001 -- a whole-platform failure, not one bad lead
                counts["errors"].append(f"linkedin: {exc}")

            try:
                _discover_instagram(account, page, niche, counts)
            except Exception as exc:  # noqa: BLE001
                counts["errors"].append(f"instagram: {exc}")

            sessions.close(account["id"], context)

            finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
            repo.finish_run(
                run["id"],
                leads_found=counts["linkedin_saved"] + counts["instagram_saved"],
                messages_sent=0,
                status="error" if counts["errors"] else "completed",
                finished_at_iso=finished_at,
                notes=" | ".join(counts["errors"]) or None,
                skipped_leads=counts["skipped_leads"],
            )

            summary.append({"account": account["label"], **counts})

    return summary


# A search that comes back with fewer results than this fraction of its
# target count is "weak" -- worth widening the search terms for, rather than
# quietly accepting a thin batch. Never widen past MAX_SEARCH_ATTEMPTS
# rounds; both platforms' widen functions converge to "as wide as it gets"
# in a small, bounded number of steps anyway.
_WEAK_RESULT_FRACTION = 0.5
_MAX_SEARCH_ATTEMPTS = 4


def _is_weak(found: int, limit: int) -> bool:
    return found < max(3, int(limit * _WEAK_RESULT_FRACTION))


def _discover_linkedin(account: dict, page, niche: str, location: str, industry: str, counts: dict) -> None:
    limit = warmup.effective_limit(account, "linkedin")
    search_niche, search_location = niche, location
    results: list[dict] = []
    seen_urls: set[str] = set()
    counts["linkedin_search_terms"] = []

    for _ in range(_MAX_SEARCH_ATTEMPTS):
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

            if _save_if_qualified(account, "linkedin", profile_url, profile, niche):
                counts["linkedin_saved"] += 1
        except Exception as exc:  # noqa: BLE001 -- one bad lead shouldn't stop the rest of the batch
            counts["skipped_leads"].append({
                "platform": "linkedin",
                "identifier": result.get("display_name") or profile_url,
                "reason": str(exc),
            })


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


def run_analysis_cycle(limit: int | None = None) -> list[dict]:
    """
    Analyze every lead currently sitting at status "discovered" (Phase 4):
    deep analysis, founder detection, WhatsApp number detection, and scoring.

    Task 10 from the spec: the full enriched record is saved to
    client_history BEFORE any message is generated (see
    run_message_generation_cycle() below, Phase 5) -- client_history exists
    permanently, whether or not this lead is ever contacted.

    `limit` caps how many leads are processed in one call -- useful for a
    careful first test rather than analysing an entire backlog at once.
    """
    leads = repo.leads_by_status("discovered")
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

        repo.insert_client_history({
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
    "analyzed" (Phase 5): one message on the lead's discovery platform, plus
    an additional WhatsApp message if Phase 4 found a public WhatsApp number
    -- WhatsApp is always an ADDITIONAL channel, never a replacement for the
    primary one (see whatsapp_detect.py's module docstring).

    The active style (direct/discovery) is read once per cycle, not once per
    lead -- style.get_active_style() only rotates on elapsed duration, so
    every lead in the same run gets the same style, which is also what makes
    the per-(channel, style) system prompt actually reuse the cache across
    the whole batch rather than just within one lead's calls.

    `limit` caps how many leads are processed in one call, same reasoning as
    run_analysis_cycle's `limit`.
    """
    leads = repo.leads_by_status("analyzed")
    if limit is not None:
        leads = leads[:limit]

    active_style = message_style.get_active_style()
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
                repo.insert_message({
                    "lead_id": lead["id"],
                    "channel": channel,
                    "body": body,
                })

            repo.update_lead(lead["id"], {
                "generated_message": primary_body,
                "message_style_used": active_style,
                "status": "awaiting_approval",
            })
            results.append({
                "lead": lead.get("business_name"), "ok": True,
                "channels": channels, "style": active_style,
            })
        except Exception as exc:  # noqa: BLE001 -- one bad lead shouldn't stop the batch
            results.append({"lead": lead.get("business_name"), "ok": False, "error": str(exc)})

    return results


def run_sending_cycle(limit: int | None = None) -> list[dict]:
    """
    Route every approved, not-yet-sent message to its channel (Phase 7).

    Instagram never auto-sends -- Instagram's ToS bans automated cold
    outreach, so it's queued for a human to send by hand via
    instagram_queue.queue_for_manual_send(), exactly as the spec requires.

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

    `limit` caps how many messages are processed in one call, same reasoning
    as the analysis/message-generation cycles' `limit`.
    """
    messages = repo.messages_approved_pending()
    if limit is not None:
        messages = messages[:limit]

    results = []
    for message in messages:
        channel = message.get("channel")
        try:
            if channel == "instagram":
                instagram_queue.queue_for_manual_send(message)
                results.append({
                    "message_id": message["id"], "channel": channel,
                    "ok": True, "action": "queued_for_manual_send",
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
                results.append({
                    "message_id": message["id"], "channel": channel, "ok": False,
                    "reason": f"{channel} auto-send not yet implemented",
                })
        except Exception as exc:  # noqa: BLE001 -- one bad message shouldn't stop the rest
            results.append({"message_id": message["id"], "channel": channel, "ok": False, "error": str(exc)})

    return results


def run_approval_reminder_check() -> dict | None:
    """
    Phase 8's approval reminder trigger: if any message has sat "awaiting"
    longer than settings.approval_reminder_hours, notify Mohamad once with
    the count. Returns the logged notification, or None if nothing is overdue.
    """
    pending = approval.messages_needing_reminder()
    if not pending:
        return None
    return whatsapp_notify.notify_approval_reminder(len(pending))


def run_full_pipeline_cycle() -> dict:
    """
    Runs every downstream step once, in spec order: analysis -> message
    generation -> sending -> approval-reminder check -> WhatsApp reply
    check -> LinkedIn reply check -> due follow-up dispatch. This is what
    build_daily_schedule() schedules once daily (see its docstring for why
    this isn't per-account, unlike discovery).

    Each step already isolates its own per-lead/per-message failures (see
    each cycle function's own docstring) -- the two reply-check steps get
    an extra try/except here on top of that, since each raises outright
    (not a per-item result list) on its own precondition failing (missing
    Twilio config; any future LinkedIn selector drift), which would
    otherwise wipe out the results of the steps that already ran
    successfully before it. linkedin_reply_check.py's selectors were
    re-verified live 2026-08-08 against a real inbox (see that module's
    docstring) -- this try/except stays regardless, same defensive posture
    as whatsapp's, since either platform can change its DOM/API at any time.

    Follow-up dispatch runs LAST, after both reply checks -- a lead that
    replied today must have its follow-up already cancelled (see
    reply_detection.handle_reply_detected -> followup.cancel_pending)
    before dispatch_due_followups() would otherwise generate a follow-up
    for someone who just responded.
    """
    analysis = run_analysis_cycle()
    messages = run_message_generation_cycle()
    sending = run_sending_cycle()
    reminder = run_approval_reminder_check()
    try:
        whatsapp_replies = whatsapp_reply_check.check_whatsapp_replies()
    except Exception as exc:  # noqa: BLE001 -- e.g. WhatsAppNotConfigured; don't lose the steps above
        whatsapp_replies = {"ok": False, "error": str(exc)}
    try:
        linkedin_replies = linkedin_reply_check.check_linkedin_replies()
    except Exception as exc:  # noqa: BLE001 -- e.g. unverified selector mismatch; don't lose the steps above
        linkedin_replies = {"ok": False, "error": str(exc)}
    try:
        followups_dispatched = followup.dispatch_due_followups()
    except Exception as exc:  # noqa: BLE001 -- don't lose the steps above over one bad batch
        followups_dispatched = {"ok": False, "error": str(exc)}

    return {
        "analysis": analysis,
        "messages": messages,
        "sending": sending,
        "approval_reminder": reminder,
        "whatsapp_replies": whatsapp_replies,
        "followups_dispatched": followups_dispatched,
        "linkedin_replies": linkedin_replies,
    }


# Fixed daily time (in config.TIMEZONE) for run_full_pipeline_cycle -- picked
# to land after every account's own run_time so discovery has had its whole
# day's chance to run first. A first-pass choice, not a tuned one: revisit
# once real cycle durations are known from actual live runs.
_DOWNSTREAM_HOUR = 20
_DOWNSTREAM_MINUTE = 0


def build_daily_schedule(accounts: list[dict]) -> BackgroundScheduler:
    """
    Wire up the full daily pipeline for the always-on server (Phase 10):
    one discovery cron trigger per account at its own configured run_time,
    replacing run_cycle's Phase 2 placeholder role now that Phase 3's real
    discovery exists (run_cycle itself is untouched and still used for the
    manual test entry point below) -- plus one shared downstream-pipeline
    trigger (run_full_pipeline_cycle) that runs once daily.

    The downstream steps are scheduled just once, not per account, because
    each of them processes every pending record across all 3 accounts in a
    single pass (repo.leads_by_status(), repo.messages_approved_pending(),
    etc.) -- unlike discovery, they aren't scoped to "this one account's
    turn" to begin with.

    Returns the scheduler unstarted -- calling code (the always-on server
    process, from Phase 10) decides when to call .start() and keep the
    process alive.
    """
    scheduler = BackgroundScheduler(timezone=config.TIMEZONE)

    for account in accounts:
        hour, minute = (int(p) for p in account["run_time"].split(":")[:2])
        scheduler.add_job(
            run_discovery_cycle,
            trigger=CronTrigger(hour=hour, minute=minute),
            id=f"discovery-{account['id']}",
            name=f"Daily discovery: {account['label']}",
            replace_existing=True,
        )

    scheduler.add_job(
        run_full_pipeline_cycle,
        trigger=CronTrigger(hour=_DOWNSTREAM_HOUR, minute=_DOWNSTREAM_MINUTE),
        id="downstream-pipeline",
        name="Daily analysis -> messages -> sending -> reminders -> replies",
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
