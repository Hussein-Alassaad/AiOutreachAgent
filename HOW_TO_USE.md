# HOW TO USE THIS PACKAGE — for Hussein

You have 4 files. Here's exactly what to do with them in Claude Code.

## The files

1. **00_MASTER_PROMPT.md** — the main instruction. This tells Claude Code who it is, what it's building, the rules, and how to behave (explain everything, work in phases, confirm before moving on).
2. **01_PHASED_BUILD_PLAN.md** — the 11 phases (Phase 0 → Phase 10), in order.
3. **02_SUPABASE_SCHEMA.sql** — the full database, ready to run in Supabase.
4. **03_PROJECT_STRUCTURE.md** — the exact folder layout.

Plus keep **Nexaris_Agent_COMPLETE_SPEC_FINAL.pdf** handy — it's the full reference if Claude Code needs a detail.

## Steps

**1. Set the model to Opus** in Claude Code (`/model` → Opus). Use Opus for Phases 0–4 (the complex foundation). You can switch to Sonnet later for routine dashboard work.

**2. Start a new Claude Code session** in an empty folder where you want the project built.

**3. Paste the entire contents of `00_MASTER_PROMPT.md`** as your first message, and attach the other 3 files (and the spec PDF) to the same message.

**4. Let Claude Code respond.** It will:
   - Read everything
   - Tell you its understanding of the project in its own words
   - Ask you any clarifying questions
   - Wait for your go-ahead

**5. Answer its questions**, then tell it to begin Phase 0.

**6. After each phase**, Claude Code will stop, summarize what it built, show you how to test it, and ask you to confirm before continuing. Test it, confirm, and it moves to the next phase.

## Things you'll need to have ready

Before Phase 4 (Claude analysis) you need:
- **A Claude API key** (from console.anthropic.com)

Before Phase 1 (database) you need:
- **A Supabase project** (free — supabase.com) with its URL and keys

Before Phase 7 (WhatsApp sending) you need:
- A **WhatsApp Business API** account (Twilio or 360dialog) OR decide on the informal method

Before Phase 10 (deploy + real use) you need:
- **3 dedicated sticky residential proxy IPs** (DataImpulse / IPRoyal / Evomi) — one per account
- **An always-on server** (Oracle Cloud free tier or a ~$5/month Hetzner VPS)
- **A Vercel account** (free) for the dashboard

You do NOT need the proxies or server until Phase 10 — you can build and test everything before then locally with no proxy at low volume.

## Important reminders (Claude Code has these too, but so you know)

- **Instagram never auto-sends** — the agent prepares those messages, a human sends them from the dashboard.
- **Nothing sends without Mahmoud's daily approval.**
- **Each account gets its own proxy IP** — never shared.
- **Warm up slowly** — start at 5–10/day per account, ramp up over 2–4 weeks. Do not start at full volume with the shared-origin accounts.

## If a session runs out of context

Claude Code keeps a `PROGRESS.md` file in the repo. If you start a fresh session, point it at `PROGRESS.md` and the master prompt, and it can continue where it left off.
