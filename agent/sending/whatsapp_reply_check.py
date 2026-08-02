"""
Polls Twilio for inbound WhatsApp replies and hands detected ones off to
crm/reply_detection.py.

No public webhook endpoint exists yet (Phase 10's always-on server doesn't
expose one), so this is pull-based: for every lead currently sitting at
"contacted" with a WhatsApp number we've actually messaged, ask Twilio's
Messages API for anything that lead sent US since our last message to them.
A webhook would be more timely later, but this needs nothing beyond the
Twilio credentials sending already requires.

LinkedIn reply detection is intentionally NOT built here -- see
crm/reply_detection.py's module docstring for why.
"""

from __future__ import annotations

import datetime as dt

import httpx

from agent import config
from agent.crm.reply_detection import handle_reply_detected
from agent.db import repositories as repo
from agent.sending.whatsapp_send import WhatsAppNotConfigured, is_configured, whatsapp_address

TWILIO_MESSAGES_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def _last_whatsapp_sent_at(lead_id: str) -> dt.datetime | None:
    """
    The most recent successful WhatsApp send to this lead -- an inbound
    message only counts as a reply to actual outreach if it's newer than
    this, not noise from before we ever messaged them. None if this lead
    was never actually sent a WhatsApp message (nothing to check against).
    """
    sent_times = [
        m["sent_at"] for m in repo.messages_for_lead(lead_id)
        if m.get("channel") == "whatsapp" and m.get("send_status") == "sent" and m.get("sent_at")
    ]
    if not sent_times:
        return None
    latest = max(sent_times)
    parsed = dt.datetime.fromisoformat(latest)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _inbound_messages(lead_number: str, since: dt.datetime) -> list[dict]:
    """
    Raw Twilio call: every message Twilio has FROM this lead's WhatsApp
    number TO ours. From/To alone identify direction here -- only our own
    Twilio-registered sender can appear as From on an outbound message, so
    anything with the lead's number as From is inbound by construction; the
    List Messages endpoint doesn't offer a separate Direction filter anyway.

    Twilio's DateSent filter is day-granularity only, so `since` narrows the
    request to the right day and the caller re-checks the exact timestamp.
    """
    response = httpx.get(
        TWILIO_MESSAGES_URL.format(sid=config.WHATSAPP_API_KEY),
        auth=(config.WHATSAPP_API_KEY, config.WHATSAPP_API_SECRET),
        params={
            "To": whatsapp_address(config.WHATSAPP_FROM_NUMBER),
            "From": whatsapp_address(lead_number),
            "DateSent>": since.strftime("%Y-%m-%d"),
        },
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json().get("messages", [])


def check_whatsapp_replies() -> list[dict]:
    """
    For every "contacted" lead with a WhatsApp number, check Twilio for a
    reply newer than our last message to them and, on a hit, hand off to
    reply_detection.handle_reply_detected(). Meant to run on the same cadence
    as the rest of the daily cycle (see scheduler.py) -- not wired into
    build_daily_schedule yet, same as run_analysis_cycle/run_sending_cycle
    and the other standalone cycle functions there.

    Returns one result dict per lead actually checked -- leads never
    WhatsApp-messaged in the first place are skipped, nothing to check a
    reply against.
    """
    if not is_configured():
        raise WhatsAppNotConfigured(
            "Missing Twilio credentials in agent/.env -- "
            "set WHATSAPP_API_KEY, WHATSAPP_API_SECRET, WHATSAPP_FROM_NUMBER."
        )

    results = []
    for lead in repo.leads_by_status("contacted"):
        if not lead.get("whatsapp_number"):
            continue
        last_sent = _last_whatsapp_sent_at(lead["id"])
        if last_sent is None:
            continue

        inbound = _inbound_messages(lead["whatsapp_number"], last_sent)
        replied = any(
            dt.datetime.strptime(m["date_sent"], "%a, %d %b %Y %H:%M:%S %z") > last_sent
            for m in inbound if m.get("date_sent")
        )
        if replied:
            handle_reply_detected(lead["id"])
        results.append({"lead_id": lead["id"], "replied": replied})

    return results
