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

import logging
import signal
import time

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED

from agent.db import repositories as repo
from agent.scheduler import build_daily_schedule

_shutdown = False

# LIVE-CONFIRMED 2026-09-13: this process (what the Dockerfile actually runs)
# never called logging.basicConfig(), so the root logger had no handler at
# all. APScheduler logs a job's start/success/failure through the standard
# `apscheduler` logger -- with no handler attached, every one of those lines
# went nowhere, `docker logs` showed only the one startup print forever, and
# a real morning where every scheduled job silently did nothing (or silently
# failed) looked identical to a healthy idle scheduler. This is what let that
# go unnoticed until checked by hand well after the fact. INFO level, plain
# stdout -- Docker already captures stdout, no file handler needed.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("agent.server")


def _handle_shutdown(signum, frame) -> None:
    global _shutdown
    _shutdown = True


def _log_job_event(event) -> None:
    """
    Surface every job's outcome in `docker logs`, not just startup. A failed
    job by default is swallowed by APScheduler's executor and only reaches a
    logger -- see this module's logging.basicConfig comment for why that was
    previously invisible. EVENT_JOB_MISSED fires when a job couldn't start
    within its misfire_grace_time (60s, see scheduler.py's job_defaults),
    e.g. if the process was busy or restarting right at the scheduled minute.
    """
    if event.exception:
        logger.error(
            "JOB FAILED: %s -> %s: %s",
            event.job_id, type(event.exception).__name__, event.exception,
            exc_info=(type(event.exception), event.exception, event.traceback) if event.traceback else None,
        )
    elif event.code == EVENT_JOB_MISSED:
        logger.warning("JOB MISSED (did not start within its grace period): %s", event.job_id)
    else:
        logger.info("job finished: %s", event.job_id)


def main() -> None:
    # PORTED 2026-08-20: build_daily_schedule() is now multi-tenant and
    # discovers its own tenants/accounts internally (see its docstring) --
    # no longer takes an accounts list. tenant_ids is only fetched here for
    # the startup log line.
    tenant_ids = repo.list_active_tenant_ids()
    scheduler = build_daily_schedule()
    scheduler.add_listener(_log_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED)
    scheduler.start()

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info("Scheduler started for %d tenant(s). Waiting for scheduled runs...", len(tenant_ids))
    for job in sorted(scheduler.get_jobs(), key=lambda j: str(j.trigger)):
        logger.info("  scheduled: %s -> %s", job.id, job.trigger)
    while not _shutdown:
        time.sleep(5)

    scheduler.shutdown()


if __name__ == "__main__":
    main()
