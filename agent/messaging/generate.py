"""
Personalised message generation via Claude Sonnet.

Must read as human, not AI. Greeting uses whichever name exists: business,
team, or founder. Body references that specific lead's weak points and AI
opportunities from Phase 4's analysis. Tone adapts per platform -- LinkedIn
more formal, WhatsApp and Instagram more direct/casual (a DM, not a memo).
"""

from __future__ import annotations

from agent import config
from agent.analysis import client as claude_client
from agent.analysis import prompts
from agent.messaging import style as style_module

_PLATFORM_TONE = {
    "linkedin": "Formal, professional tone -- this is a LinkedIn message to a business contact.",
    "whatsapp": "Direct, casual tone -- this is a WhatsApp message, closer to texting a person "
                "than emailing a company.",
    "instagram": "Casual, warm tone -- this is an Instagram DM, informal but respectful.",
}

_MESSAGE_RULES = """Rules:
- Open with a greeting using the exact name given -- never "Hi there" or "Hello business owner".
- Reference at least one SPECIFIC weak point or AI opportunity from what's given -- never vague \
("I noticed some areas to improve") -- always concrete ("your booking takes 12h" not "your \
booking could be faster").
- Keep it short -- 3-5 sentences, not a pitch deck. This opens a conversation, it doesn't close a sale.
- No exclamation-point enthusiasm, no emojis, no "I hope this message finds you well".
- Never use the words "streamline", "leverage", "revolutionize", or "unlock" -- they read as AI-generated.
- End with a low-pressure question or invitation to reply, not a hard call-to-action.

Respond with ONLY the message text, nothing else -- no preamble, no quotes around it, no "Here's \
a message:"."""

_FOLLOWUP_RULES = """Rules:
- This is a FOLLOW-UP to a message already sent that got no reply -- it must NOT repeat the \
original pitch or re-explain the same weak point in the same words. A lead who ignored the first \
message won't read a near-duplicate any differently.
- Open with the greeting name, but acknowledge briefly that this is a second note (e.g. "circling \
back", "following up", "wanted to check back") -- don't pretend it's the first contact.
- Bring something NEW: either a different angle on the business (a second weak point/opportunity \
if one exists), a lower-friction ask ("even a quick no works"), or genuine curiosity about why they \
haven't responded -- never just restate the original in different words.
- Keep it SHORTER than a first message -- 2-3 sentences. A follow-up earns less of the reader's \
attention than the first message did, not more.
- No exclamation-point enthusiasm, no emojis, no guilt-tripping ("just following up again!!"), no \
"just circling back" as the entire message with nothing else.
- Never use the words "streamline", "leverage", "revolutionize", or "unlock" -- they read as AI-generated.
- End with a low-pressure question or invitation to reply, not a hard call-to-action.

Respond with ONLY the message text, nothing else -- no preamble, no quotes around it, no "Here's \
a message:"."""


def _greeting_name(lead: dict) -> str:
    """
    Whichever name actually exists, in order of preference: a named founder
    (most personal), then the business name. Never a generic fallback like
    "there" gets used silently -- the spec requires a real name, so a lead
    with neither shouldn't reach message generation in the first place.
    """
    return lead.get("founder_name") or lead.get("business_name") or "there"


def build_system_prompt(channel: str, message_style: str, is_followup: bool = False) -> str:
    """
    Assemble the system prompt for one message-generation call: fixed
    intro, the channel's tone, the active style's guide, and the fixed
    rules block. The same (channel, style, is_followup) combination always
    produces the exact same string, which is what lets prompt caching pay
    off here -- every message generated on the same channel/style/kind hits
    the cache, not just repeats within one run.

    `is_followup` swaps in _FOLLOWUP_RULES instead of _MESSAGE_RULES -- a
    follow-up must read as a genuinely different, shorter second note, not
    a re-send of the original pitch (see _FOLLOWUP_RULES for exactly what
    that requires).
    """
    tone = _PLATFORM_TONE.get(channel, _PLATFORM_TONE["linkedin"])
    guide = style_module.style_guide(message_style)
    rules = _FOLLOWUP_RULES if is_followup else _MESSAGE_RULES

    return f"""You write short, personalized outreach messages for Nexaris, a marketing and AI \
automation agency, to send to businesses found on social media. The message must read as if a \
real person wrote it -- no AI-sounding phrasing, no generic templates, no corporate buzzwords.

{tone}

{guide}

{rules}"""


def format_personalization_context(lead: dict, original_body: str | None = None) -> str:
    lines = [
        f"Greeting name: {_greeting_name(lead)}",
        f"Business: {lead.get('business_name') or 'unknown'}",
        f"Industry: {lead.get('industry') or 'unknown'}",
        f"Weak points: {', '.join(lead.get('weak_points') or []) or 'none identified'}",
        f"AI opportunities: {', '.join(lead.get('ai_opportunities') or []) or 'none identified'}",
    ]
    if original_body:
        lines.append(f"Original message already sent (no reply yet): {original_body}")
    return "\n".join(lines)


def generate_message(lead: dict, channel: str, message_style: str, model: str | None = None) -> str:
    """
    Generate one personalized outreach message for one lead on one channel.

    `channel` is "linkedin", "whatsapp", or "instagram" -- affects tone.
    `message_style` is style.DIRECT or style.DISCOVERY -- affects how weak
    points are framed. Returns the raw message text, ready to store on
    messages.body.
    """
    model = model or config.MODEL_MESSAGES
    system = prompts.cacheable_system(build_system_prompt(channel, message_style))
    user_content = format_personalization_context(lead)
    return claude_client.call_text(system, user_content, model)


def generate_followup_message(
    lead: dict, channel: str, message_style: str, original_body: str, model: str | None = None,
) -> str:
    """
    Generate a follow-up message -- distinct from generate_message(): the
    system prompt swaps in _FOLLOWUP_RULES (shorter, acknowledges this is a
    second note, forbids repeating the original pitch) and the original
    sent message is included in the personalization context so Claude can
    actually avoid restating it rather than just being told not to.
    """
    model = model or config.MODEL_MESSAGES
    system = prompts.cacheable_system(build_system_prompt(channel, message_style, is_followup=True))
    user_content = format_personalization_context(lead, original_body=original_body)
    return claude_client.call_text(system, user_content, model)
