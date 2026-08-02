"""
Pushes approved Instagram messages to the dashboard's manual-send queue.

THIS MODULE NEVER SENDS. It marks messages 'manual_send_pending' so a human
sends them by hand and taps 'Mark as Sent', which enters the lead into the
pipeline exactly as an automated LinkedIn/WhatsApp send would.
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


def mark_sent(message_id: str, sent_via_account: str | None = None) -> dict:
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
    """
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    message = repo.update_message(message_id, {
        "send_status": "sent",
        "sent_at": now_iso,
        "sent_via_account": sent_via_account,
    })

    lead = repo.get_lead(message["lead_id"])
    contact_updates = {"contact_count": (lead.get("contact_count") or 0) + 1}
    if not lead.get("first_contacted_at"):
        contact_updates["first_contacted_at"] = now_iso
    repo.update_lead(message["lead_id"], contact_updates)

    pipeline.move_stage(message["lead_id"], "contacted", changed_by="agent")
    repo.mark_client_history_contacted(message["lead_id"])

    return message
