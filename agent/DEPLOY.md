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

## What's NOT wired into the always-on schedule yet

`agent/server.py` only cron-schedules `run_cycle` per account (via
`scheduler.build_daily_schedule`) -- the same thing `build_daily_schedule`
has always done. Discovery, analysis, message generation, sending, and
WhatsApp reply-checking (`run_discovery_cycle`, `run_analysis_cycle`,
`run_message_generation_cycle`, `run_sending_cycle`,
`sending/whatsapp_reply_check.check_whatsapp_replies`) are real, tested
functions but are still triggered manually today, not on their own cron
jobs. Wiring those into `build_daily_schedule` is a deliberate follow-on
step once each is confirmed working end-to-end against live accounts --
not something to bolt on here without that verification.
