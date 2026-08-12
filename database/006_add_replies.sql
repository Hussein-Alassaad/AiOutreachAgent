-- ============================================================
-- MIGRATION 006 — capture actual reply content, not just "a reply happened"
-- Run this in the Supabase SQL editor, after 005_add_approval_settings.sql.
-- ============================================================
--
-- WHY THIS IS NEEDED
-- crm/reply_detection.py's handle_reply_detected() only ever moved the lead
-- to "replied" -- it never persisted what the lead actually said or which
-- of our accounts received it. Both LinkedIn's and WhatsApp's reply
-- checkers already read the reply text off the platform to decide "is this
-- newer than our last send", then discarded it once the boolean was
-- computed. Hussein flagged this directly: the dashboard needs to show what
-- a client replied and from which account, not just a status flip.
--
-- A separate table, not a column on `messages` -- `messages` is our
-- OUTBOUND sends; a lead can reply more than once, and a reply isn't a
-- message we generated or approved, so it doesn't belong in that table's
-- shape (approval_status, generated body, etc. are all meaningless here).
-- ============================================================

create table if not exists replies (
    id uuid primary key default gen_random_uuid(),
    lead_id uuid not null references leads(id) on delete cascade,
    account_id uuid references accounts(id) on delete set null,
    channel text not null check (channel in ('linkedin', 'whatsapp')),
    body text not null,
    replied_at timestamptz not null,
    detected_at timestamptz not null default now()
);

create index if not exists replies_lead_id_idx on replies(lead_id);
create index if not exists replies_account_id_idx on replies(account_id);

comment on table replies is
  'Actual inbound reply content from a lead, one row per reply. account_id is whichever of our accounts owns the conversation the reply landed in.';
comment on column replies.replied_at is
  'When the lead sent it, per the platform''s own timestamp -- not when our polling cycle happened to notice it (see detected_at for that).';
comment on column replies.detected_at is
  'When our reply-checker actually found this reply -- distinct from replied_at, which is the platform''s own send time.';

-- Same model as every other table -- see database/policies.sql's header for
-- why this is a single blanket policy rather than per-command ones (two
-- trusted users, agent bypasses RLS entirely via the service role key).
alter table replies enable row level security;
drop policy if exists "authenticated full access" on replies;
create policy "authenticated full access" on replies
  for all to authenticated using (true) with check (true);
