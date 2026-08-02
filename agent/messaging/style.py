"""
Message style selection and rotation.

Direct style names the weak points outright. Discovery style is softer and
curiosity-driven. The active style holds for settings.style_duration_days,
then rotates automatically. Both remain editable from the dashboard at any
time -- this module only decides the *default* progression, it doesn't lock
anything.
"""

from __future__ import annotations

import datetime as dt

from agent.db import repositories as repo

DIRECT = "direct"
DISCOVERY = "discovery"

DIRECT_STYLE_GUIDE = (
    "Style: DIRECT. Name the specific weak point plainly and confidently -- state what's broken "
    "and what fixing it would look like. No hedging, no hinting. Example tone: \"I noticed you're "
    "spending on Meta ads, but your follow-up is manual and your booking takes 12h.\""
)

DISCOVERY_STYLE_GUIDE = (
    "Style: DISCOVERY. Softer, curiosity-driven -- raise a genuine question about how they "
    "currently handle something, rather than stating the problem outright. Invite them to share, "
    "don't diagnose them."
)


def style_guide(message_style: str) -> str:
    return DIRECT_STYLE_GUIDE if message_style == DIRECT else DISCOVERY_STYLE_GUIDE


def _rotate_if_due(settings: dict, now: dt.datetime) -> dict:
    """
    If style_duration_days has elapsed since style_last_rotated_at, flip the
    style and reset the clock. Returns the settings row to actually use --
    callers must read message_style from this return value, not the stale
    dict passed in, since it may have just changed.
    """
    last_rotated = settings.get("style_last_rotated_at")
    duration_days = settings.get("style_duration_days") or 7
    if not last_rotated:
        return settings

    last_rotated_dt = dt.datetime.fromisoformat(last_rotated)
    if last_rotated_dt.tzinfo is None:
        last_rotated_dt = last_rotated_dt.replace(tzinfo=dt.timezone.utc)

    if (now - last_rotated_dt).days < duration_days:
        return settings

    next_style = DISCOVERY if settings.get("message_style") == DIRECT else DIRECT
    return repo.update_settings({
        "message_style": next_style,
        "style_last_rotated_at": now.isoformat(),
    })


def get_active_style() -> str:
    """
    The style to use for the next batch of generated messages. Checks
    whether a rotation is due (see _rotate_if_due) before returning, so
    callers never need to think about the rotation clock themselves.
    """
    settings = repo.get_settings() or {}
    settings = _rotate_if_due(settings, dt.datetime.now(dt.timezone.utc))
    return settings.get("message_style") or DISCOVERY
