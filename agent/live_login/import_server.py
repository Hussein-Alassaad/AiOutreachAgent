"""
Plain HTTP endpoint that lets the Nexaris Connect Chrome extension hand off
an already-logged-in LinkedIn/Instagram session, as an alternative to the
VNC-based Connect Account flow in this same package (server.py/session.py).

Both flows converge on the exact same artifact: a Playwright storage_state
JSON file at browser_profiles/{account_id}.json (see core/session.py's
_storage_path -- the single source of truth the scheduler's own
SessionManager reads on every real run) plus the same OutreachAccount
loginStatus="connected" write live_login/session.py's
save_and_mark_connected() already does. This module is a second FRONT DOOR
onto that same storage format, not a new storage mechanism -- nothing
downstream (scheduler, account_pool, send flows) needs to know or care
which path produced the file.

Runs as its own stdlib http.server (same reasoning as agent/control/
server.py: one route, small JSON body in/out, not enough surface to
justify a web framework), in its own thread inside the SAME container
process as live_login/server.py's websocket server -- not a separate
container/systemd unit -- so it shares that process's filesystem access to
browser_profiles/ and its already-configured DB connection without a third
deployable unit. Started from server.py's main(); see that module.

The extension itself never talks to this port directly with raw cookies in
the clear over an unauthenticated channel -- src/app/api/extension/
import-session/route.ts (Next.js) is the extension's actual target,
verifies the tenant's short-lived code, converts the extension's raw
chrome.cookies.getAll() array into this storage_state shape, and is the
one thing that calls THIS endpoint, server-to-server, over HTTPS via Caddy
-- same trust boundary /connect and /control already cross today.

Run: imported and started by server.py's main(), not run standalone.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from agent import config
from agent.core.session import _storage_path
from agent.db import repositories as repo
from agent.live_login.auth import verify_connect_token, TokenInvalid

logger = logging.getLogger(__name__)

# Cookies alone are sufficient for LinkedIn/Instagram auth (both are
# HttpOnly session-cookie-based, e.g. LinkedIn's li_at -- VERIFIED 2026-08-31:
# no code path in this codebase's own LOGGED_IN_CHECK/LOGGED_IN_CHECK_ASYNC
# ever reads localStorage, and Chrome's cookies API can't read localStorage
# without an injected content script anyway). storage_state's "origins"
# (localStorage) field is always written as [] here -- deliberately, not an
# oversight -- rather than adding a content-script capture path that
# nothing actually needs.
_MAX_BODY_BYTES = 256 * 1024  # generous headroom over a realistic ~5KB cookie payload


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class ImportSessionHandler(BaseHTTPRequestHandler):
    # See agent/control/server.py's own comment on this exact line --
    # BaseHTTPRequestHandler defaults to HTTP/1.0, which breaks once Caddy
    # reverse-proxies it (empty response bodies). Not directly internet-
    # facing here (only Next.js's server calls this, never a browser), but
    # matching the same fix defensively rather than re-discovering it.
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 -- stdlib signature
        logger.info("%s - %s", self.address_string(), format % args)

    def _send_json(self, status_code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802 -- stdlib method name
        # Path shape: /session/{account_id}/import (dashboard-code flow,
        # first-ever connect for this account) or
        # /session/{account_id}/reconnect (extension-remembered flow, every
        # later reconnect -- see docstring below on the auth difference).
        parts = [p for p in self.path.split("/") if p]
        if len(parts) != 3 or parts[0] != "session" or parts[2] not in ("import", "reconnect"):
            self._send_json(404, {"error": "Not found -- expected /session/{account_id}/import or /reconnect"})
            return
        account_id = parts[1]
        is_reconnect = parts[2] == "reconnect"

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0 or content_length > _MAX_BODY_BYTES:
            self._send_json(400, {"error": "Missing or oversized request body."})
            return
        try:
            body = json.loads(self.rfile.read(content_length))
        except (ValueError, TypeError):
            self._send_json(400, {"error": "Invalid JSON body."})
            return

        cookies = body.get("cookies")
        if not isinstance(cookies, list) or not cookies:
            self._send_json(400, {"error": "Body must include a non-empty 'cookies' array."})
            return

        # Auth differs by mode:
        #   /import  -- Authorization: Bearer <short-lived JWT>, minted by
        #     Next.js from a dashboard-generated code (purpose=
        #     "import_session", verify_connect_token()). Proves a real
        #     tenant session clicked "Connect via extension" just now.
        #   /reconnect -- {"reconnectToken": "..."} in the JSON body, a
        #     long-lived opaque value stored on OutreachAccount by THIS
        #     handler the first time /import succeeds, and saved into the
        #     extension's own chrome.storage.local at that point. Checked
        #     by exact match against the DB, not a JWT -- there's no
        #     "tenant session" to verify a claim against on a reconnect
        #     click days/weeks later, only "does this extension hold the
        #     token this exact account issued it".
        if is_reconnect:
            reconnect_token = body.get("reconnectToken")
            if not reconnect_token or not isinstance(reconnect_token, str):
                self._send_json(401, {"error": "Missing reconnect token."})
                return
            account = repo.get_account_by_reconnect_token(account_id, reconnect_token)
            if not account:
                self._send_json(401, {
                    "error": "This extension's saved connection is no longer valid -- reconnect using a fresh code from the dashboard."
                })
                return
            tenant_id = account["tenant_id"]
        else:
            auth_header = self.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                self._send_json(401, {"error": "Missing bearer token."})
                return
            token = auth_header[len("Bearer "):]
            try:
                claims = verify_connect_token(token, account_id, purpose="import_session")
            except TokenInvalid as exc:
                logger.warning("Rejected session-import request for account %s: %s", account_id, exc)
                self._send_json(401, {"error": str(exc)})
                return
            tenant_id = claims["tenantId"]

            with repo.tenant_scope(tenant_id):
                account = repo.get_account(account_id)
            if not account:
                self._send_json(404, {"error": "Account not found."})
                return

        if account["platform"] not in ("linkedin", "instagram"):
            self._send_json(400, {"error": "Session import only applies to LinkedIn/Instagram accounts."})
            return

        # Playwright's storage_state shape: {"cookies": [...], "origins": [...]}.
        # "origins" (localStorage) is always [] -- see module docstring for why
        # that's not needed for either platform's auth.
        storage_state = {"cookies": cookies, "origins": []}

        storage_path = _storage_path(account_id)
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_text(json.dumps(storage_state), encoding="utf-8")

        update_fields = {
            "login_status": "connected",
            "login_connected_at": _utcnow_iso(),
            "login_connecting_at": None,
            "login_error": None,
        }
        response_body = {"ok": True}

        # Only the /import (code) flow issues a NEW reconnect token -- a
        # /reconnect call reuses the token it was just verified with
        # unchanged, so a successful reconnect doesn't invalidate itself.
        if not is_reconnect:
            new_token = secrets.token_urlsafe(32)
            update_fields["extension_reconnect_token"] = new_token
            response_body["reconnectToken"] = new_token

        with repo.tenant_scope(tenant_id):
            repo.update_account(account_id, update_fields)

        logger.info(
            "%s session for account %s (tenant %s) via extension -- %d cookies",
            "Reconnected" if is_reconnect else "Imported", account_id, tenant_id, len(cookies),
        )
        self._send_json(200, response_body)


def start_in_background_thread() -> threading.Thread:
    """Called from server.py's main() -- runs alongside the websocket
    server in the same process, own thread (HTTPServer.serve_forever()
    blocks, so this can't share the asyncio event loop the websocket
    server owns)."""
    # Bind to 0.0.0.0 INSIDE the container, not 127.0.0.1 -- see
    # server.py's main()'s own comment on this exact bug (LIVE-VERIFIED
    # there against the real droplet): Docker's port forwarding delivers
    # host-side connections to the container over its internal network
    # interface, not its loopback, so a server bound only to 127.0.0.1
    # inside the container silently refuses every forwarded connection.
    # External reachability is still correctly restricted at the host
    # level by `-p 127.0.0.1:8767:8767` in the `docker run` command, which
    # is what actually keeps this off the public internet -- not this
    # bind address.
    server = HTTPServer(("0.0.0.0", config.IMPORT_SESSION_PORT), ImportSessionHandler)
    thread = threading.Thread(target=server.serve_forever, name="import-session-server", daemon=True)
    thread.start()
    logger.info("session-import server listening on 0.0.0.0:%s", config.IMPORT_SESSION_PORT)
    return thread
