"""
Per-account warm-up ramp: start at 5/day, climb weekly to the full target.

Steady state is 20 Instagram + 30 LinkedIn per account. Starting there on day
one gets accounts banned. Timing is also varied between accounts so the three
don't look like one operator.

The ramp is computed from the account's `created_at` (when it joined the
pool) rather than a separate "warmup started" column -- one week after
creation is the same thing as one week of warm-up, since a pause is a manual
redistribute decision (core rule R9), never a reason to restart the ramp.
"""

from __future__ import annotations

import datetime as dt

from agent.db import repositories as repo

WARMUP_START = 5      # cap on day one
WARMUP_STEP = 5        # cap increase per full week elapsed
WARMUP_CEILING = 30    # LinkedIn's steady-state target, the higher of the two --
                       # Instagram's own lower daily_limit still applies via effective_limit()


def _weeks_since(created_at: str, now: dt.datetime) -> int:
    created = dt.datetime.fromisoformat(created_at)
    if created.tzinfo is None:
        created = created.replace(tzinfo=dt.timezone.utc)
    return max(0, (now - created).days // 7)


def compute_warmup_cap(account: dict, now: dt.datetime | None = None) -> int:
    """
    This account's current warm-up cap: WARMUP_START on day one, climbing by
    WARMUP_STEP for every full week since `created_at`, capped at
    WARMUP_CEILING. Missing `created_at` (shouldn't happen -- the column
    defaults to now() -- but the DB round-trip could theoretically omit it)
    is treated as day one rather than raising.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    created_at = account.get("created_at")
    if not created_at:
        return WARMUP_START
    weeks = _weeks_since(created_at, now)
    return min(WARMUP_START + WARMUP_STEP * weeks, WARMUP_CEILING)


def effective_limit(account: dict, platform: str) -> int:
    """
    The real per-run discovery cap for this account today: the smaller of the
    platform's steady-state daily_limit and the account's current warm-up
    cap. Persists the recomputed cap back to `accounts.warmup_current_limit`
    so the dashboard's Account Health page reflects it, since that column
    exists for display, not just internal use.
    """
    key = "ig_daily_limit" if platform == "instagram" else "linkedin_daily_limit"
    default = 20 if platform == "instagram" else 30
    platform_limit = account.get(key) or default

    cap = compute_warmup_cap(account)
    if account.get("warmup_current_limit") != cap:
        repo.update_account(account["id"], {"warmup_current_limit": cap})
        account["warmup_current_limit"] = cap

    return min(platform_limit, cap)
