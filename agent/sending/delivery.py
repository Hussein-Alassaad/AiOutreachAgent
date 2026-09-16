"""
The one rule every send path in this codebase shares: once a message has
actually been delivered, it must NEVER be sent again.

WHY THIS MODULE EXISTS (two real double-send vectors, found and fixed
2026-09-16)
---------------------------------------------------------------------------
VECTOR 1 -- no claim at all on the reply paths. linkedin_send.send_reply()
and instagram_send.send_reply() both wrote send_status='sent' only at the
very END, AFTER the send-button click, and took no claim beforehand. Any
failure after delivery (a browser crash, a session-close error, a DB blip)
left the row at 'pending' -- indistinguishable from never having been
attempted. repo.replies_pending() then re-selected it on scheduler.py's
~3-minute reply IntervalTrigger and delivered it again, and again:
unbounded, because replies are deliberately exempt from the daily send cap.
Fixed by taking repo.claim_message_for_sending() -- the same
single-statement atomic claim (UPDATE ... WHERE send_status='pending'
RETURNING *) the cold-send paths already used -- BEFORE any browser work,
and bailing out immediately when it returns None because another process
already holds the message.

VECTOR 2 -- a blanket `except Exception` that reset a DELIVERED message to
'pending'. Every send path's try block enclosed BOTH the send click and all
the teardown after it (sessions.close(), attachment cleanup, the final DB
write). A failure anywhere in that teardown reset a message the lead had
ALREADY received back to 'pending', and the next cycle re-sent it for real.
Fixed by the Delivery flag below: the reset to 'pending' now happens only
when the send click provably never occurred.

Kept in its own module, deliberately free of Playwright/httpx imports, so
all three channels (linkedin_send, instagram_send, whatsapp_send) share ONE
definition of the invariant and can never drift apart on it -- and so the
Twilio-only WhatsApp path doesn't have to import a browser-automation
module just to reach it.
"""

from __future__ import annotations

import datetime as dt

from agent.db import repositories as repo


class Delivery:
    """
    Tracks whether the real send click (or, for WhatsApp, Twilio's acceptance
    of the message) has already happened.

    `mark()` is called IMMEDIATELY after every real send action, before any
    teardown or bookkeeping runs. Error handlers consult `delivered` before
    deciding whether releasing the claim back to 'pending' -- which makes the
    message eligible for a genuine re-send -- is safe.
    """

    def __init__(self) -> None:
        self.delivered = False

    def mark(self) -> None:
        self.delivered = True


def settle_after_failure(message: dict, delivery: Delivery, exc: Exception, *, channel: str) -> None:
    """
    Decide what a failed send attempt leaves behind in the DB.

    BEFORE the send action: nothing went out, so releasing the claim back to
    'pending' is correct and safe -- this is the original, intended retry
    path, unchanged.

    AFTER the send action: the lead already has the message. Re-sending is
    the one outcome the owner has said must never happen, and it is strictly
    worse than any bookkeeping imperfection. So the row is marked terminally
    'sent' (it genuinely was), with the teardown failure recorded in
    send_failure_reason so a human can still see that something went wrong
    instead of it being silently swallowed -- and it never becomes eligible
    for another send.
    """
    if not delivery.delivered:
        repo.update_message(message["id"], {"send_status": "pending"})
        return

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    reason = (
        "DELIVERED, but the send failed afterwards during teardown/bookkeeping "
        f"({type(exc).__name__}: {exc}). Marked sent deliberately so it is never "
        "re-sent -- verify the thread if anything looks off."
    )
    try:
        repo.update_message(message["id"], {
            "send_status": "sent",
            "sent_at": now_iso,
            "send_failure_reason": reason,
        })
    except Exception:  # noqa: BLE001 -- the DB itself may be what failed; never fall back to 'pending'
        pass
    try:
        # Imported lazily: scheduler imports the sending modules at module
        # level, so a top-level import here would be circular.
        from agent import scheduler

        scheduler.log_error(
            "sending",
            RuntimeError(reason),
            channel=channel,
            lead_id=message.get("lead_id"),
        )
    except Exception:  # noqa: BLE001 -- logging must never mask the real exception being re-raised
        pass
