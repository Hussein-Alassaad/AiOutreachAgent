"""
Founder detection from profile bios.

Matches 'founder of X', 'co-founder', 'CEO & founder', 'established by',
'created by', 'owner of', and equivalents. Stores the founder name AND the
phrase that matched. Founder outreach itself is always manual.
"""

from __future__ import annotations

from agent import config
from agent.analysis import client as claude_client
from agent.analysis import prompts


def detect_founder(lead: dict, model: str | None = None) -> dict:
    """
    Scan a lead's bio for a named founder/owner. Returns
    {founder_found, founder_name, founder_source_phrase}.

    Uses Claude rather than a fixed regex list because bios phrase this in
    many ways not covered by any fixed phrase set -- the known phrases from
    the spec are given to the model as examples of the pattern to look for,
    not as an exhaustive list it's limited to.
    """
    model = model or config.MODEL_ANALYSIS
    system = prompts.cacheable_system(prompts.FOUNDER_SYSTEM_PROMPT)
    bio = lead.get("bio") or ""
    user_content = f"Bio/description text:\n{bio}" if bio else "Bio/description text: (none available)"
    return claude_client.call_json(system, user_content, model)
