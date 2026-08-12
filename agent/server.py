"""
Always-on entry point for the production server (Phase 10) -- what the
Dockerfile actually runs.

Different from `python -m agent.scheduler`'s manual test trigger (see that
module's docstring): this builds the real daily schedule from every active
account's configured run_time and blocks forever, instead of running one
forced cycle and exiting.

build_daily_schedule wires the full daily pipeline: discovery per account
at its own run_time, plus one shared daily run of analysis/message-
generation/sending/approval-reminders/WhatsApp-reply-checking. See
scheduler.py's build_daily_schedule() and DEPLOY.md's "What's wired into
the always-on schedule" section for exactly what that means and the live
verification that's still outstanding before this should actually run
unattended on a real server.

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
