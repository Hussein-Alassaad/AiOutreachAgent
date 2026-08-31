"""
Checks Instagram for replies to leads we've messaged, via a real logged-in
browser session -- Instagram's equivalent of linkedin_reply_check.py.

============================================================================
NOT YET LIVE-VERIFIED -- see instagram_send.py's module docstring for the
same caveat: built against Instagram's known DOM structure, not yet
inspected against a real conversation. WATCH THIS CLOSELY against a real
connected test account before trusting it unattended, same caution applied
everywhere else in this codebase.
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
from agent.sending.instagram_send import INSTAGRAM_INBOX_URL, CONVERSATION_LIST_ITEM_SELECTOR

# Not yet confirmed against a real thread's DOM -- see module docstring.
_THREAD_MESSAGE_SELECTOR = "div[role='row']"
_THREAD_MESSAGE_BODY_SELECTOR = "div[dir='auto']"
# Instagram doesn't expose a stable per-message sender label the way
# LinkedIn's msg-s-message-group__profile-link does; the outgoing/incoming
# distinction is inferred from alignment (own messages right-aligned) via
# this wrapper class convention -- NOT yet confirmed live, watch closely.
_OUTGOING_MESSAGE_WRAPPER_SELECTOR = "div[style*='justify-content: flex-end']"


def _has_instagram_sent(lead_id: str) -> bool:
    return any(
        m.get("channel") == "instagram" and m.get("send_status") == "sent"
        for m in repo.messages_for_lead(lead_id)
    )


def _open_thread_for_lead(page: Page, business_name: str) -> bool:
    page.goto(INSTAGRAM_INBOX_URL, timeout=30_000, wait_until="domcontentloaded")
    item = page.locator(CONVERSATION_LIST_ITEM_SELECTOR, has_text=business_name).first
    if item.count() == 0:
        return False
    human_delay()
    item.click()
    return True


def _newest_message_if_from_lead(page: Page) -> str | None:
    """
    Reads the thread's most recent message and returns its body only if it
    was NOT sent by us (inferred from alignment -- see
    _OUTGOING_MESSAGE_WRAPPER_SELECTOR's own caveat above). Returns None if
    there's no thread or the newest message is ours.
    """
    messages = page.locator(_THREAD_MESSAGE_SELECTOR)
    count = messages.count()
    if count == 0:
        return None

    newest = messages.nth(count - 1)
    if newest.locator(_OUTGOING_MESSAGE_WRAPPER_SELECTOR).count() > 0:
        return None  # our own message

    body = newest.locator(_THREAD_MESSAGE_BODY_SELECTOR).first.text_content(timeout=2_000) or ""
    return body.strip() or None


def check_instagram_replies() -> list[dict]:
    """
    For every "contacted" lead reached via Instagram, open the owning
    account's DM inbox and check whether the lead's own most recent message
    is a reply. Same dedup note as check_linkedin_replies(): once
    handle_reply_detected() fires, the lead moves off "contacted" and
    naturally drops out of future runs' candidate list.
    """
    results = []
    leads = [
        lead for lead in repo.leads_by_status("contacted")
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
                found = _open_thread_for_lead(page, business_name)
                body = _newest_message_if_from_lead(page) if found else None
            finally:
                sessions.close(account["id"], context)

            replied = body is not None
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
