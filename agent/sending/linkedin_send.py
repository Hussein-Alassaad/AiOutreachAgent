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

from agent.core.session import SessionManager
from agent.crm import pipeline
from agent.db import repositories as repo
from agent.messaging import approval

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
_PERSON_MESSAGE_BUTTON_SELECTOR = "button[aria-label^='Message ']"
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


def _send_to_company(page: Page, lead: dict, body: str) -> None:
    if not (_COMPANY_MESSAGE_MIN_LENGTH <= len(body) <= _COMPANY_MESSAGE_MAX_LENGTH):
        raise MessageLengthInvalid(
            f"Message is {len(body)} characters; LinkedIn's Page inbox requires "
            f"{_COMPANY_MESSAGE_MIN_LENGTH}-{_COMPANY_MESSAGE_MAX_LENGTH}."
        )

    message_button = page.locator(_COMPANY_MESSAGE_BUTTON_SELECTOR).first
    if message_button.count() == 0:
        raise NoMessageButtonAvailable(
            f"{lead.get('business_name') or lead['profile_url']} has no "
            "Message button enabled on its LinkedIn company page."
        )

    message_button.click()
    page.locator(_COMPANY_MODAL_SELECTOR).wait_for(state="visible", timeout=10_000)
    page.locator(_COMPANY_TOPIC_SELECT_SELECTOR).select_option(value=_TOPIC_URN)
    page.locator(_COMPANY_TEXTAREA_SELECTOR).fill(body)
    page.locator(_COMPANY_SEND_BUTTON_SELECTOR).click()


def _send_to_person(page: Page, lead: dict, body: str) -> None:
    message_button = page.locator(_PERSON_MESSAGE_BUTTON_SELECTOR).first
    if message_button.count() == 0:
        raise NoMessageButtonAvailable(
            f"{lead.get('business_name') or lead['profile_url']} has no "
            "reachable Message button (not connected, no open profile/InMail)."
        )

    message_button.click()
    box = page.locator(_PERSON_CONTENTEDITABLE_SELECTOR).first
    box.wait_for(state="visible", timeout=10_000)
    box.fill(body)
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
        context, page = sessions.open(account)
        try:
            # RE-VERIFIED 2026-08-03: default wait_until="load" caused real,
            # reproducible timeouts elsewhere in this codebase that day
            # (discovery/linkedin.py's search/profile navigation) -- LinkedIn
            # is heavy enough that waiting for every resource, not just the
            # DOM, routinely exceeded 15s. Applied the same fix here
            # pre-emptively, before this path's own first live send hits it.
            page.goto(profile_url, timeout=30_000, wait_until="domcontentloaded")
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
