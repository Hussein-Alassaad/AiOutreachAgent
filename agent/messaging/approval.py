"""
Approval workflow: nothing sends until Mohamad approves it.

Every generated message starts "awaiting" approval_status. Mohamad can
approve, edit-then-approve, or hold each one -- only an "approved" message is
ever eligible for Phase 7's sending step. A held or still-awaiting message
blocks its lead from moving past "awaiting_approval".
"""

from __future__ import annotations

import datetime as dt

from agent.db import repositories as repo

# Fallback only, used if settings.approval_reminder_hours is somehow unset
# (e.g. an older settings row from before database/005_add_approval_settings.sql
# ran). The real, dashboard-editable value lives in Settings.jsx now.
DEFAULT_REMINDER_AFTER_HOURS = 24


def is_send_blocked(message: dict) -> bool:
    """
    The hard gate Phase 7's sending step must check before ever sending
    anything: only an explicitly "approved" message is cleared. Held,
    awaiting, or merely-edited-but-not-yet-approved messages are all
    blocked -- editing alone is not approval.
    """
    return message.get("approval_status") != "approved"


def active_body(message: dict) -> str:
    """
    The text that should actually be sent: Mohamad's edited version if he
    edited it, otherwise the originally generated body.
    """
    return message.get("edited_body") or message.get("body")


def _parsed_created_at(message: dict) -> dt.datetime | None:
    created_at = message.get("created_at")
    if not created_at:
        return None
    parsed = dt.datetime.fromisoformat(created_at)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _maybe_advance_lead(lead_id: str) -> None:
    """
    Once every message on a lead is approved (none left awaiting or held),
    move the lead from "awaiting_approval" to "approved" and log the stage
    change. A lead with any held or still-awaiting message stays put --
    approval is per-lead only once every one of its channels clears.
    """
    lead = repo.get_lead(lead_id)
    if not lead or lead.get("status") != "awaiting_approval":
        return

    messages = repo.messages_for_lead(lead_id)
    if messages and all(m.get("approval_status") == "approved" for m in messages):
        repo.update_lead(lead_id, {"status": "approved"})
        repo.record_stage_change(lead_id, "awaiting_approval", "approved", changed_by="mohamad")


def approve_message(message_id: str, approved_by: str) -> dict:
    """
    Approve one message as-is (or as edited, if edit_message() was already
    called -- approval applies to whatever's in edited_body/body at this
    point, see active_body()). Advances the lead once every one of its
    messages is approved.
    """
    message = repo.update_message(message_id, {
        "approval_status": "approved",
        "approved_by": approved_by,
        "approved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    _maybe_advance_lead(message["lead_id"])
    return message


def edit_message(message_id: str, new_body: str) -> dict:
    """
    Record Mohamad's edited version. This alone does NOT approve the
    message -- "edited" is a distinct status from "approved", so a message
    that's been touched but not yet explicitly approved still blocks
    sending, same as a freshly-generated one. Call approve_message()
    afterward to actually clear it.
    """
    return repo.update_message(message_id, {
        "edited_body": new_body,
        "approval_status": "edited",
    })


def hold_message(message_id: str, reason: str | None = None) -> dict:
    """
    Hold one message -- keeps it (and its lead) blocked from sending
    indefinitely until Mohamad comes back and approves or edits it.
    `reason` is optional freeform text recorded in messages.hold_reason
    (database/005_add_approval_settings.sql) so a held message shows *why*
    it's stuck, not just that it is.
    """
    return repo.update_message(message_id, {"approval_status": "held", "hold_reason": reason})


def _reminder_after_hours() -> float:
    """
    settings.approval_reminder_hours (database/005_add_approval_settings.sql),
    dashboard-editable from Settings.jsx. Falls back to
    DEFAULT_REMINDER_AFTER_HOURS only if settings can't be read at all or
    the column is somehow null -- not the normal path.
    """
    settings = repo.get_settings() or {}
    return settings.get("approval_reminder_hours") or DEFAULT_REMINDER_AFTER_HOURS


def messages_needing_reminder() -> list[dict]:
    """
    Messages still sitting "awaiting" longer than settings.approval_reminder_hours
    -- the trigger Phase 8's actual WhatsApp reminder notification reads
    from. This function only identifies them; it does not send anything
    itself, since the notification-sending machinery doesn't exist until
    Phase 8.
    """
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=_reminder_after_hours())
    awaiting = repo.messages_awaiting_approval()
    return [m for m in awaiting if (created := _parsed_created_at(m)) and created < cutoff]
