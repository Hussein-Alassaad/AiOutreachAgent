"""
Follow-up and re-engagement scheduling and dispatch. Enforces the 2-contact
maximum.

Follow-up is a PER-LEAD decision -- Hussein sets on/off and the exact timing
himself; the agent never picks a delay. Re-engagement covers leads who
replied then went cold. Everything pauses the instant a lead replies (see
reply_detection.py). Max 2 contacts, enforced here at scheduling time, not
left to be silently skipped later.

RE-VERIFIED 2026-08-13: dispatch_due_followups() below closes the gap this
module's docstring used to flag -- due_followups() only ever surfaced what
was due, nothing generated or queued the actual message. Hussein flagged
directly that a follow-up must NOT just repeat the original pitch, so
dispatch generates through messaging/generate.py's generate_followup_message
(a distinct, shorter, non-repeating system prompt -- see that module for
what makes it different from a first message) rather than reusing
generate_message(). The generated follow-up still goes through the normal
approval queue (messages.approval_status defaults to "awaiting") -- a
follow-up is not exempt from Mohamad's review just because it was
auto-triggered by a schedule.
"""

from __future__ import annotations

import datetime as dt

from agent.db import repositories as repo
from agent.messaging import generate as message_generate
from agent.messaging import style as message_style


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
    tick would read to know what's due.
    """
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    return repo.due_follow_ups(now_iso)


def _original_body_for(lead_id: str, channel: str) -> str | None:
    """
    The most recent actually-sent (not just generated/approved) message on
    this channel -- what the follow-up should read as a continuation of.
    None if nothing was ever sent on this channel (shouldn't normally
    happen for a lead that reached "contacted", but a follow-up shouldn't
    crash the whole dispatch batch over it -- see dispatch_due_followups()).
    """
    sent = [
        m for m in repo.messages_for_lead(lead_id)
        if m.get("channel") == channel and m.get("send_status") == "sent"
    ]
    if not sent:
        return None
    return max(sent, key=lambda m: m.get("sent_at") or "").get("body")


def dispatch_due_followups() -> list[dict]:
    """
    For every follow-up whose scheduled time has arrived, generate its
    distinct follow-up message (see generate.generate_followup_message) on
    the lead's original contact channel and queue it for approval -- same
    approval_status="awaiting" gate every other generated message goes
    through (Mohamad reviews a follow-up before it sends too, exactly like
    a first message). Marks the follow_ups row "sent" once queued -- "sent"
    here means "dispatched into the approval queue", matching this table's
    existing status vocabulary (scheduled | sent | cancelled | paused),
    not "delivered to the platform" (that's messages.send_status's job).

    One bad lead's failure (e.g. no original message found, generation
    error) is isolated per-item and doesn't stop the rest of the batch --
    same pattern as run_analysis_cycle/run_message_generation_cycle.
    """
    active_style = message_style.get_active_style()
    results = []

    for follow_up in due_followups():
        lead_id = follow_up["lead_id"]
        lead = repo.get_lead(lead_id)
        if not lead:
            results.append({"follow_up_id": follow_up["id"], "ok": False, "error": "lead not found"})
            continue

        channel = lead.get("platform")
        try:
            original_body = _original_body_for(lead_id, channel)
            if not original_body:
                raise ValueError(f"No sent {channel} message found for lead {lead_id} to follow up on.")

            body = message_generate.generate_followup_message(lead, channel, active_style, original_body)
            repo.insert_message({
                "lead_id": lead_id,
                "channel": channel,
                "body": body,
                "is_followup": True,
                "is_reengagement": follow_up.get("is_reengagement", False),
            })
            repo.update_follow_up(follow_up["id"], {"status": "sent"})
            results.append({"follow_up_id": follow_up["id"], "lead": lead.get("business_name"), "ok": True, "channel": channel})
        except Exception as exc:  # noqa: BLE001 -- one bad follow-up shouldn't stop the batch
            results.append({"follow_up_id": follow_up["id"], "lead": lead.get("business_name"), "ok": False, "error": str(exc)})

    return results
