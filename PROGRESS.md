# PROGRESS — Nexaris AI Outreach Agent

Updated after every phase. If a session runs out of context, point the next one at
this file plus `00_MASTER_PROMPT.md` and it can pick up without losing anything.

**Current state (updated 2026-08-03):** This file had gone stale since Phase 3 — every
phase from 4 through 9 was actually built in the meantime but never logged here. Brought
current below. Phases 0–9 are built; Phase 9's dashboard is confirmed done (Hussein's own
call). LinkedIn sending, the last unbuilt channel, was built and verified against real
pages on 2026-08-03 (see Phase 7 below). Same day: Account Health's remaining settings
(run time, daily limits, proxy credentials) became dashboard-editable, approval
hold-reason + a configurable reminder threshold were added and the migration
(`005_add_approval_settings.sql`) has now been run against Supabase, the full daily
pipeline (discovery per account + one shared analysis→messages→sending→reminders→
replies job) was wired into cron, and LinkedIn reply-detection was built (see Phase 8
below).

**Same day, second round: a real supervised discovery/analysis/message-generation run was
executed live** (Hussein watching, agent unattended for browser navigation only — no
message was ever sent) to test everything above end-to-end for real, rather than trusting
it on paper. That live run caught **7 real, independent bugs** — LinkedIn's own DOM had
drifted since its 2026-07-31 verification, and Instagram's hashtag/profile pages had
changed shape entirely — all now fixed and re-verified live. Full list: two Playwright
navigation-hang bugs (`inner_text()`'s default visibility wait wandering into ad/footer
links; `wait_until="load"` routinely exceeding 15s on LinkedIn's SPA), a broken
`extract_company_profile` (LinkedIn moved Website/Industry/size off the old
`data-test-id="about-us__*"` page onto `/about`'s plain `<dl>`), a broken post-timestamp
regex (an added `aria-hidden` attribute silently broke the match), a broken
`extract_search_results` name dedup (the wrong, whole-card `<a>` link was winning over the
real name-only link), Instagram's hashtag page being hard-redirected to a slow-rendering
search page with no wait at all in the code, and Instagram's bio extraction reading the
**logged-in viewer's own bio**, not the profile being visited (`"biography"` moved to a
`PolarisViewer` JSON entity). See the Decisions log and each file's own docstring for the
full story per bug — all were caught by actually running the pipeline against real
LinkedIn/Instagram pages, not by inspection. Instagram's bio-link/website field is now
honestly `None` (no longer extractable without a real click; not worth guessing at).

Real gaps that remain, none of which need a fresh start: **no channel has completed an
actual supervised live send yet** (LinkedIn's company-path selectors are verified but the
Send click itself hasn't been exercised; WhatsApp is blocked on Twilio credentials, which
aren't in `agent/.env` yet — confirmed via `config.missing_required`), and
**LinkedIn reply-detection's own selectors are still inferred, not inspected against a
real inbox** (no contacted+replied lead exists yet to check against — same live-account
gap linkedin_send.py's person path already has). A real discovered/analyzed pipeline now
exists in the live database (3 LinkedIn + 5 Instagram bakery leads, all real businesses,
correctly qualified) for whenever the first supervised send happens. Full detail in
`REQUIREMENTS_COVERAGE.md`, which was updated the same day.
**Next:** Phase 10 — get real Twilio credentials, do a supervised first live send on each
channel (LinkedIn and WhatsApp) using the real leads already sitting in the pipeline,
watch LinkedIn reply-detection against a real inbox and fix any selector mismatch, then
deploy and warm up. **Do not start `agent/server.py` for real until that first round of
supervised verification is done** — see `DEPLOY.md`.

---

## Phase status

| Phase | Name | Status |
|-------|------|--------|
| 0 | Project setup & foundations | ✅ Complete |
| 1 | Database schema | ✅ Complete — verified live in Supabase |
| 2 | Agent core: account pool & sessions | ✅ Complete — verified live |
| 3 | Discovery: LinkedIn & Instagram | ✅ Full end-to-end cycle run live 2026-08-03 (real bakery leads found, qualified, and saved on both platforms) — 7 real bugs caught and fixed that day, see current-state note above |
| 4 | Claude analysis pipeline | ✅ Live-tested 2026-08-03 against real discovered leads (5 real businesses analyzed, genuinely differentiated scoring/reasoning, no errors) |
| 5 | Message generation | ✅ Live-tested 2026-08-03 (3 real LinkedIn messages generated, specific to each lead's actual analysis, all within LinkedIn's 25-750 char limit) |
| 6 | Approval workflow | ✅ Live-tested 2026-08-03 (hold_reason, dynamic approval_reminder_hours, and the Instagram manual-send queue's full mark_sent flow all confirmed against real/synthetic test data — see current-state note); real messages sit awaiting Hussein's approval in the dashboard now |
| 7 | Sending & routing | 🟡 All 3 channels built and confirmed to fail gracefully when unconfigured (WhatsApp/Instagram code paths tested); the actual click-Send has still never been exercised on any channel — deliberately not run without Hussein watching |
| 8 | CRM, pipeline, follow-up, notifications | 🟡 Built (code) and cron-wired (2026-08-03); LinkedIn reply-detection's DOM selectors are inferred, not live-verified — watch closely against a real inbox before trusting it |
| 9 | Dashboard frontend | ✅ Complete — all 10 sections + Workflow page, real-time, mobile, theming |
| 10 | Deploy & warm-up | 🟡 Docker/DEPLOY.md/vercel.json prepared; full daily pipeline now cron-wired (2026-08-03); nothing actually deployed, and no channel has a supervised live send confirmed yet — do not start the server for real until that happens |

---

## Phase 0 — Project setup & foundations

**Built:**

- Git repository initialised on `main`, `.gitignore` covering `.env`, `venv/`,
  `node_modules/`, and Playwright browser profiles
- Folder structure exactly per `03_PROJECT_STRUCTURE.md`
- **Python agent** (`agent/`)
  - `venv/` with all dependencies installed
  - `requirements.txt` — supabase, anthropic, playwright, APScheduler, dotenv,
    httpx, beautifulsoup4, pytz
  - `config.py` — single place all secrets and settings are read from
  - `db/client.py` — lazy Supabase client using the service role key
  - `main.py` — runs a self-check reporting what is and isn't configured
  - 23 stub modules, each documenting what it will hold and which phase builds it
- **Dashboard** (`dashboard/`)
  - Vite + React 18 + Tailwind v4
  - `src/lib/supabase.js` — client using the anon key, degrades gracefully when unset
  - `src/App.jsx` — Phase 0 status page listing the 10 sections coming in Phase 9
  - PWA manifest + generated icons (180/192/512 + SVG favicon), iOS meta tags,
    safe-area padding for iPhone full-screen
- `.env.example` documenting every variable, annotated with the phase that needs it
- `database/schema.sql` — copy of the Supabase schema for reference
- `REQUIREMENTS_COVERAGE.md` — every spec requirement mapped to a phase, so nothing
  can be silently dropped
- **Documentation** — `docs/PHASE_0_EXPLAINED.pdf` (17 pages) explaining every file,
  decision, and problem hit, plus step-by-step next actions. Generated by
  `tools/make_pdf.py`, which renders HTML to PDF using the Chromium that Playwright
  already installs. Every phase from here produces one of these.

**How to test:**

```bash
# Agent
agent/venv/Scripts/python.exe -m agent.main

# Dashboard
cd dashboard && npm run dev
```

**Decisions made:**

1. **Built in place** rather than inside a `nexaris-outreach-agent/` wrapper folder,
   so the planning documents sit alongside the code. Structure below that level is
   exactly as specified.
2. **Tailwind v4** via the Vite plugin instead of v3. No `tailwind.config.js` or
   `postcss.config.js` is needed — theme customisation lives in `src/index.css`
   under `@theme`. This differs from `03_PROJECT_STRUCTURE.md`, which was written
   assuming v3.
3. **Dependency pins loosened to `>=`.** Exact pins made the set unresolvable —
   supabase and anthropic each constrain `httpx` to different ranges.
4. **`runs` table** treated as a 9th table to verify in Phase 1. It is created by
   the schema but missing from the build plan's verification list, and it is what
   feeds the Run Status panel and the finish time in the daily summary.
5. **Icons are placeholders** — a generated "N" mark. Replace with real Nexaris
   branding in Phase 9.

**Not done / still open:**

- Supabase project not created yet — blocks Phase 1
- Claude API key not set — blocks Phase 4
- Playwright browsers not downloaded yet (`playwright install chromium`) — Phase 2
- Open decisions Q1–Q7 in `REQUIREMENTS_COVERAGE.md`

---

## Phase 1 — Database schema

**Built (code, no live DB yet):**

- `database/policies.sql` — Row Level Security for all 9 tables. Model: RLS on,
  `authenticated` role gets full access, `anon` gets nothing. Fits the 2-user setup.
  The agent's service-role key bypasses RLS, so only the dashboard is governed.
- `agent/db/repositories.py` — the data-access layer. 25 functions across all 9
  tables, one section per table. The only module that touches the database directly.
  Verified: imports clean.
- `agent/db/smoke_test.py` — Phase 1 checkpoint proof. Checks connection, all 9
  tables, seed data (3 accounts + settings), and a self-cleaning write/read/delete.
  Verified: fails gracefully with a clear message when keys are absent.

**Live verification (2026-07-24):** Supabase project created (ref `xtesxgezongzpdgppobm`,
Singapore). Both SQL files run successfully against the real database via a session-pooler
connection (the direct/IPv6 connection failed to resolve on this network — documented in
the Phase 1 PDF). `python -m agent.db.smoke_test` — **ALL CHECKS PASSED**: connection,
all 9 tables, seed data (3 accounts + settings), and a clean insert/read/delete round-trip.

**Phase 1 PDF:** `docs/PHASE_1_EXPLAINED.pdf` — covers all 3 files, the migration process,
the IPv6/pooler issue, and a security note on the temporary use of the DB password.

---

## Phase 2 — Agent core: account pool & browser sessions

**Built and verified live (2026-07-27):**

- `agent/db/repositories.py` — added `has_run_today()` (extends the Phase 1 data layer)
- `agent/core/account_pool.py` — loads the 3 accounts, decides which are due to run
  (active + past run_time + not already run today), with a `force` override for testing
- `agent/core/session.py` — one shared Chromium process, isolated Playwright context per
  account, per-account proxy binding (empty slot handled), persistent per-account cookie
  storage in gitignored `agent/browser_profiles/`
- `agent/core/health.py` — navigation-level failure detection (tested live) + content-level
  CAPTCHA/challenge phrase detection (built with real platform wording, ready for Phase 3)
- `agent/scheduler.py` — `run_cycle()` ties it together; `build_daily_schedule()` wires
  APScheduler cron triggers per account for Phase 10; manual trigger via
  `python -m agent.scheduler`

**Live tests, all passed:**
1. Isolation — 3 simultaneous contexts, each set a unique cookie, zero cross-account leakage
2. Success path — real `runs` rows written with correct start/finish times
3. Failure path — invalid domain correctly detected, account paused with specific
   type + reason, `redistribute_flag` confirmed untouched (core rule R9)

**Bug found and fixed:** `finish_run()` wasn't receiving a `finished_at_iso`, so every run's
finish time silently stayed `None`. Fixed in `scheduler.py`; re-verified with real data.

**Cleanup:** all 3 accounts reset to active, warnings cleared, 6 test `runs` rows deleted,
local browser profile files removed — database and machine left clean for Phase 3.

**Phase 2 PDFs:** `docs/PHASE_2_EXPLAINED.pdf` (full technical detail) and
`docs/PHASE_2_SUMMARY.pdf` (plain-English).

---

## Phase 3 — Discovery: LinkedIn & Instagram (selectors verified live)

**Built and genuinely tested (no login required):**

- `agent/discovery/qualify.py` — multi-signal qualification logic (name pattern, bio,
  website, activity, follower/headcount count). Tested with 4 realistic sample profiles,
  all correct, including an edge case (new business, low followers, still qualifies on
  other signals).
- `agent/db/repositories.py` — added `lead_profile_url_exists()` for discovery-time dedup.
- `agent/scheduler.py` — `run_discovery_cycle()` orchestrates discovery → qualify → save
  for both platforms, per due account.

**Live-verified 2026-07-31/08-02**, using a real captured login session:

- `agent/discovery/linkedin.py` — search (no public equivalent; confirmed both `/about`
  and search results hard-redirect anonymous visits to login) works once authenticated,
  but the results page uses auto-generated, non-semantic CSS classes — the `/company/<slug>/`
  href pattern is the only stable selector. Profile reading, by contrast, does NOT need
  login: a company's bare page server-renders its About section with stable
  `data-test-id="about-us__*"` attributes even logged out. Found and fixed a real bug this
  way: the website link is wrapped in a `linkedin.com/redir/redirect?url=...` redirect,
  not the site itself. Location/industry search facets (`companyHqGeo`, `industryCompanyVertical`)
  were also verified against a real authenticated search and replace the earlier
  free-text-only fallback.
- `agent/discovery/instagram.py` — confirmed Instagram serves its real rendered page (not
  an SSR-disabled shell) to a browser even logged out, for public hashtag/profile/post
  pages. Grid items also have no stable class to match on — same as LinkedIn — so
  extraction reads the `og:description`/`og:url` meta tags and the page's embedded
  hydration JSON (bio, bio-link) instead of DOM locators, since those survive even a
  login-wall overlay.

**Not yet done:** a recorded full end-to-end discovery run — `run_discovery_cycle()`'s
orchestration (looping accounts, widening weak searches, per-lead error isolation) is real
and ready, but hasn't actually been run start-to-finish with real leads landing in the
`leads` table yet.

**Phase 3 PDFs:** still not generated — the EXPLAINED/SUMMARY PDF pattern from Phases 0–2
was not continued for Phase 3 onward. Not blocking further work, just a documentation gap.

---

## Phase 4 — Claude analysis pipeline (built, not live-tested)

- `agent/analysis/client.py` — shared lazy Anthropic client; `call_json()`/`call_text()`.
- `agent/analysis/prompts.py` — system prompts wrapped for Anthropic prompt caching
  (`cache_control: ephemeral`), since the instruction block repeats per lead.
- `agent/analysis/analyze.py` — `analyze_lead()`: company size, revenue tier, industry,
  website notes, ads_running, social platforms, full weak-points list, AI opportunities.
  Model: Haiku (`config.MODEL_ANALYSIS`).
- `agent/analysis/founder.py` — Claude-based (not regex) founder/owner bio scan; returns
  the founder name plus the exact quoted source phrase.
- `agent/analysis/whatsapp_detect.py` — the one non-Claude module here: deterministic
  regex match for `wa.me/`, `api.whatsapp.com/send?phone=`, and labelled numbers.
- `agent/analysis/score.py` — `score_lead()`: 1–10 from fit + pain + budget potential,
  Hot/Warm/Cold bands, always with concrete reasoning.
- Orchestrated by `scheduler.run_analysis_cycle()`: analyze → founder → whatsapp → score
  for every `status="discovered"` lead, saves a `client_history` snapshot **before** any
  message is generated (spec Task 10), and fires hot-lead/founder-found WhatsApp alerts
  immediately per-lead.

`ANTHROPIC_API_KEY` is present in `agent/.env` (checked via `config.missing_required`), so
this can run — it just hasn't been run yet against a real discovered lead.

---

## Phase 5 — Message generation (built, not live-tested)

- `agent/messaging/generate.py` — Sonnet (`config.MODEL_MESSAGES`), per-channel tone
  (LinkedIn formal, WhatsApp casual, Instagram warm DM), a fixed rule set banning vague
  weak-point references, emojis, "I hope this message finds you well", and AI-tell words
  ("streamline", "leverage", "revolutionize", "unlock"). Greeting uses the founder name if
  found, else the business name.
- `agent/messaging/style.py` — `DIRECT` vs `DISCOVERY`, auto-rotates once
  `style_duration_days` has elapsed since `style_last_rotated_at`.
- `scheduler.run_message_generation_cycle()` generates one message on the lead's discovery
  platform, plus an additional WhatsApp message if Phase 4 found a number (never a
  replacement), then flips the lead to `awaiting_approval`.

---

## Phase 6 — Approval workflow (built, not live-tested end to end)

- `agent/messaging/approval.py` — hard gate: `is_send_blocked()` is `True` unless
  `approval_status == "approved"` exactly. Editing (`edit_message()` → `"edited"`) is
  explicitly **not** approval. `active_body()` (edited text if present, else original) is
  what sending code actually sends. A lead only auto-advances once every message on it is
  approved. `messages_needing_reminder()` flags anything sitting "awaiting" past a fixed
  24h (not yet dashboard-configurable).
- `dashboard/src/pages/ApprovalQueue.jsx` — the matching UI (Phase 9).
- No "hold reason" field exists yet on `messages` — flagged in `approval.py`'s own
  docstring as an open decision.

---

## Phase 7 — Sending & routing (all 3 channels now built)

- `agent/sending/instagram_queue.py` — never sends; queues `manual_send_pending`,
  `mark_sent()` records the send and moves the lead into the pipeline exactly like an
  automated send would.
- `agent/sending/whatsapp_send.py` — raw Twilio REST calls via `httpx` (no SDK). Records
  send, contact count, pipeline move, client_history. Doesn't set `sent_via_account`
  (WhatsApp doesn't go through one of the 3 browser accounts).
- **`agent/sending/linkedin_send.py` — built 2026-08-03**, after a real live-verification
  pass (read-only DOM inspection only, using the already-captured session for "Hussein's
  account"; the actual Send click was never exercised during this pass). The key finding
  that shaped this module: `leads.profile_url` is a **company page**, not a person, so
  LinkedIn's normal person-to-person messaging flow doesn't apply here. What does apply:
  some company Pages opt into a "Message" button that opens LinkedIn's Page-inbox modal —
  confirmed live this is genuinely inconsistent (present on a real small "bakery"/Lebanon
  search result, "Paul Bakery Beirut", absent on Nike's page). The modal requires picking
  a required "Conversation topic" (mapped to "Other", since none of the real options —
  Service request / Request a demo / Support / Careers — honestly describe cold outreach)
  and a message between 25–750 characters (both confirmed live and checked in code before
  attempting to send). When a lead's page has no Message button, `linkedin_send.py` raises
  `NoMessageButtonAvailable` — handled by `scheduler.run_sending_cycle()` as a normal
  "ok": False result, exactly like a missing WhatsApp number is for that channel. Not every
  LinkedIn lead will be auto-sendable this way, and that's an expected outcome, not a gap.
- **`WHATSAPP_API_KEY`/`WHATSAPP_API_SECRET`/`WHATSAPP_FROM_NUMBER` are not set** in
  `agent/.env` yet (confirmed via `config.missing_required`) — needs a real Twilio (or
  WhatsApp Business API) account before any WhatsApp send/notification actually goes out.
- **Nothing has completed an actual supervised live send on any channel yet.** The first
  real send on each channel should be watched, not run unattended — same caution already
  applied to every other live-account action in this project.
- `linkedin_send.py` also handles a **person** profile URL (`linkedin.com/in/<username>/`),
  for whenever a future lead's profile_url is a specific person instead of a company (e.g.
  a resolved founder profile — discovery doesn't produce these today, only company URLs).
  Only the company path could be verified live end-to-end; "Hussein's account" has 0
  connections, so there was no real connection to click "Message" on and confirm the
  person path against. It reuses the same shared messaging-widget selectors verified on
  LinkedIn's own full-page compose (`msg-form__contenteditable`/`msg-form__send-button`),
  but the profile page's own "Message" button selector is an inference from the company
  button's aria-label pattern, not a live-confirmed one — flagged honestly in the module
  docstring. Watch the first real person-shaped send closely.

---

## Phase 8 — CRM, pipeline, follow-up, notifications (built, not live-tested)

- `agent/crm/pipeline.py` — single funnel (Contacted → Replied → Interested → Meeting
  Booked → Deal Closed / Lost); `move_stage()` is the only place lead.status is touched
  once a lead is in the pipeline; every move is timestamped.
- `agent/crm/followup.py` — enforces the 2-contact max at scheduling time
  (`MaxContactsReached`), cancels pending follow-ups the instant a reply is detected.
  Actually *dispatching* a due follow-up is not built yet — needs LinkedIn sending first.
- `agent/crm/reply_detection.py` + `agent/sending/whatsapp_reply_check.py` +
  `agent/sending/linkedin_reply_check.py` — both channels' reply detection are built and
  **cron-wired** (2026-08-03, part of `run_full_pipeline_cycle`). WhatsApp pulls Twilio's
  Messages API per contacted lead (no webhook yet). LinkedIn opens the owning account's
  real messaging inbox and reads each contacted lead's thread — **but its DOM selectors
  (conversation list, thread messages, sender/timestamp) are inferred from LinkedIn's
  general naming conventions, not inspected against a real inbox** (no live conversations
  existed to check against during this build — same gap as `linkedin_send.py`'s person
  path). Watch it closely the first time it runs against a real inbox with an actual
  reply, and fix any selector mismatch, before trusting it unattended.
- `agent/notifications/whatsapp_notify.py` — all 5 alert types built (hot lead, founder
  found, account warning, approval reminder, daily summary), all routed to both configured
  WhatsApp recipients (no per-type routing yet — open decision Q4). Untested live pending
  Twilio credentials.

---

## Phase 9 — Dashboard (DONE)

All 10 spec sections plus a bonus interactive Workflow visualization page:
Live Feed, Approval Queue, Instagram Manual Send, Pipeline Board, Lead Detail, Client
History, Analytics, Account Health, Run Status, Settings. Real-time updates via Supabase
Realtime subscriptions (leads, pipeline_history, notifications_log, messages, runs),
mobile collapsible sidebar, full light/dark theming, command palette, toast notifications
for hot leads.

**Confirmed live 2026-08-02:** real Supabase Auth accounts exist for both Hussein
(`husseinalasaad5@gmail.com`) and Mohamad (`mhmdzantout0@gmail.com`), created 2026-08-01 —
checked directly via `supabase.auth.admin.list_users()`. `dashboard/src/lib/auth.js` had a
stale comment claiming these accounts didn't exist yet; corrected.

**Known gap:** `run_time`, `ig_daily_limit`/`linkedin_daily_limit`, and proxy credentials
are displayed on Account Health but have no edit form there yet — currently only editable
directly in Supabase, not from the dashboard as the spec requires (R10).

---

## Deployment groundwork (prepared, not executed)

- `agent/Dockerfile` — `python:3.11-slim`, installs deps + `playwright install --with-deps
  chromium`, entrypoint `python -m agent.server`. Must be built from the repo root so
  `agent` is importable.
- `agent/DEPLOY.md` — recommends a small always-on VPS (Hetzner CX22 or Oracle free tier),
  `docker run --restart unless-stopped`, a persistent volume for `browser_profiles/`
  (critical — losing it wipes all 3 accounts' logins), and explicitly documents that
  first-time login must happen non-headless locally, with the resulting profile copied to
  the server. Also documents which cycles are cron-scheduled (`run_cycle` only) vs. still
  manual-trigger (discovery/analysis/messaging/sending/reply-check).
- `dashboard/vercel.json` — SPA rewrite config for the Vite/React-Router build.
- Nothing has actually been deployed yet — no hosting account created.

---

## Account Health dashboard editing + approval hold-reason/reminder setting (2026-08-03)

- `dashboard/src/pages/AccountHealth.jsx` — run time, IG/LinkedIn daily limits, and proxy
  host/port/username/password (masked) are now real editable fields with a Save button per
  account, closing the S4–S6/S12 gap flagged the same day. Warm-up cap stays read-only
  (it's computed automatically, never meant to be hand-set).
- `database/005_add_approval_settings.sql` — adds `messages.hold_reason` (freeform, why a
  message was held) and `settings.approval_reminder_hours` (replaces the hardcoded 24h
  constant). **This migration has not been run against the live Supabase project yet** —
  same as every other migration, it needs Hussein to run it in the SQL editor before the
  new fields actually exist there.
- `agent/messaging/approval.py` — `hold_message()` now takes an optional `reason`;
  `messages_needing_reminder()` reads `settings.approval_reminder_hours` instead of a fixed
  constant (falls back to 24h only if settings can't be read at all).
- `dashboard/src/pages/ApprovalQueue.jsx` — a "reason for holding" field feeds `hold_reason`.
- `dashboard/src/pages/Settings.jsx` — new "Remind me about pending approvals after (hours)"
  control.

## Cron wiring: full daily pipeline (2026-08-03)

`scheduler.build_daily_schedule` now schedules `run_discovery_cycle` per account at its own
`run_time` (replacing `run_cycle`'s Phase 2 placeholder role in production scheduling) plus
one shared daily `run_full_pipeline_cycle` job (analysis → messages → sending → approval
reminders → WhatsApp replies), fixed at 20:00 project-timezone as a first-pass choice.
**This reverses an earlier deliberate deferral** — these steps were left manual-trigger-only
specifically until each was confirmed working live, and that confirmation still hasn't
happened (no channel has completed a supervised live send; Twilio credentials aren't set).
Done at explicit request, not because the earlier caution turned out to be wrong. `agent/
DEPLOY.md` and `agent/server.py` updated to match, both now explicit that `agent/server.py`
should not actually be started against a real deployment until that live verification happens.

## Live supervised discovery/analysis/messaging test + 7 bugs found and fixed (2026-08-03)

Hussein asked for everything testable to actually be run live rather than trusted on
paper, while he was away. Ran real discovery, analysis, and message generation against
real LinkedIn/Instagram pages (target: "bakery" businesses in Lebanon) — no message was
ever sent, and no supervised-send-requiring action (run_sending_cycle, WhatsApp send,
Instagram mark_sent on a real lead) was run without Hussein present, per the same caution
already established for Phase 7. The 3 non-test accounts were temporarily paused (restored
after) to scope discovery to one account with a real logged-in session.

**Bugs found and fixed, in the order they surfaced:**

1. **Faceted LinkedIn search URL timing out** — `discovery/linkedin.py`'s facet-based
   search URL (once `target_location`/`target_industry` were set to real values so
   discovery would return actual bakeries instead of companies merely *named* "Bakery")
   took longer than the hardcoded 15s to load. Fixed: `wait_until="domcontentloaded"` +
   30s across every LinkedIn navigation in `scheduler.py`, `linkedin_send.py`, and
   `linkedin_reply_check.py` (`load` waits for every resource, not just a queryable DOM).
2. **Real multi-minute hang, not a timeout** — even after fix #1, discovery hung for
   several minutes on the same search-results page. Root cause: `_safe_text()`'s
   `inner_text()` call waits for element *visibility* by default, and a real LinkedIn
   search page has plenty of `a[href*='linkedin.com/company/']` matches beyond the visible
   company cards (ad panels, footer links) — each invisible match's `inner_text()` call
   was silently eating up to its own default timeout. Fixed: switched to
   `text_content(timeout=2_000)` (reads the DOM directly, no visibility wait) in both
   `discovery/linkedin.py` and `discovery/instagram.py`'s `_safe_text`/`_safe_attr`.
3. **`extract_company_profile` returning empty bio/website/size for every lead** — the
   2026-07-31 `data-test-id="about-us__*"` selectors matched nothing at all
   (`eval_on_selector_all` returned an empty list). LinkedIn had redesigned the company
   page: real content now lives in `p.org-top-card-summary__tagline` (bio, present on the
   bare page) and a plain `<dl>` with `<h3>` label text for Website/Industry/size (only on
   `/about`, which — contrary to the old docstring's claim — does NOT redirect
   authenticated visits to login, only anonymous ones). Rewrote the function to read
   `/about` via label-matched XPath (`_dd_after_label`); `scheduler.py`'s caller now
   navigates to `/about` instead of the bare page.
4. **Every real lead scoring "zero posts"** — `_POST_TIME_RE`'s regex assumed a bare
   `<span><!---->` with no attributes; LinkedIn added `aria-hidden="true"` to that span,
   silently breaking the match for every post on every company (confirmed live against
   Wooden Bakery, which genuinely has recent posts). One-character-class regex fix.
5. **Every saved lead's `business_name` holding the entire card's text** — e.g. "Wooden
   Bakery Wooden Bakery Food and Beverage ServicesBeirutFollow[full bio text]...". The
   outer, whole-card-wrapping `<a>` and the real name-only inner `<a>` both match
   `extract_search_results`'s href selector; the dedup logic's "first truthy text wins"
   rule assumed the wrong occurrence would have *empty* text, not *longer* text. Fixed:
   keep the *shortest* non-empty text per URL instead (the wrapper's text is always a
   superset of the real name).
6. **Instagram discovery finding 0 results on every single run** — `build_hashtag_url`'s
   `/explore/tags/<tag>/` URL now hard-redirects (confirmed universal, not #bakery-
   specific) to `/explore/search/keyword/?q=%23<tag>`, a generic search page whose results
   render client-side well after `domcontentloaded` — and `_discover_instagram` had no
   wait at all after `page.goto`. Fixed with an explicit `page.wait_for_timeout(...)`;
   genuinely inconsistent in practice (0 results at 5s and again at 7s on separate runs, 21
   found at 7s/9s on others) — settled on 10s for margin, flagged as worth revisiting if
   0-result runs keep happening. The same missing-wait class of bug also hit
   `resolve_post_to_profile_url`'s and the profile page's own navigations (`wait_until=
   "load"`, same root cause as bug #1) — fixed identically.
7. **Every Instagram bio reading as "🌟" (a single star emoji) across 5 unrelated real
   accounts with 43K-584K followers** — the old `"biography":"..."` embedded-JSON regex
   WAS matching something real, just not the profile being visited: Instagram's hydration
   payload now nests that key under a `["PolarisViewer", ...]` entity, the currently
   logged-in VIEWER's own data, not the target account's. `"bio_links"` (the old website
   source) is gone from the page entirely. Fixed: read the real bio from a plain
   `<meta name="description">` tag instead (distinct from `og:description`, which now only
   has the stats line) — Instagram's own `<stats> - @user on Instagram: "<bio>"` string.
   Website/bio-link extraction was NOT patched with a guess: the visible link now sits
   behind a `<button>` with no `href` to read statically, so that field is honestly left
   `None` rather than faked.

**Also found while fixing bug #7:** Instagram leads had no `business_name` at all
(`extract_profile` never had a display-name field to begin with — Instagram's real display
name has no stable selector or semantic source). Fixed by using the `@username` (reliable,
already in `profile_url`) as `business_name` instead of leaving it null.

**End state confirmed live:** discovery, analysis, and message generation now run cleanly
end-to-end on both platforms — 3 real LinkedIn bakery leads (The Lebanese Bakery, George
Gemayel Bakery Consultancy, Paul Bakery Beirut) and 5 real Instagram bakery leads
(prestige.bakery.lb, toicroissant, clarkstreetbread, wheaty.in, fredbakery) are sitting in
the live database, correctly qualified, several already analyzed and message-generated.
`hold_reason`, `approval_reminder_hours`, and the Instagram manual-send queue's full
`mark_sent` flow were also exercised against synthetic throwaway test records (created and
deleted within the same test, never touching real leads) and confirmed working.
`run_sending_cycle()` and any other action that would have sent a real message were
deliberately not run — that's Phase 7's supervised-send step, still pending Hussein.

## Rename: Mahmoud → Mohamad (2026-08-02)

Renamed throughout code, docs, and database (16 files, commit `79a11b9`). Confirmed clean
across the working tree. One loose end that's now fixed: the Phase 0/1 PDF binaries
(`PHASE_0_EXPLAINED.pdf`, `PHASE_1_EXPLAINED.pdf`, and the two cross-phase appendix PDFs)
had been generated *before* the rename from HTML that still said "Mahmoud" — the rename
commit updated the HTML sources but never regenerated the PDFs from them. Regenerated all
of them from the current (already-renamed) HTML sources on 2026-08-02.

---

## Decisions log

| Date | Decision |
|------|----------|
| 2026-07-22 | Repo is public on GitHub as `AiOutreachAgent`. Hussein pushes himself — the agent never runs `git push`. |
| 2026-07-22 | Build order follows `01_PHASED_BUILD_PLAN.md` (11 phases) over spec §14 (10 steps) where they conflict. No requirement is dropped either way. |
| 2026-08-02 | `PROGRESS.md` and `REQUIREMENTS_COVERAGE.md` had gone stale since Phase 3 (every later phase still read TODO despite being built). Brought both current against the actual code plus live checks (Supabase Auth users queried directly, credential presence checked via `config.missing_required`), and fixed the resulting stale docstrings/comments in `scheduler.py` and `dashboard/src/lib/auth.js`. Also regenerated the Phase 0/1 PDFs, which had baked in "Mahmoud" from before the rename. |
| 2026-08-03 | Built `agent/sending/linkedin_send.py`, verified live (read-only inspection, no message actually sent) against real company pages. Discovered `leads.profile_url` being a company page (not a person) meant the standard LinkedIn messaging flow didn't apply — the real mechanism is a Page's optional "Message" button, confirmed present on some small business pages and absent on others. Chose to raise a clear `NoMessageButtonAvailable` for leads without it rather than guess a workaround. Extended the same module same day to also handle a person profile URL, at Hussein's request — that path's own "Message" button trigger couldn't be live-verified (0 connections on the test account), flagged honestly rather than presented as confirmed. |
| 2026-08-03 | At Hussein's explicit request, wired the full daily pipeline into cron (`build_daily_schedule`) and made Account Health's remaining settings (run time, daily limits, proxy) dashboard-editable, plus added `hold_reason`/`approval_reminder_hours`. The cron change reverses an earlier deliberate safety deferral (steps were manual-trigger-only pending live confirmation, which still hasn't happened) — done because asked, not because the risk went away; `DEPLOY.md`/`server.py` both now say not to actually start the server for real until a supervised live send is confirmed on each channel. |
| 2026-08-03 | Built `agent/sending/linkedin_reply_check.py`, the piece `crm/reply_detection.py` had explicitly deferred until LinkedIn sending existed. No real LinkedIn inbox with an actual conversation was available to inspect (same gap `linkedin_send.py`'s person path already had), so its DOM selectors are inferred from LinkedIn's own naming conventions rather than confirmed live — flagged honestly in the module's docstring rather than presented as verified, and wired into `run_full_pipeline_cycle` with the same defensive try/except already used for the WhatsApp reply check so a selector mismatch there can't wipe out the other steps' results. |
| 2026-08-03 | At Hussein's explicit request ("test everything, catch any bug"), ran real discovery/analysis/message-generation live rather than trusting the code on paper — no send-capable action was run unattended. Caught and fixed 7 real, independent bugs (2 Playwright navigation hangs; LinkedIn's about-page/post-timestamp/search-result-name selectors all drifted since 2026-07-31; Instagram's hashtag page now redirects to a slow client-rendered search page with no wait in the original code; Instagram's bio extraction was reading the logged-in viewer's own bio, not the target account's) — full writeup in the dedicated section above. All fixes verified by re-running live against the same real pages, not just by inspection. Chose not to guess at Instagram's now-unreadable website/bio-link field (sits behind a button with no `href`) rather than fake a value. |
| 2026-08-03 | 3 real LinkedIn leads and 5 real Instagram leads discovered during the live test round were deliberately left in the database rather than deleted, at Hussein's choice — they're genuine, correctly-qualified businesses, not corrupted test artifacts, so they're now real pipeline data awaiting his approval. Corrupted intermediate saves from mid-fix test runs (3 LinkedIn leads with concatenated `business_name` text, 5 Instagram leads with `business_name: null`) WERE deleted, each confirmed corrupted before removal. |
