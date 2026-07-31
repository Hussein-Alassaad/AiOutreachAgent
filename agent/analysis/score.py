"""
Scores every qualified lead 1-10 with written reasoning.

Components: fit (match to Nexaris services), pain (number and severity of
solvable problems), budget potential (revenue signals). Hot 8-10 / Warm 5-7 /
Cold 1-4. The written WHY is always present -- it was an original requirement.
"""

from __future__ import annotations

from agent import config
from agent.analysis import client as claude_client
from agent.analysis import prompts


def format_score_context(lead: dict, analysis: dict) -> str:
    """
    Scoring needs the DEEP ANALYSIS results (weak points, revenue signals),
    not just the raw bio -- fit/pain/budget can't be judged from raw text
    alone. Takes the lead row plus analyze.analyze_lead()'s output together.
    """
    lines = [
        f"Business: {lead.get('business_name') or 'unknown'}",
        f"Industry: {analysis.get('industry') or 'unknown'}",
        f"Revenue tier: {analysis.get('revenue_tier') or 'unknown'}",
        f"Company size: {analysis.get('company_size') or 'unknown'}",
        f"Ads running: {analysis.get('ads_running')}",
        f"Weak points: {', '.join(analysis.get('weak_points') or []) or 'none identified'}",
        f"AI opportunities: {', '.join(analysis.get('ai_opportunities') or []) or 'none identified'}",
    ]
    return "\n".join(lines)


def score_lead(lead: dict, analysis: dict, model: str | None = None) -> dict:
    """
    Score one lead 1-10. Returns {score, temperature, score_reasoning}.
    """
    model = model or config.MODEL_ANALYSIS
    system = prompts.cacheable_system(prompts.SCORE_SYSTEM_PROMPT)
    return claude_client.call_json(system, format_score_context(lead, analysis), model)
