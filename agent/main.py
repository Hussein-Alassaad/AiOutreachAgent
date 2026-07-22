"""
Nexaris AI Outreach Agent — entry point.

Phase 0: this runs a self-check and reports what is configured and what isn't.
It does no real work yet — no browsing, no Claude calls, no sending.

From Phase 2 onward this becomes the daily run loop: for each account, at its own
scheduled time, open a session and work through Tasks 1-17 from the spec.

Run it with:   python -m agent.main
"""

import sys

from agent import config
from agent.db import client as db


def _status(ok: bool) -> str:
    """Green tick / grey dash, so the output is scannable at a glance."""
    return "[ok]  " if ok else "[--]  "


def self_check() -> bool:
    """
    Report which parts of the system are configured.

    Nothing here is fatal in Phase 0 — an empty .env is the expected state right after
    setup. The point is to show Hussein exactly what still needs filling in and which
    phase will need it.
    """
    print("=" * 62)
    print("  NEXARIS AI OUTREACH AGENT - setup self-check")
    print("=" * 62)
    print()

    checks = [
        ("Python runtime", True, f"Python {sys.version.split()[0]}"),
        (
            "Supabase credentials",
            db.is_configured(),
            "set" if db.is_configured() else "not set - needed for Phase 1",
        ),
        (
            "Claude API key",
            bool(config.ANTHROPIC_API_KEY),
            "set" if config.ANTHROPIC_API_KEY else "not set - needed for Phase 4",
        ),
        (
            "WhatsApp credentials",
            bool(config.WHATSAPP_API_KEY),
            "set" if config.WHATSAPP_API_KEY else "not set - needed for Phase 7",
        ),
    ]

    for label, ok, detail in checks:
        print(f"  {_status(ok)}{label:<24} {detail}")

    # Only attempt a live connection when credentials exist, otherwise the error is
    # just noise repeating what the check above already said.
    if db.is_configured():
        ok, message = db.check_connection()
        print(f"  {_status(ok)}{'Database reachable':<24} {message}")

    print()
    print(f"  Models:    analysis={config.MODEL_ANALYSIS}")
    print(f"             messages={config.MODEL_MESSAGES}")
    print(f"  Timezone:  {config.TIMEZONE}")
    print(f"  Headless:  {config.HEADLESS}")
    print()
    print("-" * 62)
    print("  Phase 0 complete: the agent runs. It does no real work yet.")
    print("  Next: Phase 1 - create the database schema in Supabase.")
    print("-" * 62)

    return True


def main() -> int:
    """Entry point. Returns a process exit code."""
    self_check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
