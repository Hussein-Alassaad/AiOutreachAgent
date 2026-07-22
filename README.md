# Nexaris AI Outreach Agent

An automated outreach agent that discovers business leads on LinkedIn and Instagram,
analyses each business with Claude, scores it 1–10 with written reasoning, and writes
a personalised message — which sends automatically on LinkedIn and WhatsApp only
after daily human approval. Instagram messages are prepared but always sent by hand.
Everything lands in a CRM with a pipeline, shown in a responsive dashboard that works
on laptop and iPhone.

**Status:** Phase 0 of 11 complete. See [PROGRESS.md](PROGRESS.md).

---

## The three channel rules

| Channel | Discovery | Sending |
|---------|-----------|---------|
| **LinkedIn** | Automated | Automated (after approval) |
| **WhatsApp** | — | Automated (after approval), only when a public number is found |
| **Instagram** | Automated | **Never automated** — prepared for a human to send manually |

Instagram's terms ban automated cold outreach, so the agent finds, analyses, scores,
and writes the message, then stops. Nothing sends on any channel without Mahmoud's
daily approval — that gate has no bypass.

---

## Running it

### Agent (Python)

```bash
# One-time setup
python -m venv agent/venv
agent/venv/Scripts/python.exe -m pip install -r agent/requirements.txt
agent/venv/Scripts/python.exe -m playwright install chromium   # from Phase 2
cp .env.example agent/.env                                     # then fill it in

# Run
agent/venv/Scripts/python.exe -m agent.main
```

On macOS or Linux use `agent/venv/bin/python` instead.

### Dashboard (React PWA)

```bash
cd dashboard
npm install
cp ../.env.example .env        # keep only the VITE_ lines, then fill them in
npm run dev
```

Opens on `http://localhost:5173`. The dev server binds to your local network too, so
you can open it on a phone using your laptop's IP to test the mobile layout.

---

## Layout

```
agent/          Python agent — discovery, analysis, messaging, sending, CRM
  core/         Account pool, browser sessions, health checks, warm-up
  discovery/    LinkedIn + Instagram search and qualification
  analysis/     Claude analysis, founder detection, scoring
  messaging/    Message generation and style rotation
  sending/      Channel routing
  crm/          Pipeline, follow-up, reply detection
  db/           Supabase access
dashboard/      React + Tailwind PWA
database/       Reference copy of the Supabase schema
```

The agent and dashboard are separate apps sharing one Supabase database. The agent
writes data; the dashboard reads it and writes back settings, approvals, and manual
actions. Supabase Realtime keeps the dashboard live as the agent works.

---

## Reference documents

| File | What's in it |
|------|--------------|
| [00_MASTER_PROMPT.md](00_MASTER_PROMPT.md) | How the build is run, the 10 core rules |
| [01_PHASED_BUILD_PLAN.md](01_PHASED_BUILD_PLAN.md) | The 11 phases in order |
| [02_SUPABASE_SCHEMA.sql](02_SUPABASE_SCHEMA.sql) | Full database schema |
| [03_PROJECT_STRUCTURE.md](03_PROJECT_STRUCTURE.md) | Intended folder layout |
| [REQUIREMENTS_COVERAGE.md](REQUIREMENTS_COVERAGE.md) | Every requirement mapped to a phase |
| [PROGRESS.md](PROGRESS.md) | What's built, what's next, decisions made |

---

## Stack

React + Tailwind (PWA on Vercel) · Python + Playwright (agent on an always-on
server) · Claude API — Haiku for analysis, Sonnet for messages · Supabase for
Postgres, Auth, and Realtime · one dedicated sticky residential proxy IP per account.
