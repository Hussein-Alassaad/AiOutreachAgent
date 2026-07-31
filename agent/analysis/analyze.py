"""
Deep company analysis via Claude Haiku.

Company size, revenue tier, industry, website analysis (design, booking flow,
CTA, load speed), ads activity, all social platforms, the FULL weak points
list, and AI opportunities. Stored in full -- never summarised or truncated.
"""

from __future__ import annotations

from agent import config
from agent.analysis import client as claude_client
from agent.analysis import prompts


def format_lead_context(lead: dict) -> str:
    """
    Turn a lead row into the plain-text block Claude reads. This is the
    NON-cached part of the call -- it's different for every lead, unlike the
    system prompt, which is why it's the user message rather than folded
    into the (cached) system block.
    """
    lines = [
        f"Platform: {lead.get('platform') or 'unknown'}",
        f"Business name: {lead.get('business_name') or 'unknown'}",
        f"Bio/description: {lead.get('bio') or 'none available'}",
        f"Follower/headcount count: {lead.get('follower_count')}",
        f"Website: {lead.get('website') or 'none'}",
    ]
    return "\n".join(lines)


def analyze_lead(lead: dict, model: str | None = None) -> dict:
    """
    Deep analysis of one lead. Returns the parsed fields ready to merge onto
    the lead record: company_size, revenue_tier, industry, website_notes,
    ads_running, social_platforms, weak_points, ai_opportunities.

    Raises agent.analysis.client.ClaudeNotConfigured if no API key is set,
    or json.JSONDecodeError if the model's response wasn't valid JSON despite
    the prompt's instructions -- both are real failure modes callers should
    expect and handle, not paper over.
    """
    model = model or config.MODEL_ANALYSIS
    system = prompts.cacheable_system(prompts.ANALYSIS_SYSTEM_PROMPT)
    return claude_client.call_json(system, format_lead_context(lead), model)
