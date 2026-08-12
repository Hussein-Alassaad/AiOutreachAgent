# Deploying the agent (Phase 10)

The agent is one long-lived process (`agent/server.py`, APScheduler's
`BackgroundScheduler` under the hood) -- not a cron job, not serverless. It
needs a machine that's always on. The dashboard is a static build and goes to
Vercel separately; this file only covers the agent.

Recommended: a Hetzner CX22 (cheapest shared vCPU box, ~EUR4/mo) or Oracle
Cloud's Always Free ARM tier if you'd rather not pay. Either is fine --
nothing here needs more than 1-2 vCPUs and a couple GB of RAM. Ubuntu 22.04
or later.

## Choice made here: `docker run --restart unless-stopped`, not systemd

Both work. `--restart unless-stopped` was picked because Docker's own daemon
is already a systemd service that starts on boot on every mainstream distro,
so the restart policy alone covers both crash recovery and reboot recovery
without a second systemd unit to keep in sync with the image tag. If you'd
rather manage it as a systemd unit instead (e.g. to fit an existing fleet's
conventions), swap step 5 for a unit file that runs the same `docker run`
line with `Restart=always`.

## 1. Install Docker on the VPS

```bash
curl -fsSL https://get.docker.com | sh
```

## 2. Get the code onto the VPS

```bash
git clone <this repo> nexaris-agent
cd nexaris-agent
```

## 3. Build the image

Build from the repo root, not `agent/` -- the Dockerfile expects `agent` to
land in the image as an importable package (see agent/Dockerfile's header
comment).

```bash
docker build -f agent/Dockerfile -t nexaris-agent .
```

## 4. Set the environment variables

Every value the agent reads comes from `agent/config.py`, which loads
`agent/.env` locally -- on the server, pass real env vars to `docker run`
instead (`agent/.env` is dev-only and is never baked into the image). Same
keys either way; see `.env.example` at the repo root for what each one is
for:

- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`
- `ANTHROPIC_API_KEY`, `MODEL_ANALYSIS`, `MODEL_MESSAGES`
- `WHATSAPP_PROVIDER`, `WHATSAPP_API_KEY`, `WHATSAPP_API_SECRET`, `WHATSAPP_FROM_NUMBER`
- `TIMEZONE`, `HEADLESS` (must be `true` on the server -- no display here), `DEV_MAX_LEADS_PER_ACCOUNT` (leave unset in production)

Put real values in a file the shell won't echo into `docker inspect`, e.g.
`/etc/nexaris-agent.env` on the VPS (`KEY=value` per line, same format as
`.env`), and reference it with `--env-file` below rather than `-e` per var.

## 5. Run it

```bash
docker run -d \
  --name nexaris-agent \
  --restart unless-stopped \
  --env-file /etc/nexaris-agent.env \
  -v nexaris-browser-profiles:/app/agent/browser_profiles \
  nexaris-agent
```

The volume matters: `agent/core/session.py` persists each account's logged-in
browser session (cookies/local storage) to `agent/browser_profiles/` between
runs, specifically so accounts don't re-authenticate from scratch every day
-- that itself looks like a bot signal to LinkedIn/Instagram. Without the
volume, a container restart wipes every account's login and Hussein has to
redo the manual-login step for all 3 accounts.

## 6. First-time account login

`SessionManager` restores a saved session if one exists, but the first login
per account still has to happen through a real, visible browser (see
`agent/core/session.py` and `tools/capture_session.py`) -- not something to
do headless inside the container. Run the manual-login step locally first
(same machine you'd use for local development, `HEADLESS=false`), then copy
the resulting `agent/browser_profiles/<account-id>.json` files onto the
server before starting the container, or straight into the
`nexaris-browser-profiles` volume.

## Operating it

```bash
docker logs -f nexaris-agent      # tail scheduler output
docker restart nexaris-agent      # pick up a new .env value
docker build -f agent/Dockerfile -t nexaris-agent . && \
  docker stop nexaris-agent && docker rm nexaris-agent && \
  docker run -d --name nexaris-agent --restart unless-stopped \
    --env-file /etc/nexaris-agent.env \
    -v nexaris-browser-profiles:/app/agent/browser_profiles \
    nexaris-agent                   # deploy a new build
```

## What's wired into the always-on schedule (updated 2026-08-03)

`scheduler.build_daily_schedule` now schedules the full daily pipeline:
`run_discovery_cycle` per account at that account's own `run_time`
(replacing `run_cycle`'s Phase 2 placeholder role), plus one shared
`run_full_pipeline_cycle` job once a day (analysis -> message generation ->
sending -> approval-reminder check -> WhatsApp reply check -> LinkedIn
reply check) -- see `scheduler.py`'s own docstrings for exactly why the
downstream steps are scheduled once rather than per account.

**This reverses an earlier deliberate deferral** -- these steps were
previously left manual-trigger-only until each was confirmed working
end-to-end against live accounts, and that confirmation still hasn't
happened (see `PROGRESS.md`/`REQUIREMENTS_COVERAGE.md`: no channel has
completed an actual supervised live send yet, Twilio credentials aren't
configured, and LinkedIn reply-detection's own DOM selectors are inferred,
not inspected against a real inbox). Wiring them into cron now was a
direct, explicit request, not a conclusion that they're production-ready.
**Do not actually start this scheduler on a real server (`agent/server.py`)
until at least one supervised live send per channel has been watched and
confirmed, and LinkedIn reply-detection has been checked against a real
conversation** -- right now, starting it would mean the first-ever
LinkedIn/WhatsApp send, and the first-ever LinkedIn reply check, happen
unattended.
