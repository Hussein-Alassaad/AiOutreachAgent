"""
Follow-up and re-engagement scheduling. Enforces the 2-contact maximum.

Follow-up is a PER-LEAD decision -- Hussein sets on/off and the exact timing
himself; the agent never picks a delay. Re-engagement covers leads who
replied then went cold. Everything pauses the instant a lead replies (see
reply_detection.py). Max 2 contacts, enforced here at scheduling time, not
left to be silently skipped later.

Dispatching a due follow-up (generating and actually sending its message) is
NOT built here -- it needs the same LinkedIn/WhatsApp send channels Phase 7
already flagged as pending. due_followups() below only surfaces what's due;
acting on it is a follow-on step once those channels exist.
"""

from __future__ import annotations

import datetime as dt

from agent.db import repositories as repo


class MaxContactsReached(RuntimeError):
    """Raised when scheduling a follow-up would exceed max_contacts_per_lead."""


def schedule_followup(lead_id: str, scheduled_for: str, is_reengagement: bool = False) -> dict:
    """
    Schedule one follow-up (or re-engagement) at the exact time Hussein
    chose. Refuses outright if the lead has already hit
    settings.max_contacts_per_lead -- the hard cap is enforced here, at
    scheduling time, so a violation is never silently possible later.
    """
    lead = repo.get_lead(lead_id)
    settings = repo.get_settings() or {}
    max_contacts = settings.get("max_contacts_per_lead") or 2

    if lead and (lead.get("contact_count") or 0) >= max_contacts:
        raise MaxContactsReached(
            f"Lead {lead_id} already has {lead.get('contact_count')} contact(s) "
            f"(max {max_contacts}) -- cannot schedule another."
        )

    return repo.insert_follow_up({
        "lead_id": lead_id,
        "enabled": True,
        "scheduled_for": scheduled_for,
        "is_reengagement": is_reengagement,
        "status": "scheduled",
    })


def cancel_pending(lead_id: str) -> list[dict]:
    """
    Cancel every still-scheduled follow-up for one lead. Called the instant
    a reply is detected (see reply_detection.py) -- a reply means the lead
    is now engaged and doesn't need a nudge it hasn't already responded to.
    """
    pending = [f for f in repo.follow_ups_for_lead(lead_id) if f.get("status") == "scheduled"]
    return [repo.update_follow_up(f["id"], {"status": "cancelled"}) for f in pending]


def due_followups() -> list[dict]:
    """
    Follow-ups whose scheduled time has arrived -- what a daily scheduler
    tick would read to know what's due. See module docstring: this only
    surfaces what's due, it doesn't send anything.
    """
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    return repo.due_follow_ups(now_iso)
