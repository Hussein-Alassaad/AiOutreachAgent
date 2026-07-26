"""
Loads the 3 outreach accounts and decides which ones are due to run right now.

One agent manages all 3 accounts as a pool -- there is no per-account process.
Each account row carries its own run time, IG/LinkedIn daily limits, warm-up cap,
and proxy slot (which may be empty until Phase 10).
"""

from __future__ import annotations

import datetime as dt

import pytz

from agent import config
from agent.db import repositories as repo


def _local_now() -> dt.datetime:
    """Current time in the configured TIMEZONE (each account's run_time is a
    plain HH:MM:SS with no timezone of its own -- it is always interpreted in
    this one project-wide zone, set in agent/.env)."""
    tz = pytz.timezone(config.TIMEZONE)
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


def load_accounts() -> list[dict]:
    """All 3 accounts, whatever their status. Callers that only want the
    active ones should filter, since 'paused' accounts still need to show up
    in places like the dashboard's Account Health monitor."""
    return repo.list_accounts()


def get_due_accounts(force: bool = False) -> list[dict]:
    """
    Which accounts should run right now.

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
    now = _local_now()
    today_start = _today_start_iso(now)
    due = []

    for account in load_accounts():
        if account["status"] != "active":
            continue

        if force:
            due.append(account)
            continue

        run_time = _parse_run_time(account["run_time"])
        if now.time() < run_time:
            continue  # scheduled time hasn't arrived yet today

        if repo.has_run_today(account["id"], today_start):
            continue  # already ran today, don't double-dispatch

        due.append(account)

    return due
