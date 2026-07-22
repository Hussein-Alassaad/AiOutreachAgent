"""
Central configuration for the agent.

Every secret and environment-specific value is read here and nowhere else, so there is
exactly one place to look when something is misconfigured. Values come from agent/.env
(never committed) — see .env.example in the repo root for the full list.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# agent/.env sits next to this file. load_dotenv is a no-op if the file doesn't exist,
# which is what we want on the server where real env vars are set by the OS instead.
AGENT_DIR = Path(__file__).parent
load_dotenv(AGENT_DIR / ".env")


def _get(key: str, default: str | None = None) -> str | None:
    """Read an env var, treating an empty string the same as missing."""
    value = os.getenv(key, default)
    return value if value else default


# ── Supabase ──────────────────────────────────────────────────────────────────
# The agent uses the SERVICE ROLE key (full read/write, bypasses RLS) because it runs
# on a trusted server. The dashboard uses the anon key instead — never swap these.
SUPABASE_URL = _get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = _get("SUPABASE_SERVICE_KEY")

# ── Claude API ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")

# Model split per the spec: Haiku for the high-volume analysis work (cost-efficient),
# Sonnet for message generation (better writing quality drives reply rates).
MODEL_ANALYSIS = _get("MODEL_ANALYSIS", "claude-haiku-4-5-20251001")
MODEL_MESSAGES = _get("MODEL_MESSAGES", "claude-sonnet-5")

# ── WhatsApp (sending + the 5 notification types) ─────────────────────────────
# Provider is not finalised yet (open decision Q1 in REQUIREMENTS_COVERAGE.md).
# Phase 7 will build behind an interface so this can be swapped without code changes.
WHATSAPP_PROVIDER = _get("WHATSAPP_PROVIDER", "twilio")
WHATSAPP_API_KEY = _get("WHATSAPP_API_KEY")
WHATSAPP_API_SECRET = _get("WHATSAPP_API_SECRET")
WHATSAPP_FROM_NUMBER = _get("WHATSAPP_FROM_NUMBER")

# ── Runtime ───────────────────────────────────────────────────────────────────
# Timezone that per-account run times are interpreted in.
TIMEZONE = _get("TIMEZONE", "Asia/Beirut")

# When true, Playwright shows a visible browser window. Useful while building
# discovery in Phase 3; must be false on the headless server.
HEADLESS = _get("HEADLESS", "true").lower() == "true"

# Safety brake for local testing — caps leads per account regardless of the dashboard
# setting. Leave empty in production so the dashboard limits apply.
DEV_MAX_LEADS_PER_ACCOUNT = _get("DEV_MAX_LEADS_PER_ACCOUNT")


def missing_required(keys: list[str]) -> list[str]:
    """
    Return which of the given config keys are unset.

    Each phase calls this with only the keys it actually needs, so Phase 0 can run
    with an empty .env while Phase 4 can refuse to start without a Claude API key.
    """
    return [key for key in keys if not globals().get(key)]
