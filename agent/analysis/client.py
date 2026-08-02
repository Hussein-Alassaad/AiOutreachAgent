"""
Shared Claude API client for the analysis and messaging pipelines.

Mirrors db/client.py's pattern from Phase 1: one lazily-created client, reused
by every module that calls Claude, so credentials are only ever read from
config in one place. Lives in analysis/ because it was built first for Phase
4, but nothing about it is analysis-specific -- Phase 5's messaging/generate.py
uses it too.
"""

from __future__ import annotations

import json
import re

from anthropic import Anthropic

from agent import config

_client: Anthropic | None = None


class ClaudeNotConfigured(RuntimeError):
    """Raised when analysis code needs Claude but no API key is set."""


def get_client() -> Anthropic:
    """Return the shared Anthropic client, creating it on first call."""
    global _client

    if _client is None:
        missing = config.missing_required(["ANTHROPIC_API_KEY"])
        if missing:
            raise ClaudeNotConfigured(
                "Missing ANTHROPIC_API_KEY in agent/.env — "
                "get one from console.anthropic.com."
            )
        _client = Anthropic(api_key=config.ANTHROPIC_API_KEY)

    return _client


def is_configured() -> bool:
    """True when a Claude API key is present. Lets callers degrade gracefully."""
    return not config.missing_required(["ANTHROPIC_API_KEY"])


_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def call_json(system_blocks: list[dict], user_content: str, model: str, max_tokens: int = 1024) -> dict:
    """
    Call Claude with a cacheable system prompt (see prompts.cacheable_system)
    and a per-lead user message, expecting a single JSON object back.

    Strips a ```json ... ``` fence if the model wraps its answer in one
    despite the prompt instructing otherwise -- a common enough model habit
    that it's worth handling here once, rather than in every caller.
    """
    response = get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=[{"role": "user", "content": user_content}],
    )
    text = response.content[0].text.strip()
    match = _FENCE_RE.match(text)
    if match:
        text = match.group(1)
    return json.loads(text)


def call_text(system_blocks: list[dict], user_content: str, model: str, max_tokens: int = 1024) -> str:
    """
    Same shape as call_json, but for calls where the response IS the
    deliverable rather than structured fields to parse -- used by message
    generation, where the reply text is the outreach message itself.
    """
    response = get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text.strip()
