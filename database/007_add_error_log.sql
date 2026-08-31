-- ============================================================
-- MIGRATION 007 — capture every silently-swallowed pipeline failure
-- Run this in the Supabase SQL editor, after 006_add_replies.sql.
-- ============================================================
--
-- WHY THIS IS NEEDED
-- scheduler.py already catches per-lead/per-message failures everywhere
-- (12 try/except blocks) so one bad lead never stops a whole run -- but the
-- caught exception's details were only ever appended to an in-memory
-- results list that gets discarded the moment the function returns. There
-- was no way to know a send/analysis/generation step failed without
-- manually re-running it and reading the terminal. Hussein flagged this
-- directly: he needs a way to see what the agent failed to do, not just
-- assume everything worked.
-- ============================================================

create table if not exists error_log (
    id                uuid primary key default gen_random_uuid(),
    stage             text not null,                  -- discovery | analysis | message_generation | sending | reply_check | followup
    channel           text,                            -- linkedin | whatsapp | instagram | null (not channel-specific, e.g. analysis)
    lead_id           uuid references leads(id) on delete set null,
    account_id        uuid references accounts(id) on delete set null,
    error_message     text not null,
    is_expected        boolean not null default false, -- true for known/benign outcomes (e.g. "no Message button") vs a real failure
    resolved          boolean not null default false,  -- Hussein/Mohamad can mark an error as handled
    occurred_at       timestamptz not null default now()
);

create index if not exists error_log_stage_idx on error_log(stage);
create index if not exists error_log_occurred_at_idx on error_log(occurred_at desc);
create index if not exists error_log_resolved_idx on error_log(resolved);

comment on table error_log is
  'Every failure the pipeline catches, not just the ones that crash the whole run. is_expected distinguishes a normal "cannot send this way" outcome (e.g. NoMessageButtonAvailable) from a genuine error worth investigating.';
comment on column error_log.is_expected is
  'True for known, non-broken outcomes (no LinkedIn Message button, WhatsApp not configured yet) so the dashboard can separate "real problems" from "normal skip reasons" by default.';

alter table error_log enable row level security;
drop policy if exists "authenticated full access" on error_log;
create policy "authenticated full access" on error_log
  for all to authenticated using (true) with check (true);
