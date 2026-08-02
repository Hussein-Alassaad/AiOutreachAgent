"""
The 5 WhatsApp alert types, sent to the configured recipient numbers.

1. Hot lead (score 8-10) -- fires immediately on scoring, not at end of run.
2. Founder found. 3. Account warning (with type and reason). 4. Approval
reminder to Mohamad. 5. Daily summary with exact finish time and duration.

Sends through sending/whatsapp_send.py's raw Twilio call -- same
credentials, same HTTP client, as outreach sending. If Twilio isn't
configured yet (agent/.env's WHATSAPP_* fields are blank until a real
account exists) or a send fails, every function below still composes the
correct message and logs it via notifications_log -- only the actual
delivery is skipped, the same graceful-degradation pattern
agent/analysis/client.py uses for a missing Claude key. A notification
failing to deliver must never break whatever pipeline step triggered it
(e.g. a hot-lead alert firing mid analysis cycle), so both the "not
configured yet" case and a genuine Twilio/network failure are swallowed
here, not just the former.
"""

from __future__ import annotations

import httpx

from agent.db import repositories as repo
from agent.sending.whatsapp_send import WhatsAppNotConfigured, send_text


def _recipients() -> list[str]:
    settings = repo.get_settings() or {}
    return [n for n in (settings.get("whatsapp_recipient_1"), settings.get("whatsapp_recipient_2")) if n]


def _notify(notif_type: str, body: str, related_lead_id: str | None = None) -> dict:
    """
    Shared path for every alert type: try to send to each configured
    recipient, then log the notification regardless of whether the send
    itself succeeded. log_notification() records what SHOULD have gone
    out -- that's the real, testable part right now; the actual delivery
    is a separate concern that degrades gracefully (see module docstring).
    """
    recipients = _recipients()
    for to in recipients:
        try:
            send_text(to, body)
        except (WhatsAppNotConfigured, httpx.HTTPError):
            pass
    return repo.log_notification(notif_type, recipients, body, related_lead_id)


def notify_hot_lead(lead: dict) -> dict:
    body = (
        f"Hot lead: {lead.get('business_name') or 'Unknown business'} "
        f"scored {lead.get('score')}/10.\n{lead.get('score_reasoning') or ''}"
    )
    return _notify("hot_lead", body, related_lead_id=lead.get("id"))


def notify_founder_found(lead: dict) -> dict:
    body = (
        f"Founder found: {lead.get('founder_name')} at "
        f"{lead.get('business_name') or 'unknown business'}."
    )
    return _notify("founder", body, related_lead_id=lead.get("id"))


def notify_account_warning(account: dict) -> dict:
    body = (
        f"Account warning -- {account.get('label')}: {account.get('warning_type')}. "
        f"{account.get('warning_reason') or ''}"
    )
    return _notify("warning", body)


def notify_approval_reminder(pending_count: int) -> dict:
    body = f"{pending_count} message(s) still awaiting your approval."
    return _notify("approval_reminder", body)


def notify_daily_summary(finished_at: str, duration_seconds: float, leads_found: int, messages_sent: int) -> dict:
    body = (
        f"Daily run finished at {finished_at} (took {duration_seconds:.0f}s). "
        f"{leads_found} leads found, {messages_sent} messages sent."
    )
    return _notify("daily_summary", body)
