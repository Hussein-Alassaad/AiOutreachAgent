-- ============================================================
-- MIGRATION 004 — add skipped-lead tracking to runs
-- Run this in the Supabase SQL editor, after 003_add_leads_engagement.sql.
-- ============================================================
--
-- WHY THIS IS NEEDED
-- Discovery used to abort an entire account's run the moment one lead
-- failed to process (a page timeout, a missing field, anything) -- every
-- lead after it in that batch was silently lost, with no record of what
-- happened or why. Discovery now catches failures per-lead instead of
-- per-run, so the rest of the batch keeps going -- this column is where
-- each skipped lead's identifier and reason land, so the dashboard can show
-- "this business didn't go through because X" instead of the run just
-- looking short with no explanation.
-- ============================================================

alter table runs add column if not exists skipped_leads jsonb default '[]'::jsonb;

comment on column runs.skipped_leads is
  'Per-lead failures during this run: [{platform, identifier, reason}, ...]. Empty array when nothing was skipped.';
