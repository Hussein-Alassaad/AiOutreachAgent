# REQUIREMENTS COVERAGE LEDGER — Nexaris AI Outreach Agent

Every requirement from `Nexaris_Agent_COMPLETE_SPEC_FINAL.pdf`, the master prompt, and the
build plan is listed here with an ID, the phase that delivers it, and the file it lands in.

**Purpose:** Hussein asked that nothing from the spec be missed. A promise isn't checkable —
this table is. At the end of every phase, the rows for that phase get marked `DONE` and the
phase is not closed until all of its rows are marked.

**Status legend:** `TODO` = not started · `WIP` = in progress · `CODE` = code written, not
yet verified live · `DONE` = built and tested by Hussein · `N/A` = deliberately out of scope

**Updated 2026-08-02** — this ledger had gone stale since Phase 2 (every Phase 3–9 row still
read `TODO` despite the code existing). Brought current against the actual codebase and a
few live checks (Supabase Auth users queried directly, credential presence checked via
`config.missing_required`). Where a row is genuinely still open, it says so plainly rather
than being marked done because the phase shipped.

**Phase 1 — DONE (2026-07-24).** Verified live against the real Supabase project (ref
`xtesxgezongzpdgppobm`). Q6 resolved.

**Phase 2 — DONE (2026-07-27).** Account pool, isolated Playwright sessions, health checks,
scheduler all built and verified live. R6, R9 enforced in code.

**Phase 3 — Discovery: full end-to-end cycle run live (2026-08-03), 6 real bugs found
and fixed that day.** `discovery/linkedin.py` and `discovery/instagram.py` were originally
selector-checked 2026-07-31/08-02, then **actually run end-to-end live** on 2026-08-03
(real accounts, real "bakery"/Lebanon search, real leads saved to `leads`) — this caught
real DOM drift the original selector-checking pass missed, since it never exercised the
full orchestration loop: LinkedIn's about-page layout had changed (Website/Industry/size
moved off the old `data-test-id` markup), a post-timestamp regex silently broke from one
added HTML attribute, the search-result name-dedup logic was picking the wrong (whole-card)
link, Instagram's hashtag page now redirects to a slow client-rendered search page the
original code never waited for, and Instagram's bio extraction was reading the logged-in
viewer's own bio, not the target account's. Two Playwright navigation-hang bugs
(`inner_text()`'s visibility wait; `wait_until="load"`'s full-resource wait) were also
found and fixed across every LinkedIn/Instagram navigation in the codebase. Full writeup:
`PROGRESS.md`'s "Live supervised discovery/analysis/messaging test" section. **End state:**
3 real LinkedIn + 5 real Instagram bakery leads sit in the live database, correctly
qualified — this row is now genuinely DONE, not just code-complete.

**Phase 4 — Analysis pipeline: live-tested (2026-08-03).** `analysis/{analyze,founder,
whatsapp_detect,score}.py` all written per spec, then actually run live against 5 real
discovered leads (2 LinkedIn, 2 more LinkedIn, later 2 Instagram) during the same
supervised test round — genuinely differentiated Claude reasoning per lead (scores ranged
cold/3 to warm/6, each with lead-specific justification, not templated), zero errors.

**Phase 5 — Message generation: live-tested (2026-08-03).** `messaging/{generate,
style}.py` written per spec (Sonnet, per-channel tone, Direct/Discovery rotation), then
actually run live: 3 real LinkedIn messages generated, each referencing that specific
lead's actual analysis (follower count, missing bio details, etc.), all within LinkedIn's
25-750 character limit.

**Phase 6 — Approval gate: live-tested (2026-08-03).** `messaging/approval.py`'s hard gate
plus the `pages/ApprovalQueue.jsx` dashboard UI both exist. `hold_message()` with a real
`hold_reason`, and `messages_needing_reminder()`'s dynamic `approval_reminder_hours`
threshold, were both exercised against test data and confirmed working (migration
`005_add_approval_settings.sql` has now been run against Supabase). The
generate → approve step of the full loop has NOT been closed yet — 3 real LinkedIn
messages currently sit "awaiting" in the live dashboard for Hussein's actual approval;
approve → send is Phase 7's still-open supervised-send step.

**Phase 7 — Sending & routing: built for all 3 channels (CODE, none live-tested with a
real send yet).** Instagram manual-queue and WhatsApp auto-send were already built.
**LinkedIn auto-send was built and wired in on 2026-08-03**, after a real, live
verification pass (read-only DOM inspection, no message actually clicked-send) that
surfaced an important fact the earlier stub didn't know: `leads.profile_url` is a
*company page*, and only some company pages opt into LinkedIn's Page-messaging feature —
confirmed live (present on a small real "bakery"/Lebanon search result, absent on Nike's
page). `sending/linkedin_send.py` checks for that page's Message button per lead and
raises a clear `NoMessageButtonAvailable` when it's absent, the same way a missing
WhatsApp number is handled for that channel — not every lead will be LinkedIn-sendable
this way, and that's expected, not a bug. Confirmed live 2026-08-03: `whatsapp_send.py`
and `whatsapp_reply_check.py` both raise `WhatsAppNotConfigured` cleanly (not a crash) when
Twilio credentials are absent, and `run_sending_cycle()`'s own try/except correctly turns
that into a normal per-message `"ok": False` result — same day, the Instagram manual-send
queue's full `queue_for_manual_send` → `mark_sent` flow was exercised end-to-end against a
synthetic throwaway lead (created and deleted within the test) and confirmed correct
(`send_status` transitions, lead moves to "contacted", `contact_count`/
`first_contacted_at` set). `linkedin_send.py` also branches to a **person**
path (`linkedin.com/in/<username>/`) for whenever a future lead resolves to a specific
person rather than a company — discovery doesn't produce these today. That path reuses
verified shared-widget selectors but its own "Message"-button trigger is an inference
(no real connection existed on "Hussein's account" to test against), flagged as such in
the module docstring — watch its first real use. WhatsApp/Twilio credentials (`WHATSAPP_API_KEY`
etc.) are **still not set** in `agent/.env` — confirmed via `config.missing_required` — so
the WhatsApp path hasn't sent a real message yet either. **No channel has completed an
actual supervised live send yet** — that first real click-Send should be watched, same
caution the project has applied to every other live-account action.

**Phase 8 — CRM/pipeline/follow-up/notifications built (CODE, cron-wired, not
live-tested).** `crm/{pipeline,followup,reply_detection}.py`,
`notifications/whatsapp_notify.py`, `sending/whatsapp_reply_check.py`, and
`sending/linkedin_reply_check.py` all written and wired into
`run_full_pipeline_cycle` (2026-08-03). **LinkedIn reply-detection's DOM selectors are
inferred, not inspected against a real inbox** — no live conversation existed to check
against during this build (same gap `linkedin_send.py`'s person path already has); watch
it closely against a real reply before trusting it unattended.

**Phase 9 — Dashboard DONE.** All 10 sections plus a bonus Workflow visualization page,
real-time Supabase subscriptions, mobile layout, light/dark theming. Confirmed live via a
direct Supabase Auth query: real accounts exist for both Hussein
(`husseinalasaad5@gmail.com`) and Mohamad (`mhmdzantout0@gmail.com`), created 2026-08-01 —
the dashboard's own code comment claiming otherwise was stale and has been corrected.
Some per-account settings (see D8/S4–S6/S12 below) are shown on Account Health but not yet
editable there — noted in section D/section D below rather than silently marked done.

**Deployment groundwork — prepared, not executed.** `agent/Dockerfile` + `agent/DEPLOY.md`
(containerized agent, VPS recommendation, persistent browser-profile volume) and
`dashboard/vercel.json` (SPA rewrite config) are written and ready, but no hosting account
has actually been created or deployed to yet.

**Cron wiring — done 2026-08-03, at explicit request, reversing an earlier deferral.**
`scheduler.build_daily_schedule` now schedules the full daily pipeline (discovery per
account, plus one shared daily analysis/messages/sending/reminders/replies run) instead of
just the Phase 2 placeholder `run_cycle`. This was previously left manual-trigger-only on
purpose until each step was confirmed working live — that confirmation still hasn't
happened. **Do not start `agent/server.py` against a real deployment until at least one
supervised live send per channel (LinkedIn, WhatsApp) has actually been watched.**

---

## A. The 10 core rules (never violated — these are constraints, not features)

| ID | Rule | Enforced in | Phase | Status |
|----|------|-------------|-------|--------|
| R1 | Instagram is **find-only** — never auto-sends | `sending/instagram_queue.py` | 7 | CODE — built (queues + "mark as sent"), not live-tested |
| R2 | LinkedIn is fully automated — find **and** send | `discovery/linkedin.py` + `sending/linkedin_send.py` | 3/7 | CODE — find is verified live; send is built and verified against real pages, but only sendable on leads whose company page has LinkedIn's Message feature enabled (see Phase 7 note) |
| R3 | WhatsApp auto-sends **only** when a public number was found | `sending/whatsapp_send.py` | 7 | CODE — built, untested (no Twilio credentials configured yet) |
| R4 | Nothing sends without Mohamad's approval — hard gate, no bypass | `messaging/approval.py` + `pages/ApprovalQueue.jsx` | 6 | CODE — backend + dashboard UI built, not live-tested end to end |
| R5 | Messages are AI-generated per lead, human tone | `messaging/generate.py` | 5 | CODE — built, not live-tested against a real lead |
| R6 | Each account uses its own dedicated sticky proxy IP — never shared | `core/session.py` | 2 | DONE — isolation verified live; proxy slot wired, empty until Phase 10 |
| R7 | Founder detection flags for **manual** outreach — never auto-contacts founder | `analysis/founder.py` | 4 | CODE — built, not live-tested |
| R8 | Max **2** contacts per lead, ever | `crm/followup.py` | 8 | CODE — enforced at scheduling time (`MaxContactsReached`), not live-tested |
| R9 | Warnings show **type + reason**; redistribution is manual only | `core/health.py` | 2 | DONE — verified live, redistribute_flag confirmed untouched |
| R10 | Everything editable from the dashboard, no code changes | `pages/Settings.jsx`, `pages/AccountHealth.jsx`, `pages/LeadDetail.jsx` | 9 | DONE (2026-08-03) — the remaining Account Health fields (S4–S6, S12) became editable; see section D |

---

## B. Agent tasks — spec §6 (all 17, in execution order)

| ID | Task | Detail that must not be lost | File | Phase | Status |
|----|------|------------------------------|------|-------|--------|
| T1 | Boot & load config | Loads niche, location, language, targets, style, style duration, proxy creds **+ the full previously-contacted list (dedup)** + the re-contact-eligible list | `main.py` | 2 | DONE |
| T2 | Health check & session open | Warning **type + specific reason** (not generic); pause account; wait for Hussein on redistribution; isolated context per proxy IP | `core/health.py`, `core/session.py` | 2 | DONE |
| T3 | LinkedIn discovery | Search by niche/industry/company size/location/activity; 30/account; reads company name, description, headcount, website, recent posts, public contact info; **prioritises recently active over dormant**; adapts search terms if results are weak | `discovery/linkedin.py` | 3 | CODE — selectors verified live 2026-07-31/08-02; full end-to-end cycle run not yet recorded |
| T4 | Instagram discovery | Hashtags + explore + location tags; 20/account; reads bio, followers, post count, engagement, story signals; skips inactive/irrelevant; **reads only, sends nothing** | `discovery/instagram.py` | 3 | CODE — selectors verified live 2026-07-31; full end-to-end cycle run not yet recorded |
| T5 | Business qualification | Reasoning decision, **not a keyword filter**; skip personal/inactive/no clear service/wrong niche | `discovery/qualify.py` | 3 | DONE — tested with 4 realistic sample cases |
| T6 | Deep profile analysis | Company size · revenue tier · industry · **website analysis (design quality, booking flow, CTA, load speed)** · ads activity · all social platforms active on · **full weak points list** · AI opportunities. Stored and shown **in full**, never summarised | `analysis/analyze.py` | 4 | CODE — built, not live-tested |
| T7 | Founder detection | Phrases: "founder of X", "co-founder", "CEO & founder", "established by", "created by", "owner of", + any equivalent wording. Stores founder name **and the matched bio phrase**. Dashboard shows **two flags** on one card (company messaged + founder found). Fires WhatsApp alert | `analysis/founder.py` | 4 | CODE — detection logic built, not live-tested; alert path untested (no Twilio credentials yet) |
| T8 | WhatsApp number detection | Checks bio, profile description, website link, contact section — on **both** platforms. Public listed numbers only, **never guessed or scraped**. Fires WhatsApp channel **in addition to**, not instead of, the primary channel | `analysis/whatsapp_detect.py` | 4 | CODE — built, not live-tested |
| T9 | Lead scoring | 1–10 from **fit + pain + budget potential**; Hot 8–10 / Warm 5–7 / Cold 1–4; **written reasoning always present** | `analysis/score.py` | 4 | CODE — built, not live-tested |
| T10 | Save to Client History | Happens **before** any message is generated; permanent; saved whether or not outreach ever happens | `db/repositories.py`, `scheduler.run_analysis_cycle()` | 4 | CODE — ordering confirmed in code, not live-tested |
| T11 | Message generation | Sonnet; human tone; greeting uses business/team/**founder** name; references that lead's specific weak points; Direct vs Discovery style; style holds for the configured duration then rotates | `messaging/generate.py`, `messaging/style.py` | 5 | CODE — built, not live-tested |
| T12 | Queue for approval | All of the day's messages queued; Mohamad edits/approves/holds; nothing sends on **any** channel until approved | `messaging/approval.py`, `pages/ApprovalQueue.jsx` | 6 | CODE — backend + UI built, not live-tested. `hold_reason` field added 2026-08-03 (`database/005_add_approval_settings.sql` — **not yet run against the live Supabase project**, needs Hussein to run it in the SQL editor) |
| T13 | Route & send | LinkedIn auto · WhatsApp auto · Instagram to manual queue. Every send logged with **timestamp, account used, platform, message text** | `sending/*` | 7 | CODE — all 3 channels built; WhatsApp untested (no Twilio creds); LinkedIn verified against real pages but not yet through an actual supervised send; not every LinkedIn lead will have the Message feature enabled (expected, not a gap) |
| T14 | Hot lead instant alert | Fires **immediately on scoring**, not at end of run. Includes name, platform, score, reasoning, **dashboard deep-link** | `notifications/whatsapp_notify.py` | 8 | CODE — fires per-lead in `run_analysis_cycle()`, untested live (no Twilio creds) |
| T15 | CRM record + pipeline entry | New → Contacted → Replied → Interested → Meeting Booked → Deal Closed → Lost. Auto-move on reply; manual moves allowed; **every change timestamped** | `crm/pipeline.py` | 8 | CODE — built, not live-tested |
| T16 | Follow-up & re-engagement | **Per-lead** on/off + timing set by Hussein (agent never picks the delay); re-engagement for replied-then-cold; **pauses instantly on reply**; max 2 contacts; per-lead re-contact gap | `crm/followup.py`, `pages/LeadDetail.jsx` | 8 | CODE — on/off + timing UI confirmed in `LeadDetail.jsx`; scheduling + max-contacts + cancel-on-reply built; **actually dispatching a due follow-up is not built yet** (needs the LinkedIn send channel) |
| T17 | Daily summary | WhatsApp to both numbers: leads found by platform, messages sent by platform, hot/warm/cold breakdown, warnings that occurred, **exact finish time, total duration** | `notifications/whatsapp_notify.py` | 8 | CODE — built, untested live (no Twilio creds) |

---

## C. Dashboard sections — spec §7 (all 10)

All 10 sections plus a bonus Workflow page are built (Phase 9, confirmed 2026-08-02).
Marked `DONE` per Hussein's own confirmation that Phase 9 is finished; a couple of
per-account editable-settings gaps are called out under D8/section D rather than hidden.

| ID | Section | Every element required | Phase | Status |
|----|---------|------------------------|-------|--------|
| D1 | Daily Live Feed | Card per lead: business name, platform badge, profile photo, follower/headcount, industry, score, temperature badge (colour-coded), **ALL weak points — full list, not hidden**, AI opportunities, founder flag (two-part), generated message **copyable in one tap**, pipeline status. Hot leads **highlighted and pinned to top**. Filters: platform, score range, temperature, date. Click → Lead Detail | 9 | DONE |
| D2 | Instagram Manual Send | Separate section; shows **only post-approval** IG leads; name, score, temperature, copyable message, founder flag, **"Mark as Sent"** → enters pipeline as Contacted; shows pending vs sent today; **must work on iPhone** | 9 | DONE |
| D3 | Approval Section | Per item: name, platform, score, temperature, full message text, **Edit + Approve**; individual approve **and Approve All**; **count badge** ("14 awaiting"); mobile-first | 9 | DONE |
| D4 | CRM Pipeline Board | 7 kanban columns; card shows name, platform, score, temperature, last activity; **drag and drop**; auto-move on reply; click → Lead Detail | 9 | DONE |
| D5 | Lead Detail | Everything: profile data, website, all social platforms, full weak points, AI opportunities, founder name + flag, WhatsApp number, score + **full reasoning**, temperature, **all messages ever sent with dates**, **all replies with dates**, stage + **full stage history with timestamps**, follow-up settings, contact count, first contact date, **editable notes**. Edit follow-up here. Move stage here | 9 | DONE |
| D6 | Client History | **Permanent, never resets.** Search/filter by name, industry, platform, score, temperature, date range, contacted y/n, founder y/n, stage. Totals at top: ever analysed, contacted, hot/warm/cold | 9 | DONE |
| D7 | Analytics & KPIs | **Headline cards:** businesses reached (all/month/week/today), messages sent, replies + reply rate %, meetings booked, deals closed, hot leads found. **Charts:** reached per platform (bar), per account (bar), sent vs pending approval, leads per stage (funnel), hot/warm/cold (donut), reply rate per platform (bar), founders detected + founders contacted, IG-specific (found / sent manually / pending). **Daily activity timeline (line).** Filters: date range, platform, account. **Real-time updates** | 9 | DONE |
| D8 | Account Health | 3 accounts: Active/Warned/Paused; **specific warning type + reason**; per-account **redistribute toggle** (manual); usage today vs limit; last active time; **proxy status: connected/missing/error** | 9 | DONE — run_time/daily limits/proxy credentials became editable here 2026-08-03 (see S4–S6/S12) |
| D9 | Run Status | Running/Idle/Error; which account is active and **what task it's on**; start + **finish time**; **total duration** ("Completed in 2h 14m"); progress "X of 150 processed" | 9 | DONE |
| D10 | Settings | Every control in section D below | 9 | DONE for S1–S3, S7, S8, S14; see section D for the rest |
| D11 | PWA | `manifest.json` + icons; installable via Safari → Add to Home Screen; full-screen on iPhone | 0/9 | DONE |

---

## D. Editable settings — spec §8 (nothing here may require a code change)

| ID | Setting | Phase | Status |
|----|---------|-------|--------|
| S1 | Target niche / industry / business type | 1 → 9 | DONE — `pages/Settings.jsx` |
| S2 | Target location / country / market — **geography-configurable, not hardcoded** | 1 → 9 | DONE — `pages/Settings.jsx` |
| S3 | Outreach language(s) | 1 → 9 | DONE — `pages/Settings.jsx` |
| S4 | Instagram leads/day per account (default 20) | 1 → 9 | DONE (2026-08-03) — `pages/AccountHealth.jsx` |
| S5 | LinkedIn leads/day per account (default 30) | 1 → 9 | DONE (2026-08-03) — `pages/AccountHealth.jsx` |
| S6 | Run time — **per account, set independently** (not one shared time) | 1 → 9 | DONE (2026-08-03) — `pages/AccountHealth.jsx` |
| S7 | Active message style (Direct / Discovery / extensible) | 1 → 9 | DONE — `pages/Settings.jsx` |
| S8 | Style duration before rotation | 1 → 9 | DONE — `pages/Settings.jsx` |
| S9 | Per-lead follow-up on/off + timing (from Lead Detail) | 8 → 9 | DONE — `pages/LeadDetail.jsx` |
| S10 | Re-contact gap per lead | 8 → 9 | DONE — expressed as an explicit scheduled datetime rather than a day-count delta, in `pages/LeadDetail.jsx` |
| S11 | Contact cap = 2 — **deliberately NOT user-editable** | 8 | DONE — hardcoded `max_contacts_per_lead` default in `crm/followup.py`, correctly not exposed in the UI |
| S12 | Proxy IP/credentials per account × 3 — **editable slot, may be empty at launch** | 2 → 9 | DONE (2026-08-03) — `pages/AccountHealth.jsx` (password field masked) |
| S13 | Redistribution toggle per account — **never automatic** | 2 → 9 | DONE — `pages/AccountHealth.jsx` |
| S14 | WhatsApp recipient number 1 + number 2 | 1 → 9 | DONE — `pages/Settings.jsx` |
| S15 | Optional per-type alert routing (e.g. approval reminders → Mohamad only) — spec says "finalise during build" | 8 | OPEN — code currently sends all 5 alert types to both configured recipients uniformly |

---

## E. WhatsApp notifications — spec §9 (all 5)

| ID | Alert | Trigger | Recipients | Must include | Phase | Status |
|----|-------|---------|------------|--------------|-------|--------|
| N1 | Hot lead | Score 8–10, **immediately on scoring** | Both | name, platform, score, reasoning, dashboard link | 8 | CODE — built, untested (no Twilio creds configured) |
| N2 | Founder found | Founder detected alongside company message | Both | name, founder name, platform, dashboard link | 8 | CODE — built, untested |
| N3 | Account warning | Immediately on account problem | Both | which account, warning type, **specific reason** | 8 | CODE — built, untested |
| N4 | Approval reminder | Day's messages queued | Mohamad | count waiting | 8 | CODE — threshold now dashboard-configurable (`settings.approval_reminder_hours`, `pages/Settings.jsx`), untested live |
| N5 | Daily summary | Run completion | Both | leads by platform, messages by platform, hot/warm/cold, warnings, **finish time, duration** | 8 | CODE — built, untested |

---

## F. Account safety & proxy plan — spec §11

| ID | Requirement | Phase | Status |
|----|-------------|-------|--------|
| P1 | Sticky proxies only — **rotating proxies must never be used** | 2 | DONE — `core/session.py` binds one static proxy config per account, no rotation logic exists |
| P2 | Residential (or mobile for IG) — **datacenter proxies must never be used** | 2/10 | TODO — no proxies actually assigned yet (all 3 accounts' proxy slots are empty) |
| P3 | Account → IP is a permanent 1:1 assignment; contexts never mix | 2 | DONE — verified live, zero cross-account leakage |
| P4 | Proxy config slot exists from day one even when empty; filling it later is **settings-only, zero code change** | 2 | DONE — schema + `core/session.py` support it; dashboard edit form is the S12 gap above |
| P5 | Warm-up: start 5–10/day per account, ramp over 2–4 weeks to 20 IG + 30 LI | 10 | CODE — `core/warmup.py` built and used by `_discover_linkedin`/`_discover_instagram`, not yet exercised over a real multi-week span |
| P6 | Auto warm-up schedule — increments weekly toward the configured max (`core/warmup.py`) | 2/10 | CODE — same as P5 |
| P7 | **Vary timing/pattern between the 3 accounts** — identical parallel behaviour re-creates the "one operator" signal even on separate IPs | 10 | TODO |

---

## G. Explicitly OUT of scope — spec §15 (listed so they are never accidentally added)

`N/A` for this build. Revisit as a future Phase 2 project.

| ID | Excluded | Note |
|----|----------|------|
| X1 | Automated appointment setter / Calendly | Pipeline stage exists, booking is **manually logged** |
| X2 | Automated meeting-booked alerts | Manual logging only |
| X3 | Meeting preparation agent | — |
| X4 | Call summary agent | — |
| X5 | Proposal generator | — |
| X6 | Post-meeting nurture sequences (Day 1/3/5/7/14) | — |
| X7 | Customer support mode | — |
| X8 | Industry-wide campaign blasting | — |
| X9 | Meta Ad Library ad-spend detection | Basic "are they running ads" **is** in scope (T6) |
| X10 | Multi-version messaging (3 tones per lead) | One message per lead |
| X11 | Smart context-aware re-engagement on live activity | **Basic** re-engagement under follow-up controls **is** in scope (T16) |

---

## H. Open decisions — need Hussein's answer before the phase that consumes them

| ID | Question | Needed by | Status |
|----|----------|-----------|--------|
| Q1 | WhatsApp sending method: Twilio, 360dialog, or the informal method? | Phase 7 | **Resolved in code** — built behind Twilio's REST API directly (no SDK). Twilio account/credentials still need to be actually created — see the still-open item below |
| Q2 | WhatsApp **notifications** — same provider as sending, or separate? | Phase 8 | Resolved — same provider (Twilio), separate config values |
| Q3 | Reply detection for LinkedIn — poll the inbox via Playwright? | Phase 8 | **Resolved in code** (2026-08-03) — `sending/linkedin_reply_check.py` opens the account's own messaging inbox via Playwright; DOM selectors are inferred, not live-verified (no real inbox conversation existed to check against) |
| Q4 | S15 alert routing per type | Phase 8 | Still open — see S15 above |
| Q5 | Which market/niche for the first real run? | Phase 10 | Still open — `target_niche`/`target_location`/`target_industry` exist in Settings but are empty pending Hussein's input |
| Q6 | Supabase project — created yet? URL + keys | Phase 1 | Resolved — live since 2026-07-24 |
| Q7 | Claude API key | Phase 4 | Resolved — `ANTHROPIC_API_KEY` confirmed present in `agent/.env` |

**New open items surfaced since this ledger was last updated:**

| Item | What's needed | Blocks |
|------|----------------|--------|
| Twilio/WhatsApp Business credentials | `WHATSAPP_API_KEY`/`WHATSAPP_API_SECRET`/`WHATSAPP_FROM_NUMBER` are not yet set in `agent/.env` — needs a real Twilio (or WhatsApp Business API) account | R3, N1–N5, T13 (WhatsApp half), T14, T17 |
| LinkedIn sending — first supervised live send | Built and verified against real pages 2026-08-03 (`sending/linkedin_send.py`); the actual click-Send has never been exercised live — real approved messages now sit ready in the dashboard (3 leads, generated 2026-08-03) specifically for this — do that once, watched, before it runs unattended | R2, T13 (LinkedIn half) |
| LinkedIn reply detection — live verification | Built and cron-wired 2026-08-03 (`sending/linkedin_reply_check.py`); its DOM selectors are inferred, not inspected against a real inbox — no contacted+replied lead exists yet to check against (needs the first supervised send above to happen first) — watch the first real check and fix any mismatch before trusting it unattended | T16 (dispatch), Q3 |
| ~~Run `database/005_add_approval_settings.sql`~~ | **DONE** — migration confirmed run against the live Supabase project (`settings.approval_reminder_hours` read back as `24` live 2026-08-03) | T12, N4, ApprovalQueue.jsx, Settings.jsx |
| ~~Full live discovery run~~ | **DONE (2026-08-03)** — see Phase 3 above; caught and fixed 6 real bugs in the process, not a clean first pass | T3, T4 |
| First supervised live send per channel | Now cron-wired (see Phase 10 note below) but never actually run end-to-end live — watch the first real LinkedIn and WhatsApp send before letting the always-on server run unattended. Real, correctly-qualified leads are now waiting in the pipeline for exactly this (3 LinkedIn approved-pending, 5 Instagram discovered/partially analyzed) | R2, R3, T13 |

---

## Deviations from the source documents (flagged, not silent)

1. **`runs` table** is created by `02_SUPABASE_SCHEMA.sql` but is missing from the Phase 1
   verification list in the build plan. It is the data source for D9 (Run Status) and T17
   (finish time in the daily summary). Treated as a 9th table and verified in Phase 1.
2. **Build order conflict.** Spec §14 bundles analysis + message generation into one step and
   orders approval before sending. `01_PHASED_BUILD_PLAN.md` splits them (Phase 4 analysis /
   Phase 5 messages). The phased build plan wins — the master prompt names it authoritative.
   No requirement is dropped by either ordering.
3. **Repo root.** `03_PROJECT_STRUCTURE.md` shows a `nexaris-outreach-agent/` wrapper folder.
   Built in place instead so the planning documents sit alongside the code rather than in a
   sibling directory. Structure below that level is exactly as specified.
