-- ============================================================
-- MIGRATION 005 — approval hold reason + configurable reminder threshold
-- Run this in the Supabase SQL editor, after 004_add_runs_skipped_leads.sql.
-- ============================================================
--
-- WHY THIS IS NEEDED
-- Two gaps agent/messaging/approval.py's own docstring flagged as open,
-- both explicitly deferred to "a Phase 9 dashboard decision": there was
-- nowhere to record WHY a message was held (a held message just sat there
-- with no explanation), and the 24h approval-reminder threshold was a
-- hardcoded constant with no dashboard-editable setting behind it --
-- violating core rule R10 (nothing should require a code change to adjust).
-- ============================================================

alter table messages add column if not exists hold_reason text;
alter table settings add column if not exists approval_reminder_hours int not null default 24;

comment on column messages.hold_reason is
  'Why Mohamad held this message -- freeform, optional. A held message with no reason is still valid.';
comment on column settings.approval_reminder_hours is
  'Hours a message can sit "awaiting" before the approval-reminder WhatsApp alert fires. Editable from Settings.';
