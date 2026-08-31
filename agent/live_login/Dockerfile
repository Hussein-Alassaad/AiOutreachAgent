# Build from the REPO ROOT (same convention as agent/Dockerfile):
#
#   docker build -f agent/live_login/Dockerfile -t nexaris-live-login .
#
# Separate image from agent/Dockerfile (the scheduler's) on purpose -- this
# one carries a full X11/VNC stack (Xvfb, a window manager, x11vnc,
# websockify+noVNC) that the scheduler never needs and shouldn't have to
# pull in on every rebuild. Both images still COPY the same `agent` package,
# so application code (db/, core/, live_login/ itself) stays identical
# either way; only the OS-level runtime differs.
#
# Replaces the previous approach (a plain headless Chromium, its screen
# hand-JPEG-encoded frame-by-frame over a raw websocket, with mouse/key
# events synthesized back in via CDP Input.dispatch*Event calls). That was
# unreliable for two independent reasons: (1) CDP's synthetic key events
# need an explicit `text` field to actually insert characters -- easy to
# get wrong, which is exactly what happened -- and more fundamentally (2)
# CDP-driven "clicks" and "keystrokes" are literally injected at the
# protocol level, not the OS input level, which is part of why Chromium's
# own automation-detection surface exists in the first place. Real VNC
# (x11vnc talking to a real Xvfb display) uses genuine X11 input events
# (XTestFakeKeyEvent/XTestFakeButtonEvent) -- indistinguishable from a real
# keyboard/mouse at the OS level, which is what CDP's synthetic events are
# not. This does NOT remove navigator.webdriver / CDP-attach fingerprinting
# (Chromium is still Playwright-launched, same as before, for
# build_proxy_config/_storage_path/LOGGED_IN_CHECK reuse) -- so Google's own
# "Continue with Google" button resistance is a separate, still-open
# problem this does not solve. It substantially fixes ordinary typing and
# clicking into LinkedIn/Instagram's own login forms, which is what was
# actually broken.

FROM python:3.11-slim

WORKDIR /app

# wget/gnupg/ca-certificates: Playwright's own Chromium download+verify step.
# xvfb: the virtual display Chromium renders into (headless=False needs a
#   real DISPLAY; there's no physical monitor on a droplet).
# fluxbox: a minimal window manager -- Chrome misbehaves (fails to size/
#   maximize its window sanely) on a bare Xvfb display with no WM at all.
# x11vnc: exposes that Xvfb display as a standard VNC server.
# net-tools: nothing here directly needs it, but x11vnc's startup sometimes
#   shells out to `netstat`-family tools for port checks; cheap to include.
# tini: a real init process to run as PID 1 (see ENTRYPOINT below) --
#   LIVE-VERIFIED without it: a plain shell script as PID 1 does not reap
#   orphaned zombie processes the way a real init does, so every Chromium
#   process this launches leaves a [chrome]/[chrome_crashpad] <defunct>
#   zombie behind after exit -- 35 accumulated on the droplet during one
#   afternoon of testing (`ps aux | grep defunct`), each one occupying a
#   process table slot and contributing measurably to load average on this
#   droplet's single vCPU. tini's whole purpose is exactly this Docker
#   problem (https://github.com/krallin/tini).
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates tini \
    xvfb fluxbox x11vnc net-tools \
    && rm -rf /var/lib/apt/lists/*

COPY agent/requirements.txt agent/requirements.txt
RUN pip install --no-cache-dir -r agent/requirements.txt \
    && playwright install --with-deps chromium

# noVNC: the browser-side VNC client (pure JS/HTML, no build step) --
# websockify (already in requirements.txt) is the WebSocket<->TCP bridge in
# front of x11vnc; noVNC is the static page that speaks RFB-over-websocket
# to it. Pinned to a tagged release, not `master`, so a rebuild doesn't
# silently pick up an unreviewed upstream change.
RUN wget -q https://github.com/novnc/noVNC/archive/refs/tags/v1.5.0.tar.gz -O /tmp/novnc.tar.gz \
    && mkdir -p /app/novnc \
    && tar xzf /tmp/novnc.tar.gz -C /app/novnc --strip-components=1 \
    && rm /tmp/novnc.tar.gz

COPY agent agent
COPY agent/live_login/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:1

EXPOSE 8765

# tini as PID 1 (not entrypoint.sh directly) -- see the apt-get install
# comment above for why. `--` separates tini's own flags from the command
# it's supervising.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
