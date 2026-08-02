"""
Sends approved messages on WhatsApp when a public number was found.

Fires for leads from either platform -- WhatsApp is always an ADDITIONAL
channel, never a replacement for the lead's primary platform (see
analysis/whatsapp_detect.py). Twilio is called directly over its REST API
with httpx rather than through the `twilio` PyPI package (see
requirements.txt's note on why that dependency isn't pulled in) -- one HTTP
endpoint doesn't justify an extra SDK.

notifications/whatsapp_notify.py reuses send_text() below for its 5 alert
types -- one Twilio integration, two callers (outreach sends vs. internal
alerts), so credentials and the wire format only live in one place.
"""

from __future__ import annotations

import datetime as dt

import httpx

from agent import config
from agent.crm import pipeline
from agent.db import repositories as repo
from agent.messaging import approval

TWILIO_MESSAGES_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


class WhatsAppNotConfigured(RuntimeError):
    """Raised when a send is attempted with no Twilio credentials in agent/.env."""


def is_configured() -> bool:
    """True when real Twilio credentials are present."""
    return not config.missing_required(["WHATSAPP_API_KEY", "WHATSAPP_API_SECRET", "WHATSAPP_FROM_NUMBER"])


def whatsapp_address(number: str) -> str:
    """
    Twilio's WhatsApp channel addresses every number as 'whatsapp:+1234...'.
    Numbers coming out of analysis/whatsapp_detect.py aren't guaranteed a
    leading '+' (it only kept one if the source text had one), so add it
    when missing rather than send an address Twilio will reject.
    """
    digits = number if number.startswith("+") else f"+{number}"
    return f"whatsapp:{digits}"


def send_text(to: str, body: str) -> dict:
    """
    Raw Twilio send -- one WhatsApp message, no lead/pipeline bookkeeping.
    Used directly by notifications/whatsapp_notify.py (an alert isn't tied
    to a message row) and wrapped by send_message() below for outreach.
    """
    if not is_configured():
        raise WhatsAppNotConfigured(
            "Missing Twilio credentials in agent/.env -- "
            "set WHATSAPP_API_KEY, WHATSAPP_API_SECRET, WHATSAPP_FROM_NUMBER."
        )

    response = httpx.post(
        TWILIO_MESSAGES_URL.format(sid=config.WHATSAPP_API_KEY),
        auth=(config.WHATSAPP_API_KEY, config.WHATSAPP_API_SECRET),
        data={
            "From": whatsapp_address(config.WHATSAPP_FROM_NUMBER),
            "To": whatsapp_address(to),
            "Body": body,
        },
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()


def send_message(message: dict) -> dict:
    """
    Send one approved outreach message and record it the same way
    sending/instagram_queue.py's mark_sent() does for the manual-send path:
    send_status/sent_at, contact_count/first_contacted_at, the pipeline
    move, and client_history -- so an automated WhatsApp send leaves the
    identical trail a human-confirmed Instagram send does. Called by
    scheduler.py's run_sending_cycle() for every approved, pending message
    on the whatsapp channel.

    No sent_via_account is recorded -- unlike LinkedIn/Instagram, WhatsApp
    sends go through Twilio, not through one of the 3 browser-automation
    accounts, so that column stays null for this channel.
    """
    lead = repo.get_lead(message["lead_id"])
    if not lead or not lead.get("whatsapp_number"):
        raise ValueError(f"Message {message['id']} has no lead WhatsApp number to send to.")

    send_text(lead["whatsapp_number"], approval.active_body(message))

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    updated_message = repo.update_message(message["id"], {
        "send_status": "sent",
        "sent_at": now_iso,
    })

    contact_updates = {"contact_count": (lead.get("contact_count") or 0) + 1}
    if not lead.get("first_contacted_at"):
        contact_updates["first_contacted_at"] = now_iso
    repo.update_lead(message["lead_id"], contact_updates)

    pipeline.move_stage(message["lead_id"], "contacted", changed_by="agent")
    repo.mark_client_history_contacted(message["lead_id"])

    return updated_message
