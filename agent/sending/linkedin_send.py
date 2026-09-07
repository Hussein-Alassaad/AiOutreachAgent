"""
Sends approved messages on LinkedIn via the account's own session.

============================================================================
VERIFIED against real, live LinkedIn pages on 2026-08-03 -- COMPANY path only
============================================================================
Built and inspected using the already-captured real session for "Hussein's
account" -- read-only DOM inspection only (no message was ever actually sent
during this verification; every click below except the final Send was
exercised live, the Send click itself is exactly what a supervised first
real run should confirm, per the same caution already applied to LinkedIn
discovery in Phase 3).

`leads.profile_url` today is always a **company page** URL
(linkedin.com/company/<slug>/) -- that's the only shape discovery/linkedin.py
produces. This module also handles a **person** profile URL
(linkedin.com/in/<username>/) for whenever a future lead's profile_url is a
specific person instead (e.g. a resolved founder profile) -- see the
"PERSON path" section below for exactly what is and isn't verified there.

COMPANY path (fully verified): some company Pages opt in to a "Message"
action button (`data-test-message-page-button` in
`.org-top-card-primary-actions`) that opens a distinct modal -- LinkedIn's
Page inbox, not the personal inbox. Confirmed this is genuinely inconsistent
between pages, not a universal feature: a real search for "bakery" in
Lebanon (the same search discovery/linkedin.py runs) found it present on a
small business (Paul Bakery Beirut, 135 followers) but absent on a large
brand (Nike). Whether a given lead has it is discovered live, per lead, not
assumed.

That modal (verified via its real DOM, not guessed):
  - `div[role='dialog'][aria-labelledby='msg-shared-modals-msg-page-modal']`
  - a REQUIRED "Conversation topic" `<select>` with a real, stable (non
    ember-generated) id -- options are Service request / Request a demo /
    Support / Careers / Other. None of these are literally "cold outreach",
    so "Other" is used deliberately (see _TOPIC_URN below) rather than
    picking a topic that misrepresents why we're messaging.
  - a message `<textarea>` with a real stable id, `maxlength="750"` and a
    client-enforced "Minimum 25 characters" hint -- both checked before
    sending rather than letting LinkedIn silently reject a too-short or
    too-long message.
  - a "Send message" button, disabled until the two fields above are valid.

============================================================================
PERSON path -- PARTIALLY verified, honestly flagged
============================================================================
"Hussein's account" has 0 connections today, so there was no real person
profile to click "Message" on and inspect live -- unlike every other
UNVERIFIED note elsewhere in this codebase, this one isn't blocked on
login, it's blocked on having a genuine connection to test against.

What IS verified live (on linkedin.com/messaging/thread/new/, LinkedIn's
own full-page compose, reached without needing a specific person): the
shared messaging widget's contenteditable box is
`div.msg-form__contenteditable[contenteditable=true]` and its submit button
is `button.msg-form__send-button` -- both real, stable, non-ember-generated
classes. This widget is the same one LinkedIn embeds site-wide (it's what
renders inside the persistent bottom-right chat overlay too), so reusing
these two selectors after clicking a person's own "Message" button is a
reasonable inference, not a blind guess.

What is NOT verified: the profile page's own "Message" button selector.
`button[aria-label^='Message ']` is used below because the company path's
real button used exactly that aria-label convention
(`aria-label="Message Paul Bakery Beirut"`) -- a11y labels tend to be
consistent site-wide, unlike CSS classes -- but this has not been confirmed
against a real person profile. **The first time a person-shaped lead
actually reaches this code path, watch it closely** before trusting it
unattended, same as the company path's first real send.
============================================================================
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

# Reuses linkedin_reply_check.py's already-live-verified thread-opening
# selectors (see that module's own docstring for the real DOM these were
# confirmed against) -- a reply is delivered INTO the lead's existing
# conversation thread, not via the company/person "Message" button flow
# above (which opens a fresh connection request / new thread instead).
from agent.sending.linkedin_reply_check import (
    _CONVERSATION_LIST_ITEM_SELECTOR,
    LINKEDIN_MESSAGING_URL,
    LINKEDIN_FEED_URL,
    SessionLoggedOut,
    _raise_if_logged_out,
)

_THREAD_CONTENTEDITABLE_SELECTOR = "div.msg-form__contenteditable[contenteditable=true]"
_THREAD_SEND_BUTTON_SELECTOR = "button.msg-form__send-button"
# NOT yet live-verified -- LinkedIn's messaging widget attach/media button,
# inferred from its aria-label convention (same reasoning as
# _PERSON_MESSAGE_BUTTON_SELECTOR above). Watch closely on first real use.
_THREAD_ATTACHMENT_BUTTON_SELECTOR = "button[aria-label*='attach' i], button[aria-label*='media' i]"


class NoExistingThread(RuntimeError):
    """
    Raised when a reply is queued for a lead with no existing LinkedIn
    conversation to reply into -- shouldn't happen in practice (a reply only
    ever gets created after the lead already messaged us, see
    src/lib/actions/outreach-replies.ts's sendReplyAction()), but a real
    account-side edge case (thread archived/deleted on LinkedIn's side
    between the reply detection and this send) is possible, so this is a
    normal "couldn't send" outcome, not a crash -- same treatment as
    NoMessageButtonAvailable above.
    """

# The company-page message modal requires picking one of a fixed set of
# topics (real values scraped from the live <select>, see module docstring).
# "Other" is the only one that doesn't misrepresent unsolicited outreach as
# a support ticket, a demo request, a careers inquiry, etc.
_TOPIC_URN = "urn:li:fsd_pageMailboxConversationTopic:7"  # "Other"

_COMPANY_MESSAGE_MIN_LENGTH = 25
_COMPANY_MESSAGE_MAX_LENGTH = 750

_COMPANY_MESSAGE_BUTTON_SELECTOR = "div.org-top-card-primary-actions [data-test-message-page-button]"
_COMPANY_MODAL_SELECTOR = "div[role='dialog'][aria-labelledby='msg-shared-modals-msg-page-modal']"
_COMPANY_TOPIC_SELECT_SELECTOR = "select#msg-shared-modals-msg-page-modal-presenter-conversation-topic"
_COMPANY_TEXTAREA_SELECTOR = "textarea#org-message-page-modal-message"
_COMPANY_SEND_BUTTON_SELECTOR = "div.artdeco-modal__actionbar button"

# PERSON path -- see module docstring for exactly what is/isn't verified.
# LIVE-CONFIRMED 2026-09-03: a real, live test (a genuinely 1st-degree-
# connected profile) found LinkedIn renders "Message" as a plain
# <a href="/messaging/compose/?profileUrn=...&interop=msgOverlay"> link on
# a personal profile's top card, NOT the <button aria-label="Message ...">
# the original selector assumed -- that button-only selector matched zero
# elements even on a genuinely connected, messageable profile, so this had
# never actually worked for ANY real personal-profile send, connected or
# not. The `interop=msgOverlay` query param confirms this link is meant to
# open the same inline messaging overlay a button click would (not a full
# page navigation), so both element types lead to the same contenteditable
# box below -- the comma-separated selector below matches either shape
# LinkedIn might render, whichever this profile's real page uses.
_PERSON_MESSAGE_BUTTON_SELECTOR = "button[aria-label^='Message '], a[href*='/messaging/compose/']"
_PERSON_CONTENTEDITABLE_SELECTOR = "div.msg-form__contenteditable[contenteditable=true]"
_PERSON_SEND_BUTTON_SELECTOR = "button.msg-form__send-button"


class NoMessageButtonAvailable(RuntimeError):
    """
    Raised when a lead's LinkedIn page (company or person) has no "Message"
    action enabled -- not every company Page opts into the Page inbox, and
    not every person is messageable without a connection/InMail. A
    legitimate, expected outcome for some leads, not a bug. Callers should
    treat it like whatsapp_send.py's WhatsAppNotConfigured: a normal
    "can't send this way" result, not a crash.
    """


class MessageLengthInvalid(RuntimeError):
    """
    Raised when the approved message body doesn't fit the company Page
    inbox's constraints (25-750 characters, confirmed live). Only applies
    to the company path -- person-to-person messaging showed no such limit
    when inspected. Catching this before typing anything is better than
    finding out mid-send that LinkedIn silently refused to enable Send.
    """


def _is_company_page(profile_url: str) -> bool:
    return "linkedin.com/company/" in profile_url


def _is_person_profile(profile_url: str) -> bool:
    return "linkedin.com/in/" in profile_url


_VIEWING_SETTING_MODAL_SELECTOR = "[data-test-modal-id='org-page-viewing-setting-modal']"


def _send_to_company(page: Page, lead: dict, body: str) -> None:
    if not (_COMPANY_MESSAGE_MIN_LENGTH <= len(body) <= _COMPANY_MESSAGE_MAX_LENGTH):
        raise MessageLengthInvalid(
            f"Message is {len(body)} characters; LinkedIn's Page inbox requires "
            f"{_COMPANY_MESSAGE_MIN_LENGTH}-{_COMPANY_MESSAGE_MAX_LENGTH}."
        )

    # LIVE-CONFIRMED 2026-09-07: a genuinely unrelated LinkedIn-native
    # popup ("Choose what others see when you've viewed their profile" --
    # data-test-modal-id="org-page-viewing-setting-modal") can appear on a
    # company page visit and sits on top of the Message button, blocking
    # every click attempt with a real, reproducible "<div ...> subtree
    # intercepts pointer events" error -- confirmed via the exact overlay
    # id in Playwright's own actionability log, not a guess. Not present
    # on every visit (likely tied to account-level viewing-mode settings
    # never having been explicitly set), so this is a no-op when the modal
    # isn't there.
    #
    # LIVE-CONFIRMED 2026-09-07 (second finding, same session): an instant
    # .count() check right here reads 0 even when the modal goes on to
    # block the click moments later -- same class of race as every other
    # "checked before the real render finished" bug found tonight
    # (instagram_send.py's message button, its composer, its Send button).
    # A short wait_for(state="visible") catches it once actually rendered
    # without slowing down the common case (the wait exhausts quickly and
    # silently when the modal genuinely never appears).
    viewing_modal = page.locator(_VIEWING_SETTING_MODAL_SELECTOR)
    try:
        viewing_modal.wait_for(state="visible", timeout=3_000)
        modal_present = True
    except Exception:  # noqa: BLE001 -- Playwright's TimeoutError means the modal never showed, the common case
        modal_present = False
    if modal_present:
        dismiss = viewing_modal.locator("button[aria-label='Dismiss'], button.artdeco-modal__dismiss").first
        if dismiss.count() > 0:
            dismiss.click()
        else:
            page.keyboard.press("Escape")
        page.locator(_VIEWING_SETTING_MODAL_SELECTOR).wait_for(state="hidden", timeout=5_000)

    message_button = page.locator(_COMPANY_MESSAGE_BUTTON_SELECTOR).first
    if message_button.count() == 0:
        raise NoMessageButtonAvailable(
            f"{lead.get('business_name') or lead['profile_url']} has no "
            "Message button enabled on its LinkedIn company page."
        )

    # Human-scale pacing before every platform-visible action -- an instant
    # click/fill the moment the page loads, or a body typed in one atomic
    # DOM write, is itself a detectable automation signal (see
    # agent/core/pacing.py's module docstring).
    human_delay()
    message_button.click()
    page.locator(_COMPANY_MODAL_SELECTOR).wait_for(state="visible", timeout=10_000)
    human_delay()
    page.locator(_COMPANY_TOPIC_SELECT_SELECTOR).select_option(value=_TOPIC_URN)
    human_delay()
    human_type(page.locator(_COMPANY_TEXTAREA_SELECTOR), body)
    human_delay()
    page.locator(_COMPANY_SEND_BUTTON_SELECTOR).click()


def _send_to_person(page: Page, lead: dict, body: str) -> None:
    message_button = page.locator(_PERSON_MESSAGE_BUTTON_SELECTOR).first
    if message_button.count() == 0:
        raise NoMessageButtonAvailable(
            f"{lead.get('business_name') or lead['profile_url']} has no "
            "reachable Message button (not connected, no open profile/InMail)."
        )

    human_delay()
    # LIVE-CONFIRMED 2026-09-03, five real, distinct findings from the same
    # test session, in the order discovered:
    # (1) a plain click first timed out with "<p ...> subtree intercepts
    #     pointer events" -- some other element sits on top of the Message
    #     link at the click point (an unidentified sticky/overlay piece;
    #     its exact class hash is LinkedIn-generated and shifts per
    #     page-load, not worth chasing by name).
    # (2) click(force=True) bypassed the interception with no Playwright
    #     error, but genuinely opened nothing -- LinkedIn's own frontend
    #     intercepts this link's click via JS, and that handler needs a
    #     real, trusted click a forced click on an occluded element
    #     doesn't reliably deliver.
    # (3) a normal click, given time to settle first, DID eventually land
    #     and DID trigger a real navigation toward linkedin.com's
    #     Messaging inbox -- but Playwright's own actionability retry loop
    #     inside .click() (repeatedly re-checking "is this element
    #     visible/stable" while the intercepting element kept re-covering
    #     it) was still running WHILE that navigation was already firing
    #     underneath it -- by the time .click() finally "succeeded", the
    #     original link element had already been detached from the DOM
    #     ("element was detached from the DOM, retrying"), because the
    #     page had already moved on.
    # (4) skipping the click entirely and page.goto()-ing the link's own
    #     href directly was tried next -- but LinkedIn redirected that
    #     bare navigation to the plain homepage instead of opening the
    #     compose view. This confirmed the URL isn't a real bookmarkable
    #     destination: it depends on being triggered by an actual in-page
    #     click, carrying live session/page context a cold page.goto()
    #     doesn't have.
    # (5) Real fix: a genuine mouse-coordinate click via Playwright's
    #     page.mouse (real x/y position, real down+up events) -- this is
    #     what Chrome actually treats as user-trusted input (unlike a
    #     forced DOM-level click), so LinkedIn's own click handler fires
    #     correctly, while ALSO not going through Playwright's element-
    #     locator actionability retry loop that caused the race in (3).
    #     wait_for(state="attached") first (not "visible", which re-enters
    #     the same actionability checking this is deliberately avoiding)
    #     confirms the element genuinely exists before reading its
    #     position.
    message_button.wait_for(state="attached", timeout=10_000)
    message_button.scroll_into_view_if_needed()
    page.wait_for_timeout(1_500)
    box_rect = message_button.bounding_box()
    if not box_rect:
        raise NoMessageButtonAvailable(
            f"{lead.get('business_name') or lead['profile_url']} has a Message "
            "element with no visible position to click."
        )
    click_x = box_rect["x"] + box_rect["width"] / 2
    click_y = box_rect["y"] + box_rect["height"] / 2
    page.mouse.move(click_x, click_y)
    page.wait_for_timeout(200)
    page.mouse.down()
    page.wait_for_timeout(80)
    page.mouse.up()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10_000)
    except Exception:  # noqa: BLE001 -- no real navigation fired; the box may already be on this page (inline overlay case)
        pass
    box = page.locator(_PERSON_CONTENTEDITABLE_SELECTOR).first
    box.wait_for(state="visible", timeout=15_000)
    human_delay()
    human_type(box, body)
    human_delay()
    page.locator(_PERSON_SEND_BUTTON_SELECTOR).first.click()


def send_message(message: dict) -> dict:
    """
    Send one approved outreach message via the lead's own LinkedIn page --
    company or person, detected from profile_url's shape -- using the
    account that discovered it. Mirrors sending/whatsapp_send.py's
    send_message(): records send_status/sent_at, sent_via_account,
    contact_count/first_contacted_at, the pipeline move, and client_history,
    so a LinkedIn send leaves the identical trail every other channel does.

    Raises NoMessageButtonAvailable if this specific lead isn't reachable
    this way -- the caller (scheduler.run_sending_cycle()) already turns
    any exception here into a normal "ok": False result, exactly like a
    missing WhatsApp number does for that channel.
    """
    lead = repo.get_lead(message["lead_id"])
    if not lead or not lead.get("profile_url"):
        raise ValueError(f"Message {message['id']} has no lead profile_url to send to.")

    profile_url = lead["profile_url"]
    if _is_company_page(profile_url):
        send_fn = _send_to_company
    elif _is_person_profile(profile_url):
        send_fn = _send_to_person
    else:
        raise ValueError(f"Lead {lead['id']}'s profile_url isn't a recognised LinkedIn company or person URL: {profile_url}")

    body = approval.active_body(message)

    account = repo.get_account(lead["account_id"])
    if not account:
        raise ValueError(f"Lead {lead['id']} has no owning account to send from.")

    with SessionManager() as sessions:
        # ProxyIpMismatch propagates straight out of open() here, uncaught --
        # exactly the right behavior: the caller (scheduler.run_sending_cycle())
        # already turns any exception from this function into a normal
        # "ok": False result (see this function's own docstring), the same
        # treatment NoMessageButtonAvailable already gets, so a real send
        # attempt never proceeds on an account whose proxy resolved to an
        # unexpected IP.
        context, page, new_verified_ip = sessions.open(account)
        if new_verified_ip and not account.get("verified_proxy_ip"):
            repo.update_account(account["id"], {"verified_proxy_ip": new_verified_ip})
        try:
            # RE-VERIFIED 2026-08-03: default wait_until="load" caused real,
            # reproducible timeouts elsewhere in this codebase that day
            # (discovery/linkedin.py's search/profile navigation) -- LinkedIn
            # is heavy enough that waiting for every resource, not just the
            # DOM, routinely exceeded 15s. Applied the same fix here
            # pre-emptively, before this path's own first live send hits it.
            page.goto(profile_url, timeout=30_000, wait_until="domcontentloaded")
            _raise_if_logged_out(page, account)
            send_fn(page, lead, body)
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
    Delivers a tenant-written reply (message["is_reply"] == True, created by
    src/lib/actions/outreach-replies.ts's sendReplyAction()) INTO the lead's
    existing LinkedIn conversation thread -- NOT via _send_to_company/
    _send_to_person above, which both open a fresh connection request /
    company-page inbox modal instead of continuing an existing thread.

    Reuses linkedin_reply_check.py's thread-finding approach (match the
    conversation list by the lead's business_name) since that selector set
    is already live-verified against a real LinkedIn inbox -- see that
    module's docstring. The reply-composer selectors below
    (msg-form__contenteditable / msg-form__send-button) are the SAME shared
    LinkedIn messaging widget send_message()'s PERSON path already uses
    (see _PERSON_CONTENTEDITABLE_SELECTOR/_PERSON_SEND_BUTTON_SELECTOR
    above) -- reused here under a separate name since this path opens the
    thread differently (via the inbox list, not a profile's Message
    button), even though the widget itself is identical once open.

    Called by scheduler.py's run_reply_send_cycle() -- a fast poll (~every
    2-3 min), separate from the once-daily run_sending_cycle() above, so a
    reply feels close to real-time. Mirrors send_message()'s own
    bookkeeping (send_status/sent_at/sent_via_account) but deliberately
    does NOT call pipeline.move_stage() or contact_count/first_contacted_at
    -- those already happened when the ORIGINAL outbound message was sent;
    a reply to an ongoing conversation isn't a new first contact.

    If message["attachment_url"] is set (a photo/video/voice note attached
    from "Reply Here" -- see src/lib/outreach/reply-attachments.ts), it's
    downloaded to a temp file (sending/attachments.py) and attached via
    LinkedIn's own file-picker button BEFORE typing the body, so the
    attachment preview is visible before Send is clicked, matching how a
    real person would compose the message. Attachment-picker selector is
    NOT yet live-verified -- same caveat as every other new DOM interaction
    this session.
    """
    lead = repo.get_lead(message["lead_id"])
    if not lead or not lead.get("profile_url"):
        raise ValueError(f"Message {message['id']} has no lead profile_url to send to.")

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

        with SessionManager() as sessions:
            context, page, new_verified_ip = sessions.open(account)
            if new_verified_ip and not account.get("verified_proxy_ip"):
                repo.update_account(account["id"], {"verified_proxy_ip": new_verified_ip})
            try:
                # Real, likely-contributing factor found 2026-09-07: every
                # send_reply() attempt (both LinkedIn and Instagram) lost
                # its session specifically at the messaging/inbox
                # navigation tonight -- send_message()'s very similar code
                # above never did, and its navigation target is a public
                # profile/company page, not straight into messaging. See
                # instagram_send.py's send_reply() for the fuller
                # reasoning; applied identically here since both platforms
                # showed the same pattern the same night. NOT yet
                # live-verified as an actual fix -- every account available
                # was already degraded by the time this was written.
                page.goto(LINKEDIN_FEED_URL, timeout=30_000, wait_until="domcontentloaded")
                _raise_if_logged_out(page, account)
                human_delay(1.5, 3.5)
                page.goto(LINKEDIN_MESSAGING_URL, timeout=30_000, wait_until="domcontentloaded")
                _raise_if_logged_out(page, account)
                item = page.locator(_CONVERSATION_LIST_ITEM_SELECTOR, has_text=business_name).first
                if item.count() == 0:
                    raise NoExistingThread(
                        f"No existing LinkedIn conversation found for {business_name or lead['profile_url']}."
                    )
                human_delay()
                item.click()

                box = page.locator(_THREAD_CONTENTEDITABLE_SELECTOR).first
                box.wait_for(state="visible", timeout=10_000)

                if attachment_path:
                    human_delay()
                    with page.expect_file_chooser() as fc_info:
                        page.locator(_THREAD_ATTACHMENT_BUTTON_SELECTOR).first.click()
                    fc_info.value.set_files(str(attachment_path))
                    human_delay()

                if body:
                    human_delay()
                    human_type(box, body)
                human_delay()
                page.locator(_THREAD_SEND_BUTTON_SELECTOR).first.click()
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
