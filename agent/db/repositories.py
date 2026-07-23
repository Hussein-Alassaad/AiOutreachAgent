"""
Data-access layer — the ONLY place that reads or writes Supabase tables.

Every other module in the agent goes through the functions here instead of
touching the database directly. Two payoffs:

  1. If the schema changes, this one file changes — not every caller.
  2. Callers read like plain English: `repositories.insert_lead(...)`,
     `repositories.get_settings()`. No SQL leaks into the business logic.

The module is organised one section per table, in the same order as
02_SUPABASE_SCHEMA.sql: accounts, settings, leads, messages, pipeline_history,
follow_ups, client_history, notifications_log, runs.

All functions use the shared service-role client from db/client.py, so they run
on the trusted server with full access and bypass Row Level Security.
"""

from __future__ import annotations

from typing import Any

from agent.db.client import get_client

# Type alias: a database row is just a dict of column -> value.
Row = dict[str, Any]


# ════════════════════════════════════════════════════════════════════════
# ACCOUNTS — the 3 outreach accounts, each with its own proxy slot
# ════════════════════════════════════════════════════════════════════════

def list_accounts() -> list[Row]:
    """All accounts, oldest first. Used by the account pool at run start."""
    res = get_client().table("accounts").select("*").order("created_at").execute()
    return res.data


def get_account(account_id: str) -> Row | None:
    """One account by id, or None if it doesn't exist."""
    res = get_client().table("accounts").select("*").eq("id", account_id).limit(1).execute()
    return res.data[0] if res.data else None


def update_account(account_id: str, fields: Row) -> Row:
    """Patch one account (e.g. status, warning fields, proxy creds, warm-up cap)."""
    res = get_client().table("accounts").update(fields).eq("id", account_id).execute()
    return res.data[0]


def set_account_warning(account_id: str, warning_type: str, reason: str) -> Row:
    """
    Record an account warning and pause it.

    Core rule R9: warnings carry a specific TYPE and REASON, never generic, and
    the account is paused. Quota is never redistributed automatically — that stays
    a manual decision, so we only raise the flag, we don't act on it.
    """
    return update_account(account_id, {
        "status": "paused",
        "warning_type": warning_type,
        "warning_reason": reason,
    })


# ════════════════════════════════════════════════════════════════════════
# SETTINGS — single global row, edited from the dashboard
# ════════════════════════════════════════════════════════════════════════

def get_settings() -> Row | None:
    """
    The one settings row. The agent loads this at the start of every run (Task 1)
    to pick up niche, location, language, targets, message style, and so on.
    """
    res = get_client().table("settings").select("*").limit(1).execute()
    return res.data[0] if res.data else None


def update_settings(fields: Row) -> Row:
    """Patch the settings row. `id` is looked up so callers don't have to pass it."""
    current = get_settings()
    if current is None:
        raise RuntimeError("No settings row exists — run the schema seed first.")
    res = get_client().table("settings").update(fields).eq("id", current["id"]).execute()
    return res.data[0]


# ════════════════════════════════════════════════════════════════════════
# LEADS — every business the agent discovers
# ════════════════════════════════════════════════════════════════════════

def insert_lead(lead: Row) -> Row:
    """Insert a freshly discovered lead. Returns the row with its generated id."""
    res = get_client().table("leads").insert(lead).execute()
    return res.data[0]


def update_lead(lead_id: str, fields: Row) -> Row:
    """Patch a lead (analysis results, score, generated message, status, ...)."""
    res = get_client().table("leads").update(fields).eq("id", lead_id).execute()
    return res.data[0]


def get_lead(lead_id: str) -> Row | None:
    res = get_client().table("leads").select("*").eq("id", lead_id).limit(1).execute()
    return res.data[0] if res.data else None


def leads_by_status(status: str) -> list[Row]:
    """All leads in a given lifecycle status (e.g. 'awaiting_approval')."""
    res = get_client().table("leads").select("*").eq("status", status).execute()
    return res.data


def contacted_profile_urls() -> set[str]:
    """
    Profile URLs of every lead already contacted at least once.

    Task 1 deduplication: loaded at run start so no business is ever contacted
    twice unintentionally. Returned as a set for O(1) membership checks during
    discovery.
    """
    res = (
        get_client()
        .table("leads")
        .select("profile_url")
        .gt("contact_count", 0)
        .execute()
    )
    return {r["profile_url"] for r in res.data if r.get("profile_url")}


# ════════════════════════════════════════════════════════════════════════
# MESSAGES — every message queued/sent per lead
# ════════════════════════════════════════════════════════════════════════

def insert_message(message: Row) -> Row:
    res = get_client().table("messages").insert(message).execute()
    return res.data[0]


def update_message(message_id: str, fields: Row) -> Row:
    res = get_client().table("messages").update(fields).eq("id", message_id).execute()
    return res.data[0]


def messages_awaiting_approval() -> list[Row]:
    """
    Everything Mahmoud still needs to approve. Feeds the approval queue badge and
    the hard send-gate (nothing sends while this is non-empty for a given lead).
    """
    res = (
        get_client()
        .table("messages")
        .select("*")
        .eq("approval_status", "awaiting")
        .execute()
    )
    return res.data


def messages_for_lead(lead_id: str) -> list[Row]:
    res = (
        get_client()
        .table("messages")
        .select("*")
        .eq("lead_id", lead_id)
        .order("created_at")
        .execute()
    )
    return res.data


# ════════════════════════════════════════════════════════════════════════
# PIPELINE_HISTORY — every stage change, timestamped audit trail
# ════════════════════════════════════════════════════════════════════════

def record_stage_change(
    lead_id: str, from_stage: str | None, to_stage: str, changed_by: str = "agent"
) -> Row:
    """
    Append one row to the audit trail. Called by pipeline.py whenever a lead moves,
    whether the move was automatic (reply detected) or manual (drag on the board).
    """
    res = get_client().table("pipeline_history").insert({
        "lead_id": lead_id,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "changed_by": changed_by,
    }).execute()
    return res.data[0]


def stage_history(lead_id: str) -> list[Row]:
    """Full ordered stage history for the Lead Detail page."""
    res = (
        get_client()
        .table("pipeline_history")
        .select("*")
        .eq("lead_id", lead_id)
        .order("changed_at")
        .execute()
    )
    return res.data


# ════════════════════════════════════════════════════════════════════════
# FOLLOW_UPS — per-lead follow-up / re-engagement schedule
# ════════════════════════════════════════════════════════════════════════

def insert_follow_up(follow_up: Row) -> Row:
    res = get_client().table("follow_ups").insert(follow_up).execute()
    return res.data[0]


def update_follow_up(follow_up_id: str, fields: Row) -> Row:
    res = get_client().table("follow_ups").update(fields).eq("id", follow_up_id).execute()
    return res.data[0]


def due_follow_ups(now_iso: str) -> list[Row]:
    """
    Enabled follow-ups whose scheduled time has arrived and are still pending.
    The agent runs these at exactly the time Hussein set per lead.
    """
    res = (
        get_client()
        .table("follow_ups")
        .select("*")
        .eq("enabled", True)
        .eq("status", "scheduled")
        .lte("scheduled_for", now_iso)
        .execute()
    )
    return res.data


# ════════════════════════════════════════════════════════════════════════
# CLIENT_HISTORY — permanent record of every analysed lead (never resets)
# ════════════════════════════════════════════════════════════════════════

def insert_client_history(entry: Row) -> Row:
    """
    Save a permanent snapshot. Task 10: this happens BEFORE any message is
    generated, so a lead lives here forever whether or not it is ever contacted.
    """
    res = get_client().table("client_history").insert(entry).execute()
    return res.data[0]


def mark_client_history_contacted(lead_id: str) -> None:
    """Flip the `contacted` flag once outreach actually goes out for this lead."""
    get_client().table("client_history").update({"contacted": True}).eq(
        "lead_id", lead_id
    ).execute()


# ════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS_LOG — every WhatsApp alert sent
# ════════════════════════════════════════════════════════════════════════

def log_notification(
    notif_type: str, recipients: list[str], body: str, related_lead_id: str | None = None
) -> Row:
    """
    Record a sent alert. One of the 5 types: hot_lead, founder, warning,
    approval_reminder, daily_summary.
    """
    res = get_client().table("notifications_log").insert({
        "type": notif_type,
        "recipients": recipients,
        "body": body,
        "related_lead_id": related_lead_id,
    }).execute()
    return res.data[0]


# ════════════════════════════════════════════════════════════════════════
# RUNS — one row per account per daily run (finish-time tracking)
# ════════════════════════════════════════════════════════════════════════

def start_run(account_id: str) -> Row:
    """
    Open a run row when an account begins. `started_at` defaults to now() in the
    schema. Returns the row so the scheduler can close it later with the id.
    """
    res = get_client().table("runs").insert({
        "account_id": account_id,
        "status": "running",
    }).execute()
    return res.data[0]


def finish_run(
    run_id: str,
    leads_found: int,
    messages_sent: int,
    status: str = "completed",
    finished_at_iso: str | None = None,
    notes: str | None = None,
) -> Row:
    """
    Close a run with its results and finish time. The finish time feeds both the
    dashboard Run Status panel and the daily WhatsApp summary.
    """
    fields: Row = {
        "leads_found": leads_found,
        "messages_sent": messages_sent,
        "status": status,
    }
    if finished_at_iso is not None:
        fields["finished_at"] = finished_at_iso
    if notes is not None:
        fields["notes"] = notes
    res = get_client().table("runs").update(fields).eq("id", run_id).execute()
    return res.data[0]
