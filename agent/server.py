"""
Always-on entry point for the production server (Phase 10) -- what the
Dockerfile actually runs.

Different from `python -m agent.scheduler`'s manual test trigger (see that
module's docstring): this builds the real daily schedule from every active
account's configured run_time and blocks forever, instead of running one
forced cycle and exiting.

Only `run_cycle` is cron-scheduled per account today (build_daily_schedule's
existing behaviour). Discovery/analysis/message-generation/sending and
WhatsApp reply-checking are separate cycle functions in scheduler.py and
sending/whatsapp_reply_check.py, still meant to be triggered manually or
wired into cron here once each is ready to run unattended end-to-end --
wiring the rest in is a follow-on step, not this file's job to guess at.

Run it with:   python -m agent.server
"""

from __future__ import annotations

import signal
import time

from agent.db import repositories as repo
from agent.scheduler import build_daily_schedule

_shutdown = False


def _handle_shutdown(signum, frame) -> None:
    global _shutdown
    _shutdown = True


def main() -> None:
    accounts = repo.list_accounts()
    scheduler = build_daily_schedule(accounts)
    scheduler.start()

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    print(f"Scheduler started with {len(accounts)} account(s). Waiting for scheduled runs...")
    while not _shutdown:
        time.sleep(5)

    scheduler.shutdown()


if __name__ == "__main__":
    main()
