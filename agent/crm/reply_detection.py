"""
Checks LinkedIn and WhatsApp for replies and advances the pipeline.

A detected reply moves the lead Contacted -> Replied and immediately cancels
any scheduled follow-up or re-engagement for that lead.

============================================================================
STATUS -- WhatsApp checking is built, LinkedIn is intentionally not
============================================================================
WhatsApp: see sending/whatsapp_reply_check.py's check_whatsapp_replies() --
it polls Twilio's Messages API for inbound messages from each contacted
lead's WhatsApp number, and calls handle_reply_detected() below on a hit.
No public webhook endpoint exists yet, so this is pull-based rather than
push-based; good enough without Phase 10's server exposing one.

LinkedIn: NOT built here, on purpose. Detecting a LinkedIn reply means
reading a real message thread through the same live, logged-in browser
session sending/linkedin_send.py is already blocked on -- selector
verification against a real account, with real automation-detection risk.
That's separate, riskier work that shouldn't be guessed at blind; it
belongs alongside linkedin_send.py once live-account access exists.

handle_reply_detected() below is the pure-logic half both channels share
once a reply has already been found by whichever channel-specific checker
found it -- that part doesn't change with which channel triggered it.
============================================================================
"""

from __future__ import annotations

from agent.crm import followup, pipeline


def handle_reply_detected(lead_id: str) -> dict:
    """
    Given that a reply was already detected (see module docstring), advances
    the lead to "replied" and cancels any pending follow-up/re-engagement --
    the two consequences the spec requires the instant a reply comes in.
    """
    lead = pipeline.move_stage(lead_id, "replied", changed_by="agent")
    followup.cancel_pending(lead_id)
    return lead
