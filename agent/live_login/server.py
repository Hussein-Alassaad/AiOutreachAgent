"""
Entry point for the "Connect account" websocket service. Runs as its own
Docker container on the droplet (see agent/live_login/Dockerfile), separate
from the scheduler process, so a live login session crashing never takes
down scheduled runs and vice versa.

A tenant's browser opens wss://<host>/connect/{account_id}?token=... directly
(bypassing Vercel/Next.js entirely -- see src/lib/actions/outreach-live-login.ts
for where that token is minted). This module: parses account_id from the
path, verifies the token, loads the account (tenant-scoped), starts the
real browser session (live_login.session.start_login_session -- launches
Chromium onto the container's own Xvfb display, proxy-configured for that
account), and then PROXIES this websocket to the local websockify port
(127.0.0.1:6080, see entrypoint.sh) that fronts noVNC/x11vnc for that same
display -- raw byte relay in both directions, for the entire lifetime of
the connection. noVNC's RFB client (ConnectAccountModal.tsx) expects to
own the whole socket as pure VNC protocol, so this module no longer
interleaves any app-level JSON onto it (the old {"type":"success"/"error"}
messages are gone) -- login detection runs as an independent background
task that writes the outcome straight to OutreachAccount.loginStatus, and
the frontend finds out by POLLING that field (same pattern already used
elsewhere in this app), fully decoupled from the video stream.

Run: python -m agent.live_login.server
"""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlparse, parse_qs

from websockets.asyncio.server import serve, ServerConnection
from websockets.asyncio.client import connect as ws_connect
from websockets.http11 import Request, Response
from websockets.datastructures import Headers

from agent import config
from agent.core.session import _storage_path
from agent.db import repositories as repo
from agent.live_login import import_server
from agent.live_login.auth import verify_connect_token, TokenInvalid
from agent.live_login.session import start_login_session, LiveLoginError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Guards against two concurrent sessions for the same account -- belt-and-
# suspenders alongside startConnectAccountAction()'s own loginStatus=="connecting"
# check, in case two token-mint calls raced before the first DB write committed.
#
# LIVE-CONFIRMED 2026-08-31: this used to be a bare set[str]. When a
# session hung (its own _proxy_and_watch coroutine never returned --
# _WATCHDOG_SECONDS below now bounds that from the outside), the
# account's id stayed in this set until the watchdog fired, and every
# retry in between got a flat "already in progress" with no way out short
# of restarting the whole container (which kills every OTHER account's
# in-flight session too, not just the stuck one).
#
# A fixed short timeout here (tried 60s first) is wrong in the other
# direction: a legitimate session can run up to _LOGIN_TIMEOUT_SECONDS
# (10 min) while a real person works through a LinkedIn checkpoint, and a
# short timer would let a SECOND concurrent session start against the
# same account mid-legitimate-use -- exactly the double-session race this
# guard exists to prevent in the first place. What actually distinguishes
# "hung" from "legitimately still running" isn't elapsed time, it's
# whether the handler() coroutine that's doing the work is still actually
# alive -- so this maps account_id -> that coroutine's own asyncio.Task
# and asks it directly (task.done()) rather than guessing from a clock.
# A task can only ever be wrongly reported "not done" if the interpreter
# itself is wedged, which is a different failure class this can't help
# with anyway.
_active_account_ids: dict[str, "asyncio.Task"] = {}

_NOVNC_WS_URL = "ws://127.0.0.1:6080/websockify"

# live_login/session.py's own _LOGIN_TIMEOUT_SECONDS (600s / 10 min) is the
# longest a healthy session should ever legitimately run -- this sits well
# past that so it only ever fires on a genuine hang, never on ordinary
# completion (login success/failure) or an ordinary client disconnect,
# both of which unblock _proxy_and_watch well before this on their own.
_WATCHDOG_SECONDS = 900  # 15 minutes


def _is_locked(account_id: str) -> bool:
    """True if account_id has a task actually still running. A finished
    task left behind by a bug elsewhere (its own cleanup should always
    pop this entry, but this is the failsafe if that somehow doesn't
    happen) self-heals here rather than blocking forever."""
    task = _active_account_ids.get(account_id)
    if task is None:
        return False
    if task.done():
        logger.warning(
            "Found a finished-but-not-cleaned-up _active_account_ids entry for account %s -- "
            "treating as unlocked. Its own finally block should have removed this.",
            account_id,
        )
        _active_account_ids.pop(account_id, None)
        return False
    return True


async def process_request(connection: ServerConnection, request: Request) -> Response | None:
    """
    Intercepts plain HTTP requests BEFORE the websocket upgrade handshake --
    lets this one server also handle the disconnect endpoint as an ordinary
    POST, without a second process/port/Caddy route. Returning None here
    means "not handled, proceed with the normal websocket upgrade" (the
    /connect/{account_id} path everything else in this module already
    serves); returning a Response short-circuits that and answers directly.

    Disconnect is a one-shot request/response, not a streaming session, so
    it doesn't belong on the websocket path at all -- this is the same
    "authenticated one-off action against the droplet" shape the plan for
    the separate /control endpoint (agent start/stop) already established,
    reused here rather than inventing a third pattern.
    """
    parsed = urlparse(request.path)
    parts = [p for p in parsed.path.split("/") if p]

    if len(parts) != 3 or parts[0] != "connect" or parts[2] != "disconnect" or request.method != "POST":
        return None  # not our concern -- let the websocket path handle it (or 404 there)

    account_id = parts[1]
    token = _bearer_token(request.headers)
    if not token:
        return connection.respond(401, "Missing bearer token")

    try:
        claims = verify_connect_token(token, account_id, purpose="disconnect_account")
    except TokenInvalid as exc:
        logger.warning("Rejected disconnect attempt for account %s: %s", account_id, exc)
        return connection.respond(403, str(exc))

    tenant_id = claims["tenantId"]
    with repo.tenant_scope(tenant_id):
        account = repo.get_account(account_id)
    if not account:
        return connection.respond(404, "Account not found")

    if _is_locked(account_id):
        return connection.respond(409, "A connect/disconnect attempt is already in progress for this account.")

    # The actual disconnect: delete the saved session (browser_profiles/
    # {account_id}.json -- the same file save_and_mark_connected() writes,
    # same volume the scheduler's own SessionManager reads on every real
    # run) and reset the DB status. Deleting the file is what makes the
    # NEXT connect attempt (or the next scheduled run, if one somehow
    # fired) genuinely fresh rather than silently reusing a session the
    # dashboard now claims doesn't exist.
    storage_path = _storage_path(account_id)
    storage_path.unlink(missing_ok=True)

    with repo.tenant_scope(tenant_id):
        repo.update_account(account_id, {
            "login_status": "not_connected",
            "login_connected_at": None,
            "login_connecting_at": None,
            "login_error": None,
            "verified_proxy_ip": None,
            # Also invalidates any Nexaris Connect extension that
            # remembered this account (import_server.py's /reconnect
            # route checks this exact field) -- a disconnected account
            # should never be silently reconnectable by an old extension
            # install without going through a fresh dashboard code again.
            "extension_reconnect_token": None,
        })

    logger.info("Disconnected account %s (tenant %s) -- session file removed", account_id, tenant_id)
    response = connection.respond(200, json.dumps({"ok": True}))
    response.headers["Content-Type"] = "application/json"
    return response


def _bearer_token(headers: Headers) -> str | None:
    auth = headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return auth[len("Bearer "):].strip() or None


# WebSocket close reasons are capped at 123 bytes (RFC 6455 -- the reason
# shares a 125-byte control frame with the 2-byte status code). LIVE-CONFIRMED
# 2026-08-31: closing with the raw str(exc) of a Playwright timeout (its
# multi-line call log easily runs past that) doesn't just get silently
# truncated -- this library raises `ProtocolError: control frame too long`
# from INSIDE the close() call itself, which means the except block calling
# it never finishes and the client's socket is left to time out on its own
# with zero explanation, instead of getting the close frame that was meant to
# explain what went wrong. The real reason is still always written to
# OutreachAccount.loginError first (see the two call sites below) -- this is
# only what fits on the wire; the frontend's polling reads the full message.
def _close_reason(text: str) -> str:
    encoded = text.encode("utf-8")[:123]
    return encoded.decode("utf-8", errors="ignore")


async def handler(websocket: ServerConnection) -> None:
    path = websocket.request.path
    parsed = urlparse(path)
    parts = [p for p in parsed.path.split("/") if p]

    if len(parts) != 2 or parts[0] != "connect":
        await websocket.close(4404, "Not found -- expected /connect/{account_id}")
        return
    account_id = parts[1]

    token = parse_qs(parsed.query).get("token", [None])[0]
    if not token:
        await websocket.close(4401, "Missing token")
        return

    try:
        claims = verify_connect_token(token, account_id)
    except TokenInvalid as exc:
        logger.warning("Rejected connect attempt for account %s: %s", account_id, exc)
        await websocket.close(4403, _close_reason(str(exc)))
        return

    if _is_locked(account_id):
        await websocket.close(4409, "A connection attempt is already in progress for this account.")
        return

    tenant_id = claims["tenantId"]
    with repo.tenant_scope(tenant_id):
        account = repo.get_account(account_id)
    if not account:
        await websocket.close(4404, "Account not found.")
        return
    if account["platform"] not in ("linkedin", "instagram"):
        await websocket.close(4400, "Connect account only applies to LinkedIn/Instagram.")
        return

    current_task = asyncio.current_task()
    assert current_task is not None  # handler() only ever runs as a task, never awaited bare
    _active_account_ids[account_id] = current_task
    session_handle = None
    try:
        logger.info("Starting live login session: account=%s tenant=%s platform=%s",
                    account_id, tenant_id, account["platform"])
        try:
            session_handle = await start_login_session(account, account["platform"])
        except LiveLoginError as exc:
            with repo.tenant_scope(tenant_id):
                repo.update_account(account_id, {
                    "login_status": "failed",
                    "login_connecting_at": None,
                    "login_error": str(exc),
                })
            await websocket.close(4500, _close_reason(str(exc)))
            return

        try:
            # LIVE-CONFIRMED 2026-08-31: a real session left an account
            # stuck on loginStatus=="connecting" indefinitely -- the client
            # disconnected (logged as "connection closed") but this
            # coroutine never got past _proxy_and_watch, so the finally
            # block below never ran either: Chromium was still alive on
            # the droplet, unkilled, an hour later, and the dashboard had
            # no way out short of a manual DB fix. Root cause of the hang
            # itself wasn't pinned down (something inside the relay/watch
            # loop below didn't unblock the way the FIRST_COMPLETED logic
            # assumes it always will) -- this bounds it from the outside
            # instead: whatever happens inside, this coroutine is
            # guaranteed to come back within WATCHDOG_SECONDS, so the
            # finally block's cleanup (kill Chromium, clear _active_account_ids)
            # always eventually runs and the account is never stuck longer
            # than this, regardless of what actually hung.
            await asyncio.wait_for(
                _proxy_and_watch(websocket, session_handle, account_id, tenant_id),
                timeout=_WATCHDOG_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.error("Watchdog fired for account %s -- forcing session teardown", account_id)
            with repo.tenant_scope(tenant_id):
                repo.update_account(account_id, {
                    "login_status": "failed",
                    "login_connecting_at": None,
                    "login_error": "Session didn't end cleanly and was force-stopped. Please try connecting again.",
                })
    finally:
        _active_account_ids.pop(account_id, None)
        if session_handle is not None:
            try:
                await asyncio.wait_for(session_handle.close(), timeout=30)
            except Exception:
                logger.exception("session_handle.close() itself failed/hung for account %s", account_id)


async def _proxy_and_watch(websocket: ServerConnection, session_handle, account_id: str, tenant_id: str) -> None:
    """
    Bridges the client's websocket to the local websockify/noVNC endpoint
    (raw byte relay, both directions) for as long as the client stays
    connected. Runs the login-detection watcher alongside it as an
    independent task -- it writes the outcome (connected/failed) straight
    to the database and never touches this websocket at all, since noVNC's
    RFB client on the other end expects pure VNC protocol, nothing mixed
    in. Whichever ends first (client disconnects, or login detected/timed
    out) tears the whole thing down; session_handle.close() in the caller's
    finally block always runs regardless of which side ended first.
    """
    async with ws_connect(_NOVNC_WS_URL, subprotocols=["binary"]) as novnc_ws:
        async def client_to_novnc():
            async for message in websocket:
                await novnc_ws.send(message)

        async def novnc_to_client():
            async for message in novnc_ws:
                await websocket.send(message)

        relay_task = asyncio.gather(client_to_novnc(), novnc_to_client(), return_exceptions=True)
        login_task = asyncio.create_task(
            _watch_login_and_persist(session_handle, account_id, tenant_id)
        )

        done, pending = await asyncio.wait(
            {relay_task, login_task}, return_when=asyncio.FIRST_COMPLETED
        )

        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        if relay_task in done and login_task in pending:
            # The client disconnected before login was ever detected/timed
            # out -- login_task's own DB write never happened, so this is
            # the only place that outcome gets persisted.
            with repo.tenant_scope(tenant_id):
                repo.update_account(account_id, {
                    "login_status": "failed",
                    "login_connecting_at": None,
                    "login_error": "Connection closed before login completed.",
                })


async def _watch_login_and_persist(session_handle, account_id: str, tenant_id: str) -> None:
    """Polls for a successful login and persists the terminal outcome
    (connected + storage_state saved, or failed + reason) directly to the
    database -- this task's result never needs to reach the client socket,
    since the frontend polls OutreachAccount.loginStatus independently."""
    login_ok = await session_handle.wait_for_login()
    if login_ok:
        await session_handle.save_and_mark_connected()
    else:
        with repo.tenant_scope(tenant_id):
            repo.update_account(account_id, {
                "login_status": "failed",
                "login_connecting_at": None,
                "login_error": session_handle.timeout_reason(),
            })


async def main() -> None:
    if not config.AUTH_SECRET:
        raise RuntimeError("AUTH_SECRET must be set (agent/.env) before starting the live login server.")

    # Runs the Nexaris Connect extension's session-import HTTP server in a
    # background thread of this same process -- see import_server.py's own
    # docstring for why it's here (shares this process's filesystem access
    # to browser_profiles/) rather than a separate container/systemd unit.
    # HTTPServer.serve_forever() is blocking/synchronous, so it can't share
    # this module's asyncio event loop -- a plain thread, not a task.
    import_server.start_in_background_thread()

    # Bind to 0.0.0.0 INSIDE the container, not 127.0.0.1 -- Docker's port
    # forwarding (docker-proxy) delivers host-side connections to the
    # container over its internal network interface, not its loopback, so a
    # server bound only to the container's own 127.0.0.1 silently refuses
    # every forwarded connection (LIVE-VERIFIED: this exact bug reproduced
    # against the real droplet -- curl from the host got an immediate TCP
    # reset with no log line reaching this process at all, while connecting
    # from inside the container's own namespace worked fine). External
    # reachability is still restricted correctly at the host level by
    # `docker run -p 127.0.0.1:8765:8765` in DEPLOY.md, which is what
    # actually keeps this off the public internet -- not this bind address.
    async with serve(handler, "0.0.0.0", config.LIVE_LOGIN_PORT, process_request=process_request):
        logger.info("live_login server listening on 0.0.0.0:%s", config.LIVE_LOGIN_PORT)
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
