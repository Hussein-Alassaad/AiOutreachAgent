"""
Loads one tenant's outreach accounts and decides which ones are due to run
right now.

PORTED 2026-08-20: this used to load a single hardcoded pool of 3 accounts
(the old standalone single-tenant schema). Now scoped per tenant_id -- the
agent manages every active tenant's account pool in turn (see
scheduler.py's per-tenant loop, which calls list_active_tenant_ids() then
this module once per tenant), not one global pool. Each account row still
carries its own run time, IG/LinkedIn daily limits, warm-up cap, and proxy
slot, unchanged.
"""

from __future__ import annotations

import datetime as dt

import pytz

from agent import config
from agent.db import repositories as repo


def _local_now(tenant_id: str) -> dt.datetime:
    """Current time in THIS TENANT's configured timezone (each account's
    run_time is a plain HH:MM:SS with no timezone of its own -- it is
    interpreted against OutreachSettings.timezone for that tenant, falling
    back to the agent's global config.TIMEZONE if unset -- see
    repo.get_outreach_timezone()'s own docstring, the single source of
    truth for this resolution, shared with scheduler.py's CronTrigger
    construction so the two code paths can't drift out of sync)."""
    tz = pytz.timezone(repo.get_outreach_timezone(tenant_id))
    return dt.datetime.now(tz)


def _today_start_iso(now: dt.datetime) -> str:
    """Midnight of `now`'s date, in the same timezone, as an ISO string.
    Used to ask the database "has this account run since today began?"."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.isoformat()


def _parse_run_time(run_time: str) -> dt.time:
    """Accounts.run_time comes back from Supabase as 'HH:MM:SS' (or 'HH:MM').
    Parsed once per account per check -- these lists are tiny (3 rows), so
    there's no need to cache this."""
    parts = run_time.split(":")
    hour, minute = int(parts[0]), int(parts[1])
    return dt.time(hour=hour, minute=minute)


def today_start_iso(tenant_id: str) -> str:
    """Public wrapper on _today_start_iso(_local_now(tenant_id)) --
    scheduler.py needs this same "midnight in THIS TENANT's timezone, as of
    right now" value to pass to repo.claim_account_for_run(), the same day
    boundary get_due_accounts() below already uses for has_run_today().
    Kept as one function here (this module already owns "what day is it"
    per the module docstring) rather than duplicating the timezone math in
    scheduler.py."""
    return _today_start_iso(_local_now(tenant_id))


def load_accounts(tenant_id: str) -> list[dict]:
    """All of this tenant's accounts, whatever their status. Callers that
    only want the active ones should filter, since 'paused' accounts still
    need to show up in places like the dashboard's Account Health monitor."""
    return repo.list_accounts(tenant_id)


def get_due_accounts(tenant_id: str, force: bool = False) -> list[dict]:
    """
    Which of this tenant's accounts should run right now.

    An account is due when all three are true:
      1. status == 'active' (a 'paused' account never runs itself back in --
         core rule R9: redistribution/un-pausing is Hussein's manual call, the
         agent never decides to resume a paused account on its own).
      2. Its configured run_time has already passed today.
      3. It has not already produced a run today (has_run_today).

    `force=True` skips checks 2 and 3 entirely -- this is what a manual test
    trigger uses, so Hussein can test the pipeline without waiting for an
    account's actual scheduled hour or worrying about a stale run row blocking
    a second manual attempt on the same day.
    """
    now = _local_now(tenant_id)
    today_start = _today_start_iso(now)
    due = []

    for account in load_accounts(tenant_id):
        if account["status"] != "active":
            continue

        if force:
            due.append(account)
            continue

        run_time = _parse_run_time(account["run_time"])
        if now.time() < run_time:
            continue  # scheduled time hasn't arrived yet today

        if repo.has_run_today(tenant_id, account["id"], today_start):
            continue  # already ran today, don't double-dispatch

        due.append(account)

    return due
