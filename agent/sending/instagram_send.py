"""
Sends messages on Instagram via the account's own session -- both the
AI-prepared first cold message (approved by a human on the "Instagram"
dashboard page) and replies (typed by a human on "Reply Here").

============================================================================
NOT YET LIVE-VERIFIED -- built against Instagram's known/publicly-documented
DOM structure, same first-pass approach linkedin_send.py originally took
before ITS live verification (see that module's own docstring history).
Every selector below is a reasonable inference, not a confirmed one --
WATCH THIS CLOSELY against a real connected test account before trusting
either path (cold send or reply) unattended, same caution this codebase
applies to every other browser-automation module. Expect to find and fix
real selector mismatches on the first live run, same as
sending/linkedin_reply_check.py's own docstring documents happening there
three separate times.

DELIBERATE PRODUCT DECISION (not a default): earlier versions of this
codebase kept ALL Instagram sending manual (human opens the real app,
copy-pastes, sends) specifically because automating unsolicited cold DMs is
Instagram's highest-risk automation pattern for a ban. This module changes
that -- both the cold message AND replies are now agent-delivered, so the
account is never touched from a different location/IP than the agent's own
consistent proxy. That tradeoff (automation risk vs. location-consistency
risk) was made explicitly by the platform owner, not assumed by this code.
============================================================================

APPROACH: mirrors linkedin_send.py's shape closely --
  - send_cold_message(): opens the lead's profile_url, clicks Message,
    types, sends. This is the ORIGINAL AI-prepared message -- a human still
    approves it (Approval Queue / Instagram dashboard page), this module
    only replaces the "you copy-paste it by hand" step with "the agent
    performs the actual send."
  - send_reply(): opens the existing DM thread (matched by business_name,
    same approach as linkedin_reply_check.py's _open_thread_for_lead) and
    sends into it.
"""

from __future__ import annotations

import datetime as dt

from playwright.sync_api import Page

from agent.core.pacing import human_delay, human_type
from agent.core.session import SessionManager
from agent.crm import pipeline
from agent.db import repositories as repo
from agent.messaging import approval
from agent.sending import attachments

INSTAGRAM_INBOX_URL = "https://www.instagram.com/direct/inbox/"

# LIVE-CONFIRMED 2026-09-06: the original substring selector
# ("div[role='button']:has-text('Message')") matched the wrong element --
# Instagram's own top-nav Messages/DM-inbox icon renders as
# div[role='button'] with text "1\nMessages" (unread badge + label), which
# is a substring superset of "Message" and appears EARLIER in DOM order
# than the real per-profile Message button, so .first grabbed the nav icon
# every time. Real profile page dump (hussein._.alassaad, 994 followers):
# the genuine button is div[role='button'] with EXACT text "Message",
# scoped inside <header>, sitting alongside "Follow" as sibling buttons --
# scoping to header AND requiring an exact text match (not substring) is
# what actually disambiguates it from the nav icon.
_PROFILE_MESSAGE_BUTTON_SELECTOR = "header div[role='button']:text-is('Message')"

# LIVE-CONFIRMED 2026-09-06: the original selector assumed the composer
# would carry an aria-label or placeholder -- the real element has NEITHER
# (page dump: div[contenteditable='true'] with no aria-label, no
# placeholder, no other identifying attribute). It's the ONLY
# contenteditable element on the page once the DM panel is open (confirmed:
# exactly 1 match), so matching plain contenteditable=true is sufficient
# and more robust than guessing at attributes Instagram doesn't actually
# set.
_COMPOSER_SELECTOR = "div[contenteditable='true']"
# LIVE-CONFIRMED 2026-09-06: there is no text-labeled Send button at all --
# the real control is an icon-only paper-plane button, an
# svg[aria-label='Send'] nested inside the clickable element. Playwright
# resolves a click on the svg to its actual pointer target automatically
# (same as clicking any nested icon), so targeting the svg directly is
# reliable and doesn't depend on guessing the wrapping element's tag/role.
_SEND_BUTTON_SELECTOR = "svg[aria-label='Send']"
# NOT yet live-verified -- Instagram's DM composer attach/media picker,
# same inference approach as _COMPOSER_SELECTOR above.
_ATTACHMENT_BUTTON_SELECTOR = "svg[aria-label='Attach a photo or video'], div[role='button'][aria-label*='attach' i]"

# LIVE-CONFIRMED 2026-09-06: the inbox's conversation rows carry NO
# role='listitem' anywhere in the DOM (confirmed: 0 matches on a real
# inbox with several real threads visible) -- the actual clickable row is
# 10 ancestor levels up from the name text, a div[role='button'] with
# tabindex='0'. Matched by visible text the same way
# linkedin_reply_check.py matches business_name, since Instagram's own
# conversation-list DOM has no stable per-thread identifier exposed
# either.
CONVERSATION_LIST_ITEM_SELECTOR = "div[role='button']"


class NoMessageButtonAvailable(RuntimeError):
    """
    Raised when a lead's Instagram profile has no reachable Message action
    (private account with no accepted follow, business account with DMs
    restricted, etc.) -- a normal, expected "can't send this way" outcome,
    same treatment as linkedin_send.py's identically-named exception.
    """


class NoExistingThread(RuntimeError):
    """
    Raised when a reply is queued for a lead with no existing Instagram DM
    thread to reply into -- same normal-outcome treatment as
    linkedin_send.py's NoExistingThread.
    """


class SessionLoggedOut(RuntimeError):
    """
    Raised when a saved session is no longer actually authenticated on
    Instagram's side -- see linkedin_send.py's identically-named exception
    for the full reasoning (same real gap, same fix, both channels hit it
    live the same night).
    """


def send_cold_message(message: dict) -> dict:
    """
    Sends the AI-prepared first message to a lead's Instagram profile.
    Human-approved content (Approval Queue), agent-performed delivery --
    see module docstring for why this replaced the old copy-paste-by-hand
    flow. Mirrors linkedin_send.send_message()'s bookkeeping exactly:
    send_status/sent_at/sent_via_account, contact_count/first_contacted_at,
    the pipeline move to "contacted", client_history.
    """
    lead = repo.get_lead(message["lead_id"])
    if not lead or not lead.get("profile_url"):
        raise ValueError(f"Message {message['id']} has no lead profile_url to send to.")

    body = approval.active_body(message)
    account = repo.get_account(lead["account_id"])
    if not account:
        raise ValueError(f"Lead {lead['id']} has no owning account to send from.")

    with SessionManager() as sessions:
        context, page, new_verified_ip = sessions.open(account)
        if new_verified_ip and not account.get("verified_proxy_ip"):
            repo.update_account(account["id"], {"verified_proxy_ip": new_verified_ip})
        try:
            page.goto(lead["profile_url"], timeout=30_000, wait_until="domcontentloaded")
            # Real gap fixed 2026-09-06: a saved session can still be
            # genuinely logged out on Instagram's side -- live-confirmed
            # tonight, an account the dashboard showed "Connected" the
            # whole time actually redirected to the login page on real
            # navigation. Same fix as linkedin_send.py's identical check:
            # detect it here, right where every real send already
            # navigates, and persist it immediately so the dashboard
            # reflects reality instead of staying stuck on a stale
            # "Connected".
            if "/accounts/login" in page.url:
                repo.update_account(account["id"], {"login_status": "failed", "login_error": "Session logged out on Instagram -- reconnect via the extension."})
                raise SessionLoggedOut(
                    f"Account {account.get('label') or account['id']} is no longer logged in on Instagram "
                    f"(redirected to {page.url})."
                )
            _send_from_profile(page, lead, body)
        finally:
            sessions.close(account["id"], context)

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    updated_message = repo.update_message(message["id"], {
        "send_status": "sent",
        "sent_at": now_iso,
        "sent_via_account": account["id"],
    })

    contact_updates = {"contact_count": (lead.get("contact_count") or 0) + 1}
    if not lead.get("first_contacted_at"):
        contact_updates["first_contacted_at"] = now_iso
    repo.update_lead(message["lead_id"], contact_updates)

    pipeline.move_stage(message["lead_id"], "contacted", changed_by="agent")
    repo.mark_client_history_contacted(message["lead_id"])

    return updated_message


def send_reply(message: dict) -> dict:
    """
    Delivers a tenant-written reply into the lead's existing Instagram DM
    thread. Does NOT touch contact_count/first_contacted_at/pipeline stage
    -- those already happened on the original cold send; see
    linkedin_send.send_reply()'s docstring for the identical reasoning.

    Handles message["attachment_url"] the same way linkedin_send.send_reply()
    does -- download to temp file, attach via Instagram's own file-picker
    button before typing the body, clean up after. Attachment-picker
    selector is NOT yet live-verified.
    """
    lead = repo.get_lead(message["lead_id"])
    if not lead:
        raise ValueError(f"Message {message['id']} has no lead to send to.")

    business_name = lead.get("business_name") or ""
    body = approval.active_body(message)
    attachment_url = message.get("attachment_url")
    attachment_path = None
    account = repo.get_account(lead["account_id"])
    if not account:
        raise ValueError(f"Lead {lead['id']} has no owning account to send from.")

    try:
        if attachment_url:
            attachment_path = attachments.download_attachment(attachment_url, message.get("attachment_name"))

        # LIVE-CONFIRMED 2026-09-06: the inbox list row shows the person's
        # DISPLAY NAME ("Hussein Alassaad"), not their @handle
        # ("hussein._.alassaad") -- confirmed live: filtering by the
        # display name matched the real row (count=1), filtering by the
        # handle parsed from profile_url matched nothing (count=0). An
        # earlier version of this fix assumed the opposite and was wrong.
        # business_name is the best available proxy for the real display
        # name for a well-formed lead; fall back to the handle only if
        # business_name is empty, since some match is better than none.
        profile_url = lead.get("profile_url") or ""
        handle = profile_url.rstrip("/").rsplit("/", 1)[-1] if profile_url else ""
        match_text = business_name or handle

        with SessionManager() as sessions:
            context, page, new_verified_ip = sessions.open(account)
            if new_verified_ip and not account.get("verified_proxy_ip"):
                repo.update_account(account["id"], {"verified_proxy_ip": new_verified_ip})
            try:
                page.goto(INSTAGRAM_INBOX_URL, timeout=30_000, wait_until="domcontentloaded")
                if "/accounts/login" in page.url:
                    repo.update_account(account["id"], {"login_status": "failed", "login_error": "Session logged out on Instagram -- reconnect via the extension."})
                    raise SessionLoggedOut(
                        f"Account {account.get('label') or account['id']} is no longer logged in on Instagram "
                        f"(redirected to {page.url})."
                    )
                item = page.locator(CONVERSATION_LIST_ITEM_SELECTOR, has_text=match_text).first
                try:
                    item.wait_for(state="visible", timeout=15_000)
                except Exception as exc:  # noqa: BLE001 -- Playwright's TimeoutError, re-raised as our own domain exception
                    raise NoExistingThread(
                        f"No existing Instagram conversation found for {match_text or lead.get('profile_url')}."
                    ) from exc
                human_delay()
                item.click()

                box = page.locator(_COMPOSER_SELECTOR).first
                box.wait_for(state="visible", timeout=10_000)

                if attachment_path:
                    human_delay()
                    with page.expect_file_chooser() as fc_info:
                        page.locator(_ATTACHMENT_BUTTON_SELECTOR).first.click()
                    fc_info.value.set_files(str(attachment_path))
                    human_delay()

                if body:
                    human_delay()
                    human_type(box, body)
                human_delay()
                page.locator(_SEND_BUTTON_SELECTOR).first.click()
            finally:
                sessions.close(account["id"], context)
    finally:
        if attachment_path:
            attachments.cleanup_attachment(attachment_path)

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    return repo.update_message(message["id"], {
        "send_status": "sent",
        "sent_at": now_iso,
        "sent_via_account": account["id"],
    })


def _send_from_profile(page: Page, lead: dict, body: str) -> None:
    # LIVE-CONFIRMED 2026-09-06: page.goto()'s wait_until="domcontentloaded"
    # fires as soon as the HTML skeleton parses, well before Instagram's
    # client-side JS has actually rendered the profile header -- an instant
    # .count() check right after navigation reads an empty page and always
    # raised NoMessageButtonAvailable, even though the real button appears
    # correctly within a couple seconds. wait_for() waits for the real
    # render instead of an arbitrary sleep, and still fails clearly (same
    # exception) if the button genuinely never shows up.
    message_button = page.locator(_PROFILE_MESSAGE_BUTTON_SELECTOR).first
    try:
        message_button.wait_for(state="visible", timeout=15_000)
    except Exception as exc:  # noqa: BLE001 -- Playwright's TimeoutError, re-raised as our own domain exception
        raise NoMessageButtonAvailable(
            f"{lead.get('business_name') or lead['profile_url']} has no reachable Message button on Instagram."
        ) from exc

    human_delay()
    message_button.click()
    box = page.locator(_COMPOSER_SELECTOR).first
    box.wait_for(state="visible", timeout=10_000)
    human_delay()
    human_type(box, body)
    human_delay()
    page.locator(_SEND_BUTTON_SELECTOR).first.click()
