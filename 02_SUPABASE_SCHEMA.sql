-- ============================================================
-- NEXARIS AI OUTREACH AGENT — SUPABASE SCHEMA
-- Run this in the Supabase SQL editor.
-- Walk Hussein through it table by table before running.
-- ============================================================

-- Enable UUID generation
create extension if not exists "uuid-ossp";

-- ============================================================
-- ACCOUNTS — the 3 outreach accounts, each with its own proxy
-- ============================================================
create table if not exists accounts (
    id                uuid primary key default uuid_generate_v4(),
    label             text not null,                 -- e.g. "Account 1"
    platform_handles  jsonb default '{}'::jsonb,     -- {linkedin: "...", instagram: "..."}
    run_time          time not null default '09:00', -- individual daily run time
    ig_daily_limit    int  not null default 20,      -- editable
    linkedin_daily_limit int not null default 30,    -- editable
    proxy_host        text,                          -- proxy config slot (nullable — can be empty at launch)
    proxy_port        text,
    proxy_username    text,
    proxy_password    text,
    warmup_current_limit int default 5,              -- current warm-up cap, ramps up over weeks
    status            text not null default 'active',-- active | warned | paused
    warning_type      text,                          -- e.g. "captcha", "login_challenge", "limit_reached"
    warning_reason    text,                          -- human-readable explanation
    redistribute_flag boolean default false,         -- Hussein's manual decision when paused
    last_active_at    timestamptz,
    created_at        timestamptz default now()
);

-- ============================================================
-- SETTINGS — single-row global config, editable from dashboard
-- ============================================================
create table if not exists settings (
    id                uuid primary key default uuid_generate_v4(),
    -- targeting
    target_niche      text default '',
    target_industry   text default '',
    target_location   text default '',               -- country / market
    target_business_type text default '',
    outreach_languages text[] default array['English'],
    -- message style
    message_style     text default 'discovery',      -- direct | discovery
    style_duration_days int default 7,                -- rotate style after N days
    style_last_rotated_at timestamptz default now(),
    -- follow-up / contact rules
    default_recontact_gap_days int default 30,        -- default gap before 2nd touch
    max_contacts_per_lead int default 2,              -- HARD RULE — do not exceed
    -- notifications
    whatsapp_recipient_1 text,                        -- e.g. Hussein
    whatsapp_recipient_2 text,                        -- e.g. Mahmoud
    -- approval
    approval_required boolean default true,           -- permanent hard gate
    updated_at        timestamptz default now()
);

-- ============================================================
-- LEADS — every business the agent discovers
-- ============================================================
create table if not exists leads (
    id                uuid primary key default uuid_generate_v4(),
    account_id        uuid references accounts(id),  -- which account found it
    platform          text not null,                 -- linkedin | instagram
    -- profile basics
    business_name     text,
    profile_url       text,
    profile_photo_url text,
    follower_count    int,
    industry          text,
    website           text,
    social_platforms  jsonb default '[]'::jsonb,      -- all platforms they're active on
    -- deep analysis
    company_size      text,
    revenue_tier      text,
    ads_running       boolean,
    weak_points       jsonb default '[]'::jsonb,      -- full list, shown in dashboard
    ai_opportunities  jsonb default '[]'::jsonb,
    -- founder detection
    founder_found     boolean default false,
    founder_name      text,
    founder_source_phrase text,                       -- the bio phrase that matched
    -- whatsapp detection
    whatsapp_found    boolean default false,
    whatsapp_number   text,
    -- scoring
    score             int,                            -- 1-10
    temperature       text,                           -- hot | warm | cold
    score_reasoning   text,                           -- the WHY
    -- message
    generated_message text,
    message_style_used text,
    -- lifecycle
    status            text not null default 'discovered',
                      -- discovered | analyzed | awaiting_approval | approved
                      -- | manual_send_pending | contacted | replied | interested
                      -- | meeting_booked | deal_closed | lost
    contact_count     int default 0,                  -- 0, 1, or 2 (max 2)
    first_contacted_at timestamptz,
    notes             text,
    created_at        timestamptz default now(),
    updated_at        timestamptz default now()
);

-- ============================================================
-- MESSAGES — every message sent/queued per lead
-- ============================================================
create table if not exists messages (
    id                uuid primary key default uuid_generate_v4(),
    lead_id           uuid references leads(id) on delete cascade,
    channel           text not null,                 -- linkedin | whatsapp | instagram
    body              text not null,
    is_followup       boolean default false,
    is_reengagement   boolean default false,
    -- approval
    approval_status   text default 'awaiting',       -- awaiting | approved | held | edited
    approved_by       text,                          -- Mahmoud
    approved_at       timestamptz,
    edited_body       text,                          -- if Mahmoud edited it
    -- send
    send_status       text default 'pending',        -- pending | sent | manual_pending | failed
    sent_at           timestamptz,
    sent_via_account  uuid references accounts(id),
    created_at        timestamptz default now()
);

-- ============================================================
-- PIPELINE_HISTORY — every stage change, timestamped
-- ============================================================
create table if not exists pipeline_history (
    id                uuid primary key default uuid_generate_v4(),
    lead_id           uuid references leads(id) on delete cascade,
    from_stage        text,
    to_stage          text not null,
    changed_by        text default 'agent',          -- agent | hussein | mahmoud
    changed_at        timestamptz default now()
);

-- ============================================================
-- FOLLOW_UPS — scheduled follow-ups / re-engagements per lead
-- ============================================================
create table if not exists follow_ups (
    id                uuid primary key default uuid_generate_v4(),
    lead_id           uuid references leads(id) on delete cascade,
    enabled           boolean default false,          -- per-lead on/off (Hussein's choice)
    scheduled_for     timestamptz,                    -- Hussein sets the timing
    is_reengagement   boolean default false,
    status            text default 'scheduled',       -- scheduled | sent | cancelled | paused
    created_at        timestamptz default now()
);

-- ============================================================
-- CLIENT_HISTORY — permanent record of EVERY analyzed lead
-- (never resets; separate from the live feed)
-- ============================================================
create table if not exists client_history (
    id                uuid primary key default uuid_generate_v4(),
    lead_id           uuid references leads(id) on delete set null,
    business_name     text,
    platform          text,
    industry          text,
    score             int,
    temperature       text,
    weak_points       jsonb,
    founder_found     boolean,
    founder_name      text,
    contacted         boolean default false,
    snapshot          jsonb,                          -- full lead snapshot at analysis time
    analyzed_at       timestamptz default now()
);

-- ============================================================
-- NOTIFICATIONS_LOG — every WhatsApp alert sent
-- ============================================================
create table if not exists notifications_log (
    id                uuid primary key default uuid_generate_v4(),
    type              text not null,                 -- hot_lead | founder | warning | approval_reminder | daily_summary
    recipients        text[],                        -- numbers it was sent to
    body              text,
    related_lead_id   uuid references leads(id) on delete set null,
    sent_at           timestamptz default now()
);

-- ============================================================
-- RUNS — one row per daily agent run (for finish-time tracking)
-- ============================================================
create table if not exists runs (
    id                uuid primary key default uuid_generate_v4(),
    account_id        uuid references accounts(id),
    started_at        timestamptz default now(),
    finished_at       timestamptz,
    leads_found       int default 0,
    messages_sent     int default 0,
    status            text default 'running',        -- running | completed | error
    notes             text
);

-- ============================================================
-- HELPFUL INDEXES
-- ============================================================
create index if not exists idx_leads_status      on leads(status);
create index if not exists idx_leads_temperature on leads(temperature);
create index if not exists idx_leads_platform    on leads(platform);
create index if not exists idx_leads_account     on leads(account_id);
create index if not exists idx_messages_lead     on messages(lead_id);
create index if not exists idx_messages_approval on messages(approval_status);
create index if not exists idx_pipeline_lead     on pipeline_history(lead_id);
create index if not exists idx_followups_sched   on follow_ups(scheduled_for);

-- ============================================================
-- SEED — insert the 3 accounts and one settings row
-- (Hussein: replace the handles later with real account info)
-- ============================================================
insert into accounts (label, run_time, ig_daily_limit, linkedin_daily_limit, warmup_current_limit)
values
  ('Account 1', '09:00', 20, 30, 5),
  ('Account 2', '11:00', 20, 30, 5),
  ('Account 3', '13:00', 20, 30, 5)
on conflict do nothing;

insert into settings (message_style, style_duration_days, default_recontact_gap_days)
select 'discovery', 7, 30
where not exists (select 1 from settings);

-- ============================================================
-- END OF SCHEMA
-- ============================================================
