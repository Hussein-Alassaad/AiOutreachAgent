"""
Pipeline stage movement and timestamped history.

Contacted -> Replied -> Interested -> Meeting Booked -> Deal Closed -> Lost.
The agent auto-moves on send (see sending/instagram_queue.py) and on reply
detection (see reply_detection.py); everything else is a manual move from
the dashboard's kanban board (Phase 9). Every single move is timestamped in
pipeline_history for the audit trail -- this module is the one place that
touches lead.status once a lead has entered the pipeline, so that history is
never accidentally skipped.
"""

from __future__ import annotations

from agent.db import repositories as repo

STAGES = ["contacted", "replied", "interested", "meeting_booked", "deal_closed", "lost"]


def move_stage(lead_id: str, to_stage: str, changed_by: str = "agent") -> dict:
    """
    Move one lead to a new pipeline stage and log it. `changed_by` is
    "agent" for automatic moves (a send, a detected reply) or a human's
    name for a manual move on the dashboard's kanban board -- either way,
    the move is timestamped, since that's the audit trail the spec requires.
    """
    lead = repo.get_lead(lead_id)
    from_stage = lead.get("status") if lead else None
    repo.update_lead(lead_id, {"status": to_stage})
    repo.record_stage_change(lead_id, from_stage, to_stage, changed_by=changed_by)
    return repo.get_lead(lead_id)
