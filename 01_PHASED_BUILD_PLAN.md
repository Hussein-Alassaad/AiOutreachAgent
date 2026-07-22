# PHASED BUILD PLAN — Nexaris AI Outreach Agent

Build in this exact order. **Stop at the end of every phase, summarize, show how to test, and wait for Hussein's confirmation before continuing.**

Update `PROGRESS.md` after each phase.

---

## PHASE 0 — Project setup & foundations

**Goal:** a clean, running skeleton for both the agent and the dashboard.

- Set up the repo using the structure in `03_PROJECT_STRUCTURE.md`
- Initialize the Python backend/agent project (with a virtual environment, `requirements.txt`)
- Initialize the React + Tailwind dashboard (Vite)
- Set up the Supabase project connection (env vars, client setup in both backend and frontend)
- Create a `.env.example` listing every environment variable the project will need (Claude API key, Supabase URL/key, proxy credentials per account, WhatsApp API credentials, etc.)
- Create `PROGRESS.md`
- Confirm both apps run (dashboard shows a blank page, agent runs a "hello" script)

**Checkpoint:** Hussein can run the dashboard locally and run the agent script. Nothing does real work yet.

---

## PHASE 1 — Database schema

**Goal:** the full database exists in Supabase.

- Run `02_SUPABASE_SCHEMA.sql` in Supabase (walk Hussein through doing this)
- Verify every table exists: `leads`, `messages`, `pipeline_history`, `client_history`, `accounts`, `settings`, `notifications_log`, `follow_ups`
- Set up Row Level Security appropriately (2 users only)
- Create the backend data-access layer (functions to read/write each table)
- Seed the `settings` table with sensible defaults (niche empty, 20 IG / 30 LinkedIn per account, default run times, message style = "discovery", style duration = 7 days, etc.)

**Checkpoint:** Hussein can see all tables in Supabase and the backend can read/write a test record.

---

## PHASE 2 — Agent core: account pool & browser sessions

**Goal:** the agent can open isolated browser sessions per account, each on its own proxy.

- Build the **account pool manager**: loads the 3 accounts, each with its own run time, daily limit, and **proxy config slot** (can be empty for now)
- Build Playwright session management: one isolated browser context per account, routed through that account's proxy IP (if set) — sessions must NEVER share context or IP
- Build the **account health check**: detect login challenges, CAPTCHAs, restrictions; when detected, record warning **type + reason**, pause the account, and log it
- Build the **scheduler**: each account runs at its own configured time; agent records run start and **finish time**
- Build the **redistribution decision hook**: when an account is paused, do NOT auto-redistribute — surface the decision to Hussein (a flag in the DB the dashboard will read)

**Checkpoint:** Hussein can trigger a run; the agent opens sessions per account (no proxy yet is fine), respects run times, and logs a warning correctly if a session fails. Test at LOW volume.

---

## PHASE 3 — Discovery: LinkedIn & Instagram

**Goal:** the agent finds and reads real profiles.

- **LinkedIn discovery:** search by niche/industry/company size/location/activity; read company name, description, headcount, website, recent posts, public contact info; target 30/account (from settings)
- **Instagram discovery:** search hashtags/explore/location tags; read bio, followers, posts, engagement; target 20/account (from settings)
- **Qualification logic:** decide if each profile is a real business worth pursuing; skip personal/inactive/irrelevant accounts
- Make discovery **agentic** — if a search returns weak results, the agent adapts its search terms rather than collecting junk
- Store raw discovered data in `leads` with status "discovered"

**Checkpoint:** Hussein runs the agent and sees real qualified leads appear in the database from both platforms. Test at low volume with a few profiles.

---

## PHASE 4 — Claude analysis pipeline

**Goal:** every lead is deeply analyzed, scored, and enriched by Claude.

- **Deep analysis (Haiku):** company size, revenue tier, industry, website analysis, ads activity, social presence, full weak points list, AI opportunities
- **Founder detection (Haiku):** scan bio for founder phrases; if found, store founder name + set the founder flag
- **WhatsApp number detection:** check bio/description/website for a public WhatsApp number; store if found
- **Lead scoring (Haiku):** score 1–10 with written reasoning; label Hot/Warm/Cold
- **Save to `client_history`** permanently (full record, whether or not it's later contacted)
- Use **prompt caching** on the repeated instruction template to cut cost
- Store everything on the `leads` record

**Checkpoint:** Hussein sees leads in the DB now fully enriched — score, reasoning, weak points, founder flag, WhatsApp flag. Verify the scoring reasoning makes sense on real examples.

---

## PHASE 5 — Message generation

**Goal:** every qualified lead gets a personalized, human-sounding message.

- **Message generation (Sonnet):** personalized greeting (business/team/founder name), references specific weak points/opportunities, human tone
- Respect the **message style** setting (Direct = names weak points; Discovery = softer/curiosity) and **style duration** (rotate after N days)
- Use the personalization-line style from the spec as a guide (e.g. "I noticed you're spending on Meta ads, but your follow-up is manual and your booking takes 12h")
- Adapt tone per platform (LinkedIn more formal, WhatsApp more direct)
- Store the generated message on the lead, status "awaiting_approval"

**Checkpoint:** Hussein reviews generated messages for real leads and confirms they read as human and personalized.

---

## PHASE 6 — Approval workflow

**Goal:** Mahmoud approves before anything sends.

- Build the message queue: all generated messages sit in "awaiting_approval"
- Build the backend approve/edit/hold logic
- Nothing can move to "sending" without approval
- Build the approval reminder trigger (feeds Phase 8 notifications)

**Checkpoint:** Hussein can approve/edit/hold a message via a test call; unapproved messages are correctly blocked from sending.

---

## PHASE 7 — Sending & routing

**Goal:** approved messages go out on the right channels.

- **LinkedIn:** auto-send via the account's Playwright session; log timestamp/account/platform; move lead to "Contacted"
- **WhatsApp:** auto-send when a public number was found (integrate WhatsApp Business API — Twilio/360dialog, or the chosen method); log and move to "Contacted"
- **Instagram:** do NOT auto-send — mark the message as "manual_send_pending" so the dashboard's Instagram section shows it; when a human marks it Sent, move to "Contacted"
- Respect per-account daily limits and the warm-up ramp

**Checkpoint:** A test approved LinkedIn message sends; a WhatsApp test sends; an Instagram message correctly appears in the manual-send queue instead of sending.

---

## PHASE 8 — CRM, pipeline, follow-up, notifications

**Goal:** the full lifecycle and all alerts work.

- **Pipeline:** New → Contacted → Replied → Interested → Meeting Booked → Deal Closed → Lost; auto-move on reply detection; manual moves supported; timestamp every move
- **Follow-up + re-engagement:** per-lead on/off + timing; re-engagement for cold-after-reply; max 2 contacts; re-contact gap set per lead; pause on reply
- **WhatsApp notifications (all 5 types to both numbers):** hot lead alert, founder found, account warning, approval reminder (to Mahmoud), daily run summary
- Reply detection: check for replies on LinkedIn/WhatsApp and update pipeline

**Checkpoint:** Hussein sees leads move through stages, receives test WhatsApp alerts, and follow-up scheduling works.

---

## PHASE 9 — Dashboard frontend

**Goal:** the full responsive dashboard, working on laptop and iPhone.

Build these sections (see spec §7 for full detail):
- **Live Feed** — today's lead cards (all details incl. weak points, founder flag, copyable message)
- **Instagram Manual Send section** — ready messages + "Mark as Sent"
- **Mahmoud's Approval section** — edit/approve per message + Approve All + count badge (mobile-friendly)
- **CRM Pipeline board** — kanban, drag-and-drop
- **Lead Detail page** — full record + editable follow-up
- **Client History** — permanent searchable database
- **Analytics/KPIs** — all metrics from spec §7.7 with date/platform/account filters
- **Account Health monitor** — status + warning type/reason + redistribution toggle
- **Run Status panel** — running/idle, current task, start/finish time, duration
- **Settings panel** — every editable control from spec §8
- Make it a **PWA** (installable, works full-screen on iPhone)

**Checkpoint:** Hussein opens the dashboard on his laptop AND adds it to his iPhone home screen; every section shows real data.

---

## PHASE 10 — Deploy & warm-up

**Goal:** live and safe.

- Deploy dashboard to Vercel
- Deploy agent to the always-on server (Oracle free tier or VPS)
- Set up the daily scheduler on the server
- **Test end-to-end at LOW volume with NO proxy first**
- Add proxy credentials per account in settings (3 dedicated sticky residential IPs)
- Begin the **warm-up ramp**: start 5–10/day per account, increase over 2–4 weeks to the 20 IG / 30 LinkedIn target
- Vary timing slightly between accounts

**Checkpoint:** The system runs a full real daily cycle end-to-end, safely, at warm-up volume.

---

## After Phase 10
Everything in the spec's "Out of Scope" section (meetings automation, proposals, call summaries, support mode, campaigns) is a potential **Phase 2 project** — do not build these now.
