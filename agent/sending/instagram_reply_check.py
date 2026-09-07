"""
Checks Instagram for replies to leads we've messaged, via a real logged-in
browser session -- Instagram's equivalent of linkedin_reply_check.py.

============================================================================
LIVE-VERIFIED 2026-09-07 against a real conversation with a genuine
incoming reply ("Thanks", from hussein._.alassaad) -- the ORIGINAL
selectors below were all wrong and have been replaced; see each constant's
own comment for exactly what real inspection found.
============================================================================

APPROACH: pull-based -- for every "contacted" OR "replied" lead reached
via Instagram, open the account's own DM inbox, find that lead's thread by
its business_name, and read the ENTIRE thread (not just the newest
message -- see _sync_thread_messages()'s own docstring for the real gap
that fixed, 2026-09-07). Every message not already recorded is backfilled
into the correct table by direction: a lead's message becomes a new
OutreachReply row (and advances the pipeline via handle_reply_detected()),
our own message (including one sent manually from the real Instagram app,
outside this platform entirely) becomes a new OutreachMessage row -- so
Reply Here shows the REAL, complete conversation regardless of how each
message was actually sent. Reuses instagram_send.py's
_CONVERSATION_LIST_ITEM_SELECTOR constant so the two modules can't
silently drift out of sync on how a thread is located.
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


def _read_thread_messages(page: Page) -> list[dict]:
    """
    Reads EVERY message bubble currently in the open thread, in
    chronological order, each tagged "us" or "lead". Real gap fixed
    2026-09-07: the original version of this function only ever looked at
    the SINGLE NEWEST message -- live-confirmed the same night, a message
    sent manually from the real Instagram app (not through this platform)
    was correctly excluded from outreach_replies (it's genuinely ours, not
    a reply), but that also meant it never showed up anywhere on the
    dashboard at all -- Reply Here only ever displays OutreachMessage rows
    (platform-originated sends) plus OutreachReply rows (detected incoming
    replies), so a real, genuine part of the conversation was invisible.
    Reading the WHOLE thread, not just the tail, is what lets the caller
    backfill a manually-sent outgoing message the same way it already
    backfills an incoming reply.

    Direction is inferred from horizontal position -- see the previous
    version's own comment for the live-measured evidence (outgoing:
    left=762, incoming: left=523, same viewport) -- compared against the
    THREAD's own average left offset rather than a fixed pixel threshold,
    so it holds regardless of viewport width.
    """
    messages = page.locator(_THREAD_MESSAGE_SELECTOR)
    count = messages.count()
    if count == 0:
        return []

    boxes = []
    for i in range(count):
        el = messages.nth(i)
        text = (el.text_content(timeout=2_000) or "").strip()
        if not text or text.startswith(_SIDEBAR_PROMPT_TEXT):
            continue
        box = el.bounding_box()
        if box:
            boxes.append({"left": box["x"], "text": text})

    if not boxes:
        return []

    avg_left = sum(b["left"] for b in boxes) / len(boxes)
    return [
        {"from": "us" if b["left"] > avg_left else "lead", "text": b["text"]}
        for b in boxes
    ]


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
                live_messages = _read_thread_messages(page) if found else []
            except SessionLoggedOut as exc:
                # login_status is already persisted "failed" by
                # _raise_if_logged_out itself -- record this as a real
                # error, not a silent "no reply" (see _open_thread_for_lead's
                # own docstring for why that distinction matters).
                results.append({"lead_id": lead["id"], "replied": False, "error": str(exc)})
                continue
            finally:
                sessions.close(account["id"], context)

            if not live_messages:
                results.append({"lead_id": lead["id"], "replied": False})
                continue

            new_replies, new_outgoing = _sync_thread_messages(lead, account, live_messages)
            results.append({
                "lead_id": lead["id"],
                "replied": new_replies > 0,
                "new_replies": new_replies,
                "new_outgoing_backfilled": new_outgoing,
            })

    return results


def _sync_thread_messages(lead: dict, account: dict, live_messages: list[dict]) -> tuple[int, int]:
    """
    Real gap fixed 2026-09-07: Reply Here only ever displayed
    OutreachMessage rows (platform-originated sends) and OutreachReply
    rows (detected incoming replies) -- a message sent manually from the
    real Instagram app, outside this platform, was correctly excluded
    from being mistaken for a reply, but that also meant it was invisible
    on the dashboard entirely, even though it's a genuine part of the
    real conversation.

    Dedup by CONTENT within each direction, not a stable message id --
    Instagram's DOM exposes no per-message identifier to key off (see
    _read_thread_messages' own docstring), so a body already known on the
    matching side is treated as already-recorded. Known real limitation:
    two literally-identical messages on the same side (e.g. sending "K"
    twice) are indistinguishable this way and the second one won't be
    backfilled -- accepted the same way the single-newest-reply version
    of this function already accepted it for the incoming side alone.

    Returns (new_replies_recorded, new_outgoing_backfilled).
    """
    known_incoming = {r.get("body") for r in repo.replies_for_lead(lead["id"])}
    known_outgoing = {
        m.get("edited_body") or m.get("body")
        for m in repo.messages_for_lead(lead["id"])
        if m.get("channel") == "instagram"
    }

    new_replies = 0
    new_outgoing = 0
    for msg in live_messages:
        text = msg["text"]
        if msg["from"] == "lead":
            if text in known_incoming:
                continue
            handle_reply_detected(
                lead["id"],
                channel="instagram",
                body=text,
                replied_at=dt.datetime.now(dt.timezone.utc),
                account_id=account["id"],
            )
            known_incoming.add(text)
            new_replies += 1
        else:
            if text in known_outgoing:
                continue
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            repo.insert_message({
                "lead_id": lead["id"],
                "channel": "instagram",
                "body": text,
                "approval_status": "approved",
                "approved_at": now_iso,
                "send_status": "sent",
                "sent_at": now_iso,
                "sent_via_account": account["id"],
            })
            known_outgoing.add(text)
            new_outgoing += 1

    return new_replies, new_outgoing
