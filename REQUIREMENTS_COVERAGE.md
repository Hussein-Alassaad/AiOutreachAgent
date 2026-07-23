# REQUIREMENTS COVERAGE LEDGER — Nexaris AI Outreach Agent

Every requirement from `Nexaris_Agent_COMPLETE_SPEC_FINAL.pdf`, the master prompt, and the
build plan is listed here with an ID, the phase that delivers it, and the file it lands in.

**Purpose:** Hussein asked that nothing from the spec be missed. A promise isn't checkable —
this table is. At the end of every phase, the rows for that phase get marked `DONE` and the
phase is not closed until all of its rows are marked.

**Status legend:** `TODO` = not started · `WIP` = in progress · `CODE` = code written, not
yet verified live · `DONE` = built and tested by Hussein · `N/A` = deliberately out of scope

**Phase 1 progress (2026-07-23):** `database/policies.sql` (RLS), `agent/db/repositories.py`
(25 functions, all 9 tables), and `agent/db/smoke_test.py` are written and import-clean.
The live run + verify in Supabase is blocked on Hussein's keys (Q6). Settings rows
S1–S8, S14 get their storage from the schema now; their dashboard UI is Phase 9.

---

## A. The 10 core rules (never violated — these are constraints, not features)

| ID | Rule | Enforced in | Phase | Status |
|----|------|-------------|-------|--------|
| R1 | Instagram is **find-only** — never auto-sends | `sending/instagram_queue.py` | 7 | TODO |
| R2 | LinkedIn is fully automated — find **and** send | `sending/linkedin_send.py` | 7 | TODO |
| R3 | WhatsApp auto-sends **only** when a public number was found | `sending/whatsapp_send.py` | 7 | TODO |
| R4 | Nothing sends without Mahmoud's approval — hard gate, no bypass | approval gate in `sending/` | 6 | TODO |
| R5 | Messages are AI-generated per lead, human tone | `messaging/generate.py` | 5 | TODO |
| R6 | Each account uses its own dedicated sticky proxy IP — never shared | `core/session.py` | 2 | TODO |
| R7 | Founder detection flags for **manual** outreach — never auto-contacts founder | `analysis/founder.py` | 4 | TODO |
| R8 | Max **2** contacts per lead, ever | `crm/followup.py` + DB constraint | 8 | TODO |
| R9 | Warnings show **type + reason**; redistribution is manual only | `core/health.py` | 2 | TODO |
| R10 | Everything editable from the dashboard, no code changes | `pages/Settings.jsx` | 9 | TODO |

---

## B. Agent tasks — spec §6 (all 17, in execution order)

| ID | Task | Detail that must not be lost | File | Phase | Status |
|----|------|------------------------------|------|-------|--------|
| T1 | Boot & load config | Loads niche, location, language, targets, style, style duration, proxy creds **+ the full previously-contacted list (dedup)** + the re-contact-eligible list | `main.py` | 2 | TODO |
| T2 | Health check & session open | Warning **type + specific reason** (not generic); pause account; wait for Hussein on redistribution; isolated context per proxy IP | `core/health.py`, `core/session.py` | 2 | TODO |
| T3 | LinkedIn discovery | Search by niche/industry/company size/location/activity; 30/account; reads company name, description, headcount, website, recent posts, public contact info; **prioritises recently active over dormant**; adapts search terms if results are weak | `discovery/linkedin.py` | 3 | TODO |
| T4 | Instagram discovery | Hashtags + explore + location tags; 20/account; reads bio, followers, post count, engagement, story signals; skips inactive/irrelevant; **reads only, sends nothing** | `discovery/instagram.py` | 3 | TODO |
| T5 | Business qualification | Reasoning decision, **not a keyword filter**; skip personal/inactive/no clear service/wrong niche | `discovery/qualify.py` | 3 | TODO |
| T6 | Deep profile analysis | Company size · revenue tier · industry · **website analysis (design quality, booking flow, CTA, load speed)** · ads activity · all social platforms active on · **full weak points list** · AI opportunities. Stored and shown **in full**, never summarised | `analysis/analyze.py` | 4 | TODO |
| T7 | Founder detection | Phrases: "founder of X", "co-founder", "CEO & founder", "established by", "created by", "owner of", + any equivalent wording. Stores founder name **and the matched bio phrase**. Dashboard shows **two flags** on one card (company messaged + founder found). Fires WhatsApp alert | `analysis/founder.py` | 4 | TODO |
| T8 | WhatsApp number detection | Checks bio, profile description, website link, contact section — on **both** platforms. Public listed numbers only, **never guessed or scraped**. Fires WhatsApp channel **in addition to**, not instead of, the primary channel | `analysis/whatsapp_detect.py` | 4 | TODO |
| T9 | Lead scoring | 1–10 from **fit + pain + budget potential**; Hot 8–10 / Warm 5–7 / Cold 1–4; **written reasoning always present** | `analysis/score.py` | 4 | TODO |
| T10 | Save to Client History | Happens **before** any message is generated; permanent; saved whether or not outreach ever happens | `db/repositories.py` | 4 | TODO |
| T11 | Message generation | Sonnet; human tone; greeting uses business/team/**founder** name; references that lead's specific weak points; Direct vs Discovery style; style holds for the configured duration then rotates | `messaging/generate.py`, `messaging/style.py` | 5 | TODO |
| T12 | Queue for approval | All of the day's messages queued; Mahmoud edits/approves/holds; nothing sends on **any** channel until approved | `crm/` + `pages/ApprovalQueue.jsx` | 6 | TODO |
| T13 | Route & send | LinkedIn auto · WhatsApp auto · Instagram to manual queue. Every send logged with **timestamp, account used, platform, message text** | `sending/*` | 7 | TODO |
| T14 | Hot lead instant alert | Fires **immediately on scoring**, not at end of run. Includes name, platform, score, reasoning, **dashboard deep-link** | `notifications/whatsapp_notify.py` | 8 | TODO |
| T15 | CRM record + pipeline entry | New → Contacted → Replied → Interested → Meeting Booked → Deal Closed → Lost. Auto-move on reply; manual moves allowed; **every change timestamped** | `crm/pipeline.py` | 8 | TODO |
| T16 | Follow-up & re-engagement | **Per-lead** on/off + timing set by Hussein (agent never picks the delay); re-engagement for replied-then-cold; **pauses instantly on reply**; max 2 contacts; per-lead re-contact gap | `crm/followup.py` | 8 | TODO |
| T17 | Daily summary | WhatsApp to both numbers: leads found by platform, messages sent by platform, hot/warm/cold breakdown, warnings that occurred, **exact finish time, total duration** | `notifications/whatsapp_notify.py` | 8 | TODO |

---

## C. Dashboard sections — spec §7 (all 10)

| ID | Section | Every element required | Phase | Status |
|----|---------|------------------------|-------|--------|
| D1 | Daily Live Feed | Card per lead: business name, platform badge, profile photo, follower/headcount, industry, score, temperature badge (colour-coded), **ALL weak points — full list, not hidden**, AI opportunities, founder flag (two-part), generated message **copyable in one tap**, pipeline status. Hot leads **highlighted and pinned to top**. Filters: platform, score range, temperature, date. Click → Lead Detail | 9 | TODO |
| D2 | Instagram Manual Send | Separate section; shows **only post-approval** IG leads; name, score, temperature, copyable message, founder flag, **"Mark as Sent"** → enters pipeline as Contacted; shows pending vs sent today; **must work on iPhone** | 9 | TODO |
| D3 | Approval Section | Per item: name, platform, score, temperature, full message text, **Edit + Approve**; individual approve **and Approve All**; **count badge** ("14 awaiting"); mobile-first | 9 | TODO |
| D4 | CRM Pipeline Board | 7 kanban columns; card shows name, platform, score, temperature, last activity; **drag and drop**; auto-move on reply; click → Lead Detail | 9 | TODO |
| D5 | Lead Detail | Everything: profile data, website, all social platforms, full weak points, AI opportunities, founder name + flag, WhatsApp number, score + **full reasoning**, temperature, **all messages ever sent with dates**, **all replies with dates**, stage + **full stage history with timestamps**, follow-up settings, contact count, first contact date, **editable notes**. Edit follow-up here. Move stage here | 9 | TODO |
| D6 | Client History | **Permanent, never resets.** Search/filter by name, industry, platform, score, temperature, date range, contacted y/n, founder y/n, stage. Totals at top: ever analysed, contacted, hot/warm/cold | 9 | TODO |
| D7 | Analytics & KPIs | **Headline cards:** businesses reached (all/month/week/today), messages sent, replies + reply rate %, meetings booked, deals closed, hot leads found. **Charts:** reached per platform (bar), per account (bar), sent vs pending approval, leads per stage (funnel), hot/warm/cold (donut), reply rate per platform (bar), founders detected + founders contacted, IG-specific (found / sent manually / pending). **Daily activity timeline (line).** Filters: date range, platform, account. **Real-time updates** | 9 | TODO |
| D8 | Account Health | 3 accounts: Active/Warned/Paused; **specific warning type + reason**; per-account **redistribute toggle** (manual); usage today vs limit; last active time; **proxy status: connected/missing/error** | 9 | TODO |
| D9 | Run Status | Running/Idle/Error; which account is active and **what task it's on**; start + **finish time**; **total duration** ("Completed in 2h 14m"); progress "X of 150 processed" | 9 | TODO |
| D10 | Settings | Every control in section D below | 9 | TODO |
| D11 | PWA | `manifest.json` + icons; installable via Safari → Add to Home Screen; full-screen on iPhone | 9 | TODO |

---

## D. Editable settings — spec §8 (nothing here may require a code change)

| ID | Setting | Phase | Status |
|----|---------|-------|--------|
| S1 | Target niche / industry / business type | 1 (schema) → 9 (UI) | TODO |
| S2 | Target location / country / market — **geography-configurable, not hardcoded** | 1 → 9 | TODO |
| S3 | Outreach language(s) | 1 → 9 | TODO |
| S4 | Instagram leads/day per account (default 20) | 1 → 9 | TODO |
| S5 | LinkedIn leads/day per account (default 30) | 1 → 9 | TODO |
| S6 | Run time — **per account, set independently** (not one shared time) | 1 → 9 | TODO |
| S7 | Active message style (Direct / Discovery / extensible) | 1 → 9 | TODO |
| S8 | Style duration before rotation | 1 → 9 | TODO |
| S9 | Per-lead follow-up on/off + timing (from Lead Detail) | 8 → 9 | TODO |
| S10 | Re-contact gap per lead | 8 → 9 | TODO |
| S11 | Contact cap = 2 — **deliberately NOT user-editable** | 8 | TODO |
| S12 | Proxy IP/credentials per account × 3 — **editable slot, may be empty at launch** | 2 → 9 | TODO |
| S13 | Redistribution toggle per account — **never automatic** | 2 → 9 | TODO |
| S14 | WhatsApp recipient number 1 + number 2 | 1 → 9 | TODO |
| S15 | Optional per-type alert routing (e.g. approval reminders → Mahmoud only) — spec says "finalise during build" | 8 | OPEN |

---

## E. WhatsApp notifications — spec §9 (all 5)

| ID | Alert | Trigger | Recipients | Must include | Phase | Status |
|----|-------|---------|------------|--------------|-------|--------|
| N1 | Hot lead | Score 8–10, **immediately on scoring** | Both | name, platform, score, reasoning, dashboard link | 8 | TODO |
| N2 | Founder found | Founder detected alongside company message | Both | name, founder name, platform, dashboard link | 8 | TODO |
| N3 | Account warning | Immediately on account problem | Both | which account, warning type, **specific reason** | 8 | TODO |
| N4 | Approval reminder | Day's messages queued | Mahmoud | count waiting | 8 | TODO |
| N5 | Daily summary | Run completion | Both | leads by platform, messages by platform, hot/warm/cold, warnings, **finish time, duration** | 8 | TODO |

---

## F. Account safety & proxy plan — spec §11

| ID | Requirement | Phase | Status |
|----|-------------|-------|--------|
| P1 | Sticky proxies only — **rotating proxies must never be used** | 2 | TODO |
| P2 | Residential (or mobile for IG) — **datacenter proxies must never be used** | 2/10 | TODO |
| P3 | Account → IP is a permanent 1:1 assignment; contexts never mix | 2 | TODO |
| P4 | Proxy config slot exists from day one even when empty; filling it later is **settings-only, zero code change** | 2 | TODO |
| P5 | Warm-up: start 5–10/day per account, ramp over 2–4 weeks to 20 IG + 30 LI | 10 | TODO |
| P6 | Auto warm-up schedule — increments weekly toward the configured max (`core/warmup.py`) | 2/10 | TODO |
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

| ID | Question | Needed by | Default I'll use if you don't specify |
|----|----------|-----------|---------------------------------------|
| Q1 | WhatsApp sending method: Twilio, 360dialog, or the informal method? | Phase 7 | Build behind an interface so the provider is swappable; pick Twilio as the reference implementation |
| Q2 | WhatsApp **notifications** — same provider as sending, or separate? | Phase 8 | Same provider, separate config |
| Q3 | Reply detection for LinkedIn — poll the inbox via Playwright? | Phase 8 | Poll on each run |
| Q4 | S15 alert routing per type | Phase 8 | All 5 to both numbers; approval reminder to Mahmoud only |
| Q5 | Which market/niche for the first real run? | Phase 10 | Left empty in settings, you fill it in |
| Q6 | Supabase project — created yet? URL + keys | Phase 1 | Blocks Phase 1 |
| Q7 | Claude API key | Phase 4 | Blocks Phase 4 |

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
