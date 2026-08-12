"""
Checks LinkedIn for replies to leads we've messaged, via a real logged-in
browser session -- the piece crm/reply_detection.py's module docstring
explicitly deferred until linkedin_send.py's own live-account access existed.

============================================================================
RE-VERIFIED LIVE 2026-08-08 against Hussein's real messaging inbox, after
two real sends (Paul Bakery Beirut, George Gemayel) -- 3 real, confirmed
bugs found and fixed. No actual reply existed yet to test the "lead
replied" branch itself (both leads had a single message: our own outbound
send) -- see the note on sender-detection below for what that means for
confidence level.
============================================================================
1. `_CONVERSATION_LINK_SELECTOR` assumed an `<a href="...">` to match
   against `profile_url`'s slug. The real element
   (`msg-conversation-listitem__link`) is a plain `<div>` with NO href
   attribute at all -- confirmed by inspecting the raw captured HTML, not
   guessed. That made every lookup silently fail; `_open_thread_for_lead`
   would return False for every lead, always. Fixed by matching on the
   conversation's visible participant-name text instead (real DOM:
   `h3.msg-conversation-listitem__participant-names` contains the exact
   business name, e.g. "Paul Bakery Beirut" -- confirmed present verbatim
   for both real leads messaged so far) rather than a URL that was never
   there.

2. `_THREAD_MESSAGE_TIMESTAMP_SELECTOR`'s `<time>` element has NO `datetime`
   attribute in the real DOM -- confirmed live, it only contains human text
   ("8:29 PM"). The old code's `get_attribute("datetime")` always returned
   None, so `_newest_message_if_from_lead` always bailed out immediately
   regardless of whether a reply existed. There's no reliable absolute
   timestamp in the DOM at all for a same-day message (LinkedIn shows
   relative/short text, not ISO), so this function now only checks whether
   the newest message's sender is the lead, dropping the
   newer-than-last-send timestamp comparison entirely (see below).

3. The docstring's own claim -- "absence of a sender-link element means
   this is our own message" -- was the wrong way around. The real captured
   thread's ONLY message so far is OUR OWN outbound send, and it DOES carry
   a `msg-s-message-group__profile-link` span with the sender's name
   ("Hussein Alassaad") inside it. LinkedIn appears to label every message
   group's sender, not just the other party's. Fixed by matching the
   sender-name text against the LEAD's business_name specifically, instead
   of inferring identity from whether a link element exists at all.

STILL NOT FULLY VERIFIED: neither test lead has actually replied yet, so
the true positive path (a real reply detected and correctly attributed) has
not been exercised end to end -- only the true-negative path (0 messages
from the lead, correctly reports no reply) has been confirmed live. Watch
this closely against the first real incoming reply and adjust if the
sender-name match needs refining (e.g. LinkedIn abbreviating/truncating a
long business name in the message-group header).

APPROACH: pull-based, same shape as whatsapp_reply_check.py. For every lead
currently at "contacted" that we reached via LinkedIn (not WhatsApp -- that
channel's own checker already covers WhatsApp-contacted leads), open the
account's own messaging inbox, find that lead's conversation thread by its
business_name, and check whether the newest message's sender name matches
the lead (not us) -- if so, that's a reply.

This is real browser automation, not a public API -- the same
automation-detection exposure linkedin_send.py itself already carries.
Nothing here is fired more often than once per shared daily cycle (see
scheduler.py's run_full_pipeline_cycle), matching every other LinkedIn
browser action in this codebase.
============================================================================
"""

from __future__ import annotations

import datetime as dt

from playwright.sync_api import Page

from agent.core.session import SessionManager
from agent.crm.reply_detection import handle_reply_detected
from agent.db import repositories as repo

LINKEDIN_MESSAGING_URL = "https://www.linkedin.com/messaging/"

# RE-VERIFIED live 2026-08-08 against Hussein's real inbox -- see module
# docstring for exactly what changed and why.
_CONVERSATION_LIST_ITEM_SELECTOR = "li.msg-conversation-listitem"
_THREAD_MESSAGE_SELECTOR = "div.msg-s-event-listitem"
_THREAD_MESSAGE_SENDER_SELECTOR = "span.msg-s-message-group__profile-link"
_THREAD_MESSAGE_BODY_SELECTOR = "p.msg-s-event-listitem__body"


def _has_linkedin_sent(lead_id: str) -> bool:
    """
    True if this lead was ever actually LinkedIn-messaged (nothing to check
    a reply against otherwise). Replaces the old _last_linkedin_sent_at()'s
    timestamp value -- see module docstring point 2 for why an absolute
    "newer than X" comparison isn't possible here: the real thread's <time>
    element has no machine-readable datetime attribute, only human text
    ("8:29 PM"), confirmed live 2026-08-08.
    """
    return any(
        m.get("channel") == "linkedin" and m.get("send_status") == "sent"
        for m in repo.messages_for_lead(lead_id)
    )


def _open_thread_for_lead(page: Page, business_name: str) -> bool:
    """
    Finds this lead's conversation in the messaging inbox by matching the
    thread's visible participant-name text against business_name, and opens
    it. Returns False if no matching thread exists yet -- treated as "no
    reply to check" rather than an error, same as a missing WhatsApp number.

    RE-VERIFIED 2026-08-08: previously matched profile_url's slug against
    the conversation link's href -- confirmed live that
    msg-conversation-listitem__link is a plain <div> with NO href attribute
    at all, so that match could never succeed. Real DOM does carry the
    lead's exact business name as visible text
    (h3.msg-conversation-listitem__participant-names), confirmed against
    both real leads messaged so far ("Paul Bakery Beirut", "George Gemayel |
    Bakery Consultancy - Aliments Est." both appeared verbatim) -- matching
    on that instead.
    """
    # Same wait_until="domcontentloaded" fix applied across every other
    # LinkedIn navigation in this codebase 2026-08-03 -- see
    # discovery/linkedin.py's module docstring for the real, reproducible
    # timeouts that "load" caused on LinkedIn's heavy SPA pages.
    page.goto(LINKEDIN_MESSAGING_URL, timeout=30_000, wait_until="domcontentloaded")
    item = page.locator(_CONVERSATION_LIST_ITEM_SELECTOR, has_text=business_name).first
    if item.count() == 0:
        return False
    item.click()
    return True


def _newest_message_if_from_lead(page: Page, business_name: str) -> str | None:
    """
    Reads the thread's most recent message and returns its body only if its
    sender name matches the lead's business_name (a reply), not our own
    account. Returns None if there's no thread or the newest message is
    ours.

    RE-VERIFIED 2026-08-08: the module docstring's original assumption --
    "absence of a sender-link element means this is our own message" -- was
    backwards. The one real message captured live so far (our own outbound
    send to Paul Bakery Beirut) DOES carry a
    span.msg-s-message-group__profile-link with the sender's name
    ("Hussein Alassaad") -- LinkedIn labels every message group's sender,
    not just the other party's. Fixed to match the sender name against the
    lead's business_name specifically, rather than inferring identity from
    element presence alone. The absolute-timestamp comparison this function
    used to also require is gone -- see _has_linkedin_sent()'s docstring for
    why that's no longer available in the real DOM; this now only answers
    "is the newest message from the lead", which check_linkedin_replies()
    combines with the replies table to avoid re-flagging the same reply
    twice (see there).
    """
    messages = page.locator(_THREAD_MESSAGE_SELECTOR)
    count = messages.count()
    if count == 0:
        return None

    newest = messages.nth(count - 1)
    sender = newest.locator(_THREAD_MESSAGE_SENDER_SELECTOR).first
    if sender.count() == 0:
        return None
    sender_name = (sender.text_content(timeout=2_000) or "").strip()
    if sender_name != business_name:
        return None

    body = newest.locator(_THREAD_MESSAGE_BODY_SELECTOR).first.text_content(timeout=2_000) or ""
    return body.strip()


def check_linkedin_replies() -> list[dict]:
    """
    For every "contacted" lead reached via LinkedIn, open the owning
    account's messaging inbox and check whether the lead's own most recent
    message is a reply (sender name matches the lead, not us). Meant to run
    on the same cadence as whatsapp_reply_check.check_whatsapp_replies()
    (see scheduler.run_full_pipeline_cycle).

    Dedup note: once handle_reply_detected() fires, pipeline.move_stage()
    moves the lead from "contacted" to "replied" -- this function only ever
    looks at "contacted" leads, so a lead that already had its reply
    recorded naturally drops out of future runs' candidate list on its own.
    No separate "have we already recorded this reply" check is needed here.

    Returns one result dict per lead actually checked -- leads never
    LinkedIn-messaged, or with no matching thread found, are skipped.
    """
    results = []
    leads = [
        lead for lead in repo.leads_by_status("contacted")
        if lead.get("platform") == "linkedin"
    ]
    if not leads:
        return results

    accounts_by_id = {}
    with SessionManager() as sessions:
        for lead in leads:
            if not _has_linkedin_sent(lead["id"]):
                continue

            business_name = lead.get("business_name") or ""
            account = accounts_by_id.get(lead["account_id"])
            if account is None:
                account = repo.get_account(lead["account_id"])
                accounts_by_id[lead["account_id"]] = account
            if not account:
                continue

            context, page = sessions.open(account)
            try:
                found = _open_thread_for_lead(page, business_name)
                body = _newest_message_if_from_lead(page, business_name) if found else None
            finally:
                sessions.close(account["id"], context)

            replied = body is not None
            if replied:
                handle_reply_detected(
                    lead["id"],
                    channel="linkedin",
                    body=body,
                    replied_at=dt.datetime.now(dt.timezone.utc),
                    account_id=account["id"],
                )
            results.append({"lead_id": lead["id"], "replied": replied})

    return results
