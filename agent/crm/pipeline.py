"""
Pipeline stage movement and timestamped history.

Built in Phase 8. Not implemented yet.

New -> Contacted -> Replied -> Interested -> Meeting Booked -> Deal Closed ->
Lost. The agent auto-moves on send and on reply detection; everything else is
manual. Every single move is timestamped for the audit trail.
"""
