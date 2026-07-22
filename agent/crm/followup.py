"""
Follow-up and re-engagement scheduling. Enforces the 2-contact maximum.

Built in Phase 8. Not implemented yet.

Follow-up is a PER-LEAD decision -- Hussein sets on/off and the exact timing
himself; the agent never picks a delay. Re-engagement covers leads who replied
then went cold. Everything pauses the instant a lead replies. Max 2 contacts.
"""
