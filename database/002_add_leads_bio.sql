-- ============================================================
-- MIGRATION 002 — add raw bio/description storage to leads
-- Run this in the Supabase SQL editor, after 02_SUPABASE_SCHEMA.sql
-- and database/policies.sql.
-- ============================================================
--
-- WHY THIS IS NEEDED
-- The original schema stores the RESULTS of analysis (weak_points,
-- ai_opportunities, founder_source_phrase, etc.) but never stored the raw
-- bio/description text that Phase 3 discovery actually reads off a profile.
-- Phase 4's Claude analysis pipeline needs that original text as its input —
-- without it, there is nothing for the deep analysis, founder detection, or
-- WhatsApp number detection to actually read.
--
-- This was caught while building Phase 4, not before, because Phase 3 never
-- needed the raw text itself — only the qualification decision derived from
-- it. Adding the column now, before any real production leads exist, is a
-- clean fix rather than a workaround.
-- ============================================================

alter table leads add column if not exists bio text;

comment on column leads.bio is
  'Raw bio/description text as scraped from the profile — the input Phase 4''s Claude analysis reads.';
