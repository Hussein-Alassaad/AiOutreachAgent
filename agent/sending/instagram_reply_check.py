"""
Checks Instagram for replies to leads we've messaged, via a real logged-in
browser session -- Instagram's equivalent of linkedin_reply_check.py.

============================================================================
LIVE-VERIFIED 2026-09-07 against a real conversation with a genuine
incoming reply ("Thanks", from hussein._.alassaad) -- the ORIGINAL
selectors below were all wrong and have been replaced; see each constant's
own comment for exactly what real inspection found.
============================================================================

APPROACH: pull-based, identical shape to linkedin_reply_check.py -- for
every lead currently at "contacted" that we reached via Instagram, open the
account's own DM inbox, find that lead's conversation thread by its
business_name, and check whether the newest message's sender is the lead
(not us). Reuses instagram_send.py's _CONVERSATION_LIST_ITEM_SELECTOR
constant so the two modules can't silently drift out of sync on how a
thread is located.
"""

from __future__ import annotations

import datetime as dt

from playwright.sync_api import Page

from agent.core.pacing import human_delay
from agent.core.session import ProxyIpMismatch, SessionManager
from agent.crm.reply_detection import handle_reply_detected
from agent.db import repositories as repo
from agent.sending.instagram_send import (
    INSTAGRAM_INBOX_URL,
    CONVERSATION_LIST_ITEM_SELECTOR,
    _raise_if_logged_out,
    SessionLoggedOut,
)

# LIVE-CONFIRMED 2026-09-07: the original div[role='row'] selector matched
# ZERO elements in a real open thread -- Instagram's message bubbles carry
# no ARIA role at all, and there's no labeled/roled container wrapping the
# message list either (walked every ancestor from a real reply's text node
# to the document root: exactly one had any role/aria-label at all, a
# role='button' hover target on the bubble itself, not a list container --
# confirmed live, not assumed). Each message bubble (both incoming and
# outgoing) IS a div[role='presentation'], confirmed by finding they line
# up 1:1 with the visible message bubbles in chronological order.
#
# The one real wrinkle: the FIRST role='presentation' match on the page is
# sometimes unrelated sidebar chrome ("What's new... Your note"), not a
# message -- present only when that inbox-wide prompt hasn't been
# dismissed. Since there's no clean container to scope into instead, this
# is filtered by content instead of position: an element whose direct text
# is exactly that sidebar prompt's own copy is excluded, everything else
# role='presentation' on the page is treated as a message bubble. Scoped
# to whatever the caller navigated to (a specific /direct/t/<id>/ thread
# URL), not the inbox list page, so this never picks up unrelated
# role='presentation' elements from a different part of the app.
_SIDEBAR_PROMPT_TEXT = "What's new"
_THREAD_MESSAGE_SELECTOR = "div[role='presentation']"
_THREAD_MESSAGE_BODY_SELECTOR = "div[dir='auto']"


def _has_instagram_sent(lead_id: str) -> bool:
    return any(
        m.get("channel") == "instagram" and m.get("send_status") == "sent"
        for m in repo.messages_for_lead(lead_id)
    )


def _open_thread_for_lead(page: Page, account: dict, business_name: str) -> bool:
    """
    account is required (not just page) so a genuinely logged-out session
    is detected and persisted the same way instagram_send.py's send paths
    already do -- LIVE-CONFIRMED 2026-09-07: before this, a logged-out
    session made _open_thread_for_lead silently return False (the
    conversation list item just never "found"), which reported an
    identical "replied": False result as a lead that genuinely hasn't
    replied yet -- a real false negative with no visible error, exactly
    the failure mode a human scanning "not yet replied" against their own
    real inbox (see this codebase's own Reply Here warning banner) exists
    to catch, but silently, indefinitely, is a much worse outcome than
    surfacing it as a real error the moment it happens.

    LIVE-CONFIRMED 2026-09-07, second fix: an instant .count() check
    right after page.goto() reads 0 even when the conversation genuinely
    exists and renders moments later -- same timing race found and fixed
    in every other Instagram/LinkedIn selector tonight. wait_for() catches
    it once actually rendered.
    """
    page.goto(INSTAGRAM_INBOX_URL, timeout=30_000, wait_until="domcontentloaded")
    _raise_if_logged_out(page, account)
    item = page.locator(CONVERSATION_LIST_ITEM_SELECTOR, has_text=business_name).first
    try:
        item.wait_for(state="visible", timeout=10_000)
    except Exception:  # noqa: BLE001 -- Playwright's TimeoutError means no matching thread exists, a real "no" not a crash
        return False
    human_delay()
    item.click()
    # LIVE-CONFIRMED 2026-09-07, third fix in this function: clicking the
    # conversation updates an in-page panel rather than navigating (page.url
    # stays on /direct/inbox/ throughout -- confirmed live), so there's no
    # navigation event to wait on, and human_delay() alone returned before
    # the thread's own messages had rendered. Reading the messages at that
    # point found only stale/empty content and reported "no reply" for a
    # thread that genuinely had one -- the exact false negative this whole
    # function exists to avoid. A real settle wait here is what actually
    # makes the read see the conversation that just opened.
    page.wait_for_timeout(3_000)
    return True


def _newest_message_if_from_lead(page: Page) -> str | None:
    """
    Reads the thread's most recent message and returns its body only if it
    was NOT sent by us. LIVE-CONFIRMED 2026-09-07 against a real thread
    with a genuine incoming reply: outgoing (our own) bubbles render
    right-aligned with a visibly larger left offset than incoming ones --
    confirmed via getBoundingClientRect() on both a real outgoing and a
    real incoming bubble in the same thread (our own: left=762; the
    lead's reply: left=523, same viewport). A fixed pixel threshold is
    fragile across viewport widths, so this compares each bubble's left
    offset against the THREAD's own average instead -- outgoing bubbles
    sit further right than the thread's own center of mass, incoming ones
    sit further left, which holds regardless of absolute viewport size.
    """
    messages = page.locator(_THREAD_MESSAGE_SELECTOR)
    count = messages.count()
    if count == 0:
        return None

    boxes = []
    for i in range(count):
        el = messages.nth(i)
        text = (el.text_content(timeout=2_000) or "").strip()
        if not text or text.startswith(_SIDEBAR_PROMPT_TEXT):
            continue
        box = el.bounding_box()
        if box:
            boxes.append({"index": i, "left": box["x"], "text": text})

    if not boxes:
        return None

    avg_left = sum(b["left"] for b in boxes) / len(boxes)
    newest = boxes[-1]
    is_outgoing = newest["left"] > avg_left
    if is_outgoing:
        return None  # our own message

    return newest["text"] or None


def check_instagram_replies() -> list[dict]:
    """
    Checks both "contacted" leads (never replied yet) AND "replied" leads
    (an ongoing back-and-forth) reached via Instagram, opens the owning
    account's DM inbox, and records the lead's newest message if it's both
    from the lead AND genuinely new -- not the same reply already recorded
    from a previous run.

    Real gap fixed 2026-09-07: this originally only ever checked
    "contacted" leads, on the assumption that one reply ends the detection
    cycle for that lead -- true for the FIRST reply (which is what moves a
    lead onto "replied" in the first place), but wrong for any reply after
    that: a lead who's already "replied" and sends a second message in the
    same conversation was invisible to every future run, silently, since
    "replied" leads were never even looked at again. Now dedup happens by
    CONTENT (compare the newest message against the most recently recorded
    reply for this lead, repo.replies_for_lead()'s own ordering) instead of
    by lead STATUS, so a genuinely new message in an ongoing conversation
    is caught, while the same already-recorded reply read again on a later
    run is correctly skipped rather than inserted twice.
    """
    results = []
    leads = [
        lead for lead in repo.leads_by_status("contacted") + repo.leads_by_status("replied")
        if lead.get("platform") == "instagram"
    ]
    if not leads:
        return results

    accounts_by_id = {}
    with SessionManager() as sessions:
        for lead in leads:
            if not _has_instagram_sent(lead["id"]):
                continue

            business_name = lead.get("business_name") or ""
            account = accounts_by_id.get(lead["account_id"])
            if account is None:
                account = repo.get_account(lead["account_id"])
                accounts_by_id[lead["account_id"]] = account
            if not account:
                continue

            try:
                context, page, new_verified_ip = sessions.open(account)
            except ProxyIpMismatch as exc:
                try:
                    repo.insert_error({
                        "stage": "proxy_ip_mismatch", "channel": "instagram",
                        "account_id": account["id"], "error_message": str(exc), "is_expected": False,
                    })
                except Exception:  # noqa: BLE001 -- logging itself must never crash this run
                    pass
                results.append({"lead_id": lead["id"], "replied": False, "error": str(exc)})
                continue
            if new_verified_ip and not account.get("verified_proxy_ip"):
                repo.update_account(account["id"], {"verified_proxy_ip": new_verified_ip})
                account["verified_proxy_ip"] = new_verified_ip
            try:
                found = _open_thread_for_lead(page, account, business_name)
                body = _newest_message_if_from_lead(page) if found else None
            except SessionLoggedOut as exc:
                # login_status is already persisted "failed" by
                # _raise_if_logged_out itself -- record this as a real
                # error, not a silent "no reply" (see _open_thread_for_lead's
                # own docstring for why that distinction matters).
                results.append({"lead_id": lead["id"], "replied": False, "error": str(exc)})
                continue
            finally:
                sessions.close(account["id"], context)

            if body is None:
                results.append({"lead_id": lead["id"], "replied": False})
                continue

            # Dedup by CONTENT, not by lead status -- see this function's
            # own docstring for why. replies_for_lead() already orders by
            # replied_at (see its own repo definition), so [-1] is the most
            # recently recorded reply, if any.
            existing = repo.replies_for_lead(lead["id"])
            already_recorded = bool(existing) and existing[-1].get("body") == body
            replied = not already_recorded
            if replied:
                handle_reply_detected(
                    lead["id"],
                    channel="instagram",
                    body=body,
                    replied_at=dt.datetime.now(dt.timezone.utc),
                    account_id=account["id"],
                )
            results.append({"lead_id": lead["id"], "replied": replied})

    return results
