"""
Finds a public WhatsApp number in bio, description, or website text.

Only publicly listed business numbers -- never guessed, never scraped from
private sources. A hit opens WhatsApp as an ADDITIONAL channel alongside the
primary one, not as a replacement.

Deterministic pattern matching, not a Claude call -- a WhatsApp number is
either explicitly present in a recognisable form (a wa.me link, or a number
clearly labelled "WhatsApp") or it isn't. There's no judgement call here for
a model to usefully add, and matching this way means the result is exactly
reproducible and needs no API key to test.
"""

from __future__ import annotations

import re

# api.whatsapp.com/send?phone=... is checked first -- it's the least ambiguous
# possible signal, a direct WhatsApp deep link with the number as a URL param.
_API_LINK_RE = re.compile(r"api\.whatsapp\.com/send\?phone=(\+?\d{7,15})", re.IGNORECASE)

# wa.me/<number> short links -- equally unambiguous.
_WA_ME_RE = re.compile(r"(?:https?://)?(?:api\.)?wa\.me/(\+?\d{7,15})", re.IGNORECASE)

# The word "WhatsApp" followed within a short distance by something that looks
# like a phone number. Lower confidence than a link, but still a real,
# explicit label -- not a guess. Requires the digits to actually look like a
# phone number (7+ digits) to avoid matching unrelated short numbers.
_LABEL_RE = re.compile(r"whats\s*app[^0-9+]{0,15}(\+?[\d][\d\s\-()]{6,17}\d)", re.IGNORECASE)


def _normalise(raw: str) -> str:
    """Strip everything but digits and a leading +."""
    return re.sub(r"[^\d+]", "", raw)


def detect_whatsapp_in_text(text: str) -> dict:
    """
    Pure text-matching core, kept separate from detect_whatsapp() below so it
    can be tested directly with plain strings, no lead record needed.
    """
    for pattern in (_API_LINK_RE, _WA_ME_RE, _LABEL_RE):
        match = pattern.search(text or "")
        if match:
            return {"whatsapp_found": True, "whatsapp_number": _normalise(match.group(1))}

    return {"whatsapp_found": False, "whatsapp_number": None}


def detect_whatsapp(lead: dict) -> dict:
    """
    Scan a lead's bio and website URL for a publicly listed WhatsApp number.
    Returns {whatsapp_found, whatsapp_number}.
    """
    combined = "\n".join(filter(None, [lead.get("bio"), lead.get("website")]))
    return detect_whatsapp_in_text(combined)
