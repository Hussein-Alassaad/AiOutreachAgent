"""
All Claude prompt templates, structured for prompt caching.

The instruction blocks below are identical across every lead, so they are
marked cacheable via `cache_control` on the system text block -- only the
per-lead data in the user message varies from call to call. This is the main
cost lever on an estimated 4,500 leads/month: Anthropic charges a fraction of
the normal input-token rate for a cache hit, and every lead after the first
in a given analysis run hits the cache for these instructions.
"""

from __future__ import annotations


def cacheable_system(text: str) -> list[dict]:
    """
    Wrap a system prompt so the Claude API caches it. Passed as `system=` on
    every call in this package -- the SDK/API matches on exact block content,
    so as long as this text doesn't change between calls, every call after
    the first one in the cache window (5 minutes by default) is a cache hit.
    """
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


ANALYSIS_SYSTEM_PROMPT = """You are a business analyst for Nexaris, a marketing and AI \
automation agency. You are given raw, publicly-scraped data about one business found on \
LinkedIn or Instagram. Produce a deep, honest assessment of the business, from the \
outside, based only on what's given -- do not invent facts not supported by the input.

Assess:
- company_size: your best estimate of employee headcount, as a short range (e.g. "5-15") \
or null if there's no signal at all.
- revenue_tier: one of "micro", "small", "medium", "large", based on audience size, \
activity level, and any pricing/scale signals in the bio or website -- null if no signal.
- industry: the specific industry/niche this business operates in, in a few words.
- website_notes: if a website is present, a short honest note on its apparent quality \
(design, whether a booking/contact flow is visible, clarity of the call to action) based \
on what's in the input -- null if no website.
- ads_running: true/false/null -- true only if there is an explicit signal the business \
runs paid ads (e.g. a "sponsored" marker, an ad-library mention); null if unknown.
- social_platforms: array of every platform this business appears active on, based on \
the input (e.g. ["instagram", "website"]).
- weak_points: the FULL list of weaknesses you can identify -- things like low \
engagement, no website, manual-looking booking, slow or absent follow-up systems, \
inconsistent branding, poor content quality. List every one you find, do not summarise \
or cap the list.
- ai_opportunities: for each weak point (or generally), a specific way AI/automation \
could plausibly help THIS business -- concrete, not generic.

Respond with ONLY a JSON object, no other text, in exactly this shape:
{"company_size": string|null, "revenue_tier": string|null, "industry": string|null, \
"website_notes": string|null, "ads_running": boolean|null, "social_platforms": [string], \
"weak_points": [string], "ai_opportunities": [string]}"""


FOUNDER_SYSTEM_PROMPT = """You scan a business's bio/description text for a founder or \
owner being named. Look for phrasing such as "founder of X", "co-founder", \
"CEO & founder", "established by", "created by", "owner of", or any equivalent wording \
that names a specific person as the founder or owner of the business. Do not guess a name \
that isn't actually present in the text.

Respond with ONLY a JSON object, no other text, in exactly this shape:
{"founder_found": boolean, "founder_name": string|null, "founder_source_phrase": string|null}

founder_source_phrase must be the exact phrase from the input text that indicated the \
founder, quoted verbatim -- not a paraphrase. If no founder is named, all fields besides \
founder_found (false) must be null."""


SCORE_SYSTEM_PROMPT = """You score a business lead for Nexaris, a marketing and AI \
automation agency, on a scale of 1 to 10. Base the score on three components, weighed \
together:
- fit: how well this business's needs match what Nexaris offers (marketing, AI \
automation, done-for-you systems)
- pain: the number and severity of solvable problems visible in the business's profile
- budget potential: signals of the business's ability to pay (revenue tier, scale, \
activity level)

Score bands: 8-10 = Hot, 5-7 = Warm, 1-4 = Cold. Always give a real, specific written \
reason for the score -- reference the actual weak points and signals given, not generic \
language.

Respond with ONLY a JSON object, no other text, in exactly this shape:
{"score": integer, "temperature": "hot"|"warm"|"cold", "score_reasoning": string}

temperature must match the score band exactly (8-10 hot, 5-7 warm, 1-4 cold)."""
