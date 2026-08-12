"""
Checks LinkedIn and WhatsApp for replies and advances the pipeline.

A detected reply moves the lead Contacted -> Replied and immediately cancels
any scheduled follow-up or re-engagement for that lead.

============================================================================
STATUS -- both channels have code; LinkedIn's is not live-verified yet
============================================================================
WhatsApp: see sending/whatsapp_reply_check.py's check_whatsapp_replies() --
it polls Twilio's Messages API for inbound messages from each contacted
lead's WhatsApp number, and calls handle_reply_detected() below on a hit.
No public webhook endpoint exists yet, so this is pull-based rather than
push-based; good enough without Phase 10's server exposing one.

LinkedIn: see sending/linkedin_reply_check.py's check_linkedin_replies() --
opens the owning account's real messaging inbox and reads each contacted
lead's thread. Built 2026-08-03, but its DOM selectors are inferred, not
inspected against a real LinkedIn inbox (see that module's own docstring
for exactly what's unverified) -- watch it closely against a real
conversation before trusting it unattended, same caution already applied
to linkedin_send.py's person path.

handle_reply_detected() below is the pure-logic half both channels share
once a reply has already been found by whichever channel-specific checker
found it -- that part doesn't change with which channel triggered it.

RE-VERIFIED 2026-08-05: this used to only flip the lead's status to
"replied" and threw away the reply itself -- both check_whatsapp_replies()
and check_linkedin_replies() already had the actual reply text (and, via
the lead, which account received it) in hand and discarded it once their
own boolean "did they reply" check was done. Hussein flagged directly that
the dashboard needs to show what a client actually said and from which
account, not just a status flip with no content behind it. Now persisted to
the `replies` table (database/006_add_replies.sql) via
repositories.insert_reply() -- one row per reply, so a lead that replies
more than once keeps every message, not just the fact that "a" reply
happened.
============================================================================
"""

from __future__ import annotations

import datetime as dt

from agent.crm import followup, pipeline
from agent.db import repositories as repo


def handle_reply_detected(
    lead_id: str,
    *,
    channel: str,
    body: str,
    replied_at: dt.datetime,
    account_id: str | None = None,
) -> dict:
    """
    Given that a reply was already detected (see module docstring), records
    the actual reply content, advances the lead to "replied", and cancels
    any pending follow-up/re-engagement -- the consequences the spec
    requires the instant a reply comes in.

    `channel`/`body`/`replied_at` are required, not optional, deliberately:
    a caller that detected a reply already has these (that's how it knew a
    reply happened at all), so there's no legitimate call site that would
    need to omit them -- making them required here is what stops a future
    reply-detection path from silently reintroducing the same "status flip
    with no content" gap this fix closes.
    """
    repo.insert_reply({
        "lead_id": lead_id,
        "account_id": account_id,
        "channel": channel,
        "body": body,
        "replied_at": replied_at.isoformat(),
    })
    lead = pipeline.move_stage(lead_id, "replied", changed_by="agent")
    followup.cancel_pending(lead_id)
    return lead
