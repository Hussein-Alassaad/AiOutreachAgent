# PROJECT STRUCTURE — Nexaris AI Outreach Agent

This is the folder layout Claude Code should set up in Phase 0. Two main parts: the **agent** (Python) and the **dashboard** (React). They share one Supabase database.

```
nexaris-outreach-agent/
│
├── PROGRESS.md                  # updated after every phase — what's done, what's next
├── README.md                    # how to run everything
├── .env.example                 # every env var needed (no real secrets)
│
├── agent/                       # ── THE PYTHON AGENT ──
│   ├── requirements.txt
│   ├── .env                     # real secrets (gitignored)
│   ├── main.py                  # entry point — the daily run loop
│   ├── scheduler.py             # runs each account at its own time
│   │
│   ├── core/
│   │   ├── account_pool.py      # loads 3 accounts, manages rotation
│   │   ├── session.py           # Playwright browser context per account + proxy
│   │   ├── health.py            # detects warnings, records type + reason
│   │   └── warmup.py            # per-account warm-up ramp logic
│   │
│   ├── discovery/
│   │   ├── linkedin.py          # LinkedIn search + profile reading
│   │   ├── instagram.py         # Instagram search + profile reading
│   │   └── qualify.py           # is this a real business worth pursuing?
│   │
│   ├── analysis/
│   │   ├── analyze.py           # deep company analysis (Claude Haiku)
│   │   ├── founder.py           # founder detection from bio
│   │   ├── whatsapp_detect.py   # find public WhatsApp number
│   │   ├── score.py             # lead scoring 1-10 + reasoning (Haiku)
│   │   └── prompts.py           # all Claude prompt templates (with caching)
│   │
│   ├── messaging/
│   │   ├── generate.py          # message generation (Claude Sonnet)
│   │   └── style.py             # message style + duration rotation logic
│   │
│   ├── sending/
│   │   ├── linkedin_send.py     # auto-send on LinkedIn
│   │   ├── whatsapp_send.py     # auto-send on WhatsApp (when number found)
│   │   └── instagram_queue.py   # push to dashboard manual-send queue (NO auto-send)
│   │
│   ├── crm/
│   │   ├── pipeline.py          # stage movement + history
│   │   ├── followup.py          # follow-up + re-engagement scheduling (max 2 contacts)
│   │   └── reply_detection.py   # detect replies, move pipeline
│   │
│   ├── notifications/
│   │   └── whatsapp_notify.py   # all 5 alert types to both numbers
│   │
│   └── db/
│       ├── client.py            # Supabase connection
│       └── repositories.py      # read/write functions per table
│
├── dashboard/                   # ── THE REACT DASHBOARD (PWA) ──
│   ├── package.json
│   ├── vite.config.js
│   ├── tailwind.config.js
│   ├── .env                     # frontend env (Supabase public keys)
│   ├── public/
│   │   ├── manifest.json        # PWA manifest (installable on iPhone)
│   │   └── icons/               # app icons for home screen
│   │
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       ├── lib/
│       │   └── supabase.js       # Supabase client
│       │
│       ├── components/           # shared UI pieces
│       │   ├── LeadCard.jsx
│       │   ├── KpiCard.jsx
│       │   ├── TemperatureBadge.jsx
│       │   └── ...
│       │
│       └── pages/                # one file per dashboard section (spec §7)
│           ├── LiveFeed.jsx          # today's leads
│           ├── InstagramManualSend.jsx  # IG messages to send by hand
│           ├── ApprovalQueue.jsx     # Mahmoud approves messages
│           ├── PipelineBoard.jsx     # kanban
│           ├── LeadDetail.jsx        # full lead record
│           ├── ClientHistory.jsx     # permanent database
│           ├── Analytics.jsx         # all KPIs + charts
│           ├── AccountHealth.jsx     # 3 accounts, warnings, redistribution
│           ├── RunStatus.jsx         # live run tracking + finish time
│           └── Settings.jsx          # every editable control (spec §8)
│
└── database/
    └── schema.sql               # copy of 02_SUPABASE_SCHEMA.sql for reference
```

## Notes for Claude Code

- **Agent and dashboard are separate apps** sharing one Supabase DB. The agent writes data; the dashboard reads it (and writes settings/approvals/manual actions back).
- **Realtime:** use Supabase Realtime so the dashboard updates live as the agent works (live feed, run status, analytics).
- **The PWA manifest + icons** are what make it installable on Mahmoud's iPhone — don't skip these in Phase 9.
- **Secrets** (Claude API key, proxy credentials, WhatsApp API) live in `agent/.env`, never committed. `.env.example` documents them.
- Keep each file focused — one responsibility. Hussein wants to be able to open any file and understand it.
