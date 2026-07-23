-- ============================================================
-- NEXARIS AI OUTREACH AGENT — ROW LEVEL SECURITY
-- Run this in the Supabase SQL editor AFTER 02_SUPABASE_SCHEMA.sql.
-- ============================================================
--
-- WHY THIS FILE EXISTS
-- Creating a table does not protect it. Until Row Level Security (RLS) is
-- enabled, the access rules are wide open at the policy layer and Supabase
-- will warn you that every table is unprotected.
--
-- THE MODEL (deliberately simple — only 2 users)
-- This system has exactly two people: Hussein and Mahmoud. Both log in through
-- Supabase Auth, so both carry the built-in `authenticated` role. There is no
-- "public visitor" who should ever see a lead or a message.
--
-- So the rule for every table is the same:
--   * Enable RLS.
--   * Allow the `authenticated` role to do everything (select/insert/update/delete).
--   * Everyone else (the `anon` role) gets nothing.
--
-- The AGENT is unaffected by these policies: it connects with the SERVICE ROLE
-- key, which bypasses RLS entirely. RLS only governs the DASHBOARD, which uses
-- the anon key + a logged-in session.
--
-- If later you want finer control (e.g. Mahmoud can approve but not delete
-- leads), you replace the blanket policy on a specific table with per-command
-- policies. For now, both users are trusted owners.
-- ============================================================


-- Helper: enable RLS and grant authenticated users full access on one table.
-- Postgres has no "apply to all tables" shortcut for policies, so we repeat the
-- same three statements per table. Kept explicit so it is easy to read and to
-- later override a single table without touching the rest.

-- ---- accounts ----
alter table accounts enable row level security;
drop policy if exists "authenticated full access" on accounts;
create policy "authenticated full access" on accounts
  for all to authenticated using (true) with check (true);

-- ---- settings ----
alter table settings enable row level security;
drop policy if exists "authenticated full access" on settings;
create policy "authenticated full access" on settings
  for all to authenticated using (true) with check (true);

-- ---- leads ----
alter table leads enable row level security;
drop policy if exists "authenticated full access" on leads;
create policy "authenticated full access" on leads
  for all to authenticated using (true) with check (true);

-- ---- messages ----
alter table messages enable row level security;
drop policy if exists "authenticated full access" on messages;
create policy "authenticated full access" on messages
  for all to authenticated using (true) with check (true);

-- ---- pipeline_history ----
alter table pipeline_history enable row level security;
drop policy if exists "authenticated full access" on pipeline_history;
create policy "authenticated full access" on pipeline_history
  for all to authenticated using (true) with check (true);

-- ---- follow_ups ----
alter table follow_ups enable row level security;
drop policy if exists "authenticated full access" on follow_ups;
create policy "authenticated full access" on follow_ups
  for all to authenticated using (true) with check (true);

-- ---- client_history ----
alter table client_history enable row level security;
drop policy if exists "authenticated full access" on client_history;
create policy "authenticated full access" on client_history
  for all to authenticated using (true) with check (true);

-- ---- notifications_log ----
alter table notifications_log enable row level security;
drop policy if exists "authenticated full access" on notifications_log;
create policy "authenticated full access" on notifications_log
  for all to authenticated using (true) with check (true);

-- ---- runs ----
alter table runs enable row level security;
drop policy if exists "authenticated full access" on runs;
create policy "authenticated full access" on runs
  for all to authenticated using (true) with check (true);

-- ============================================================
-- VERIFY (optional): after running, this should list 9 rows, all rowsecurity=true
--   select tablename, rowsecurity from pg_tables
--   where schemaname = 'public' order by tablename;
-- ============================================================
