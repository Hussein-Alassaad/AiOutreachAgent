"""
Pushes approved Instagram messages to the dashboard's manual-send queue.

THIS MODULE NEVER SENDS. It marks messages 'manual_send_pending' so a human
sends them by hand and taps 'Mark as Sent', which enters the lead into the
pipeline exactly as an automated LinkedIn/WhatsApp send would.

Instagram REPLIES (message["is_reply"] == True, from the "Reply Here"
dashboard page) are queued here too, not auto-sent -- there's no live-
verified Instagram DOM-send automation anywhere in this codebase yet (only
LinkedIn's sending/linkedin_send.py has one, built and inspected against a
real live account per that module's own docstring). Guessing untested
selectors for a real client-facing send is exactly the mistake this
codebase's other modules deliberately avoid (every DOM selector here was
confirmed against a real page first) -- so Instagram replies queue for
manual send until real Instagram send automation is built and verified the
same way. See scheduler.py's run_reply_send_cycle() for how this queue is
populated on the fast reply-poll cadence.
"""

from __future__ import annotations

import datetime as dt

from agent.crm import pipeline
from agent.db import repositories as repo


def queue_for_manual_send(message: dict) -> dict:
    """
    Move one approved Instagram message into the manual-send queue. Never
    touches LinkedIn/WhatsApp messages -- the caller (scheduler.py's sending
    dispatcher) is responsible for only routing Instagram messages here.
    """
    return repo.update_message(message["id"], {"send_status": "manual_send_pending"})


def mark_sent(message_id: str, sent_via_account: str | None = None, is_reply: bool = False) -> dict:
    """
    Called when a human actually sends the queued message by hand and taps
    'Mark as Sent' in the dashboard (Phase 9). Records the send and moves
    the lead to 'contacted' -- the same pipeline entry point an automated
    LinkedIn/WhatsApp send will use, so Instagram leads aren't treated any
    differently once they're actually sent, only in how they get sent.

    Increments contact_count and sets first_contacted_at only if this is
    genuinely the lead's first contact (max_contacts_per_lead is a hard
    cap enforced elsewhere -- this function just records what happened).
    The actual pipeline move (and its history entry) goes through
    crm/pipeline.py, the one place that's supposed to touch lead.status
    once a lead has entered the pipeline.

    `is_reply=True` (caller already has the message row in hand -- from
    "Reply Here", see run_reply_send_cycle()) skips ALL of the above
    bookkeeping: the lead's first-contact/pipeline move already happened
    when the ORIGINAL outbound message was sent, so re-running it here
    would wrongly reset a lead that's already past "contacted" (e.g.
    "replied", "interested") back to "contacted", and double-count
    contact_count for what's actually an ongoing conversation, not a new
    contact.
    """
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    message = repo.update_message(message_id, {
        "send_status": "sent",
        "sent_at": now_iso,
        "sent_via_account": sent_via_account,
    })

    if is_reply:
        return message

    lead = repo.get_lead(message["lead_id"])
    contact_updates = {"contact_count": (lead.get("contact_count") or 0) + 1}
    if not lead.get("first_contacted_at"):
        contact_updates["first_contacted_at"] = now_iso
    repo.update_lead(message["lead_id"], contact_updates)

    pipeline.move_stage(message["lead_id"], "contacted", changed_by="agent")
    repo.mark_client_history_contacted(message["lead_id"])

    return message
