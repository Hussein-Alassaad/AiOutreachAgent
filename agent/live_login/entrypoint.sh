#!/bin/sh
# Starts the X11/VNC stack this container needs, in dependency order, then
# runs the actual Python server in the foreground so `docker logs` shows its
# output and `docker stop`'s SIGTERM reaches it directly (not a supervisor
# process). Deliberately NOT using a full init system (s6-overlay,
# supervisord) -- three fixed background processes with a known startup
# order is simple enough to sequence by hand with `wait-for` sleeps, and it
# keeps this image's surface small.
set -e

# 1. Virtual display. -ac disables X access control (fine: this display is
#    only reachable from inside the container's own network namespace,
#    never bound to a host port directly -- x11vnc is the only thing that
#    talks to it, and x11vnc itself is what's password/token-gated).
Xvfb "$DISPLAY" -screen 0 1366x768x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# LIVE-CONFIRMED 2026-08-31: the fixed 1s sleep this used to be wasn't
# always enough -- a real container restart immediately followed by a real
# connect attempt (a client retrying right after this container came back
# up) hit Chromium launching before Xvfb's socket actually existed yet
# ("Missing X server or $DISPLAY"), even though this same 1s sleep had
# looked reliable in every earlier manual test. Poll for the actual X11
# socket file instead of guessing a fixed duration -- DISPLAY=":1" means
# the socket is /tmp/.X11-unix/X1 (strip the leading colon). Capped at 10s
# total so this can't hang forever if Xvfb itself fails to start; that
# failure surfaces on its own once x11vnc/Chromium try to use the display
# and can't.
DISPLAY_NUM=$(echo "$DISPLAY" | sed 's/^://')
for i in $(seq 1 100); do
  [ -S "/tmp/.X11-unix/X${DISPLAY_NUM}" ] && break
  sleep 0.1
done

# 2. Window manager. Runs detached from any particular window; just keeps
#    Chrome from rendering as an unmanaged, oddly-sized top-level window.
fluxbox &

sleep 1

# 3. VNC server on top of the Xvfb display. -nopw: no VNC-level password --
#    auth is enforced one layer up, by live_login/server.py's existing
#    JWT-gated WebSocket handshake, before any byte of this connection is
#    proxied through. -forever: keep serving reconnects instead of exiting
#    after the first client disconnects (a session can legitimately have
#    the tab closed and reopened during troubleshooting). -shared: allow
#    the Python server's own supervision to coexist if it ever needs to
#    peek at the display.
#
# CPU-conscious flags -- this droplet is 1 vCPU (LIVE-VERIFIED via `nproc`
# on the real droplet), and x11vnc's default polling is tuned for a
# multi-core desktop, not a single shared core also running Chromium at
# the same time:
#   -wait 100: poll for screen changes every 100ms instead of x11vnc's much
#     more aggressive default (~a few ms) -- a login form isn't a video,
#     it doesn't need every possible frame; trading a little latency for
#     meaningfully less CPU spent on constant polling is the right trade
#     here specifically because the CPU IS the bottleneck (confirmed via
#     `top` showing load average > vCPU count during real use).
#   -defer 50: batch/coalesce rapid-fire changes (e.g. a page still
#     laying out) into fewer actual frame sends rather than one per
#     micro-change.
#   -threads: use x11vnc's threaded polling, which parallelizes the
#     encode step across whatever the OS scheduler can give it -- on a
#     single vCPU this doesn't add real parallelism, but avoids x11vnc's
#     own internal serialization overhead on some code paths; harmless
#     either way.
x11vnc -display "$DISPLAY" -nopw -forever -shared -quiet -rfbport 5900 \
  -wait 100 -defer 50 -threads &

sleep 1

# 4. websockify: bridges the VNC server's raw TCP protocol to a WebSocket,
#    and also serves noVNC's static files (the browser-side VNC client) on
#    the same port via --web. Bound to 127.0.0.1 only -- like the Python
#    server's own port, this must never be reachable except from inside
#    this container (live_login/server.py proxies to it after its own auth
#    check, exactly as documented in that file).
websockify --web /app/novnc 127.0.0.1:6080 127.0.0.1:5900 &

# 5. The actual application server -- runs in the foreground so this
#    script's own process IS the container's PID 1 concern (signals from
#    `docker stop` reach python directly via `exec`).
exec python -m agent.live_login.server
