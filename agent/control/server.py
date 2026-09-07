"""
Plain HTTP control endpoint for starting/stopping/checking the scheduler
container (`nexaris-agent`) from the Admin dashboard. Runs as a host-level
process (systemd unit, NOT a Docker container -- see DEPLOY.md's "Agent
control service" section for why: this process needs to run `docker`
commands against the host's Docker daemon, and giving any internet-facing
container access to the Docker socket is a real privilege-escalation risk
this repo deliberately avoids).

Uses Python's stdlib http.server rather than adding a web framework
dependency -- this is exactly one route (`POST /control`) with a small JSON
body in and out, not enough surface to justify FastAPI/Flask.

Run: python -m agent.control.server
"""

from __future__ import annotations

import json
import logging
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent import config
from agent.control.auth import verify_control_token, TokenInvalid

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_CONTAINER_NAME = "nexaris-agent"
_ALLOWED_ACTIONS = {"start", "stop", "status", "send_reply"}
_SUBPROCESS_TIMEOUT_SECONDS = 15


def _docker_status() -> str:
    """Returns "running", "stopped", or "not_found" -- never raises for a
    container that simply doesn't exist yet (a real, expected state before
    the very first deploy), only for a genuine docker/host-level failure.

    LIVE-VERIFIED 2026-08-26: `docker inspect nexaris-agent` alone is NOT
    enough to distinguish "no container by this name" from "container never
    run, but an IMAGE with this same tag exists" -- an image tagged
    `nexaris-agent:latest` (built by DEPLOY.md's build step) but never
    `docker run`, matches `docker inspect nexaris-agent` too, returning
    image metadata with no `.State` field at all, which crashed the
    `--format` template lookup below with a confusing parse error rather
    than a clean "not found". Filtering by `docker ps -a --filter
    name=^/nexaris-agent$` first (containers only, exact name, matching
    Docker's own anchoring convention for this filter) avoids that
    ambiguity entirely -- confirmed against the real droplet, where the
    image exists but the container has never been started.
    """
    list_result = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^/{_CONTAINER_NAME}$", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    if list_result.returncode != 0:
        raise RuntimeError(f"docker ps failed: {list_result.stderr.strip()}")
    if _CONTAINER_NAME not in list_result.stdout.split():
        return "not_found"

    result = subprocess.run(
        ["docker", "inspect", _CONTAINER_NAME, "--format", "{{.State.Status}}"],
        capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker inspect failed: {result.stderr.strip()}")
    status = result.stdout.strip()
    return "running" if status == "running" else "stopped"


_ENV_FILE = "/etc/nexaris-agent.env"
_VOLUME_NAME = "nexaris-browser-profiles"


def _docker_action(action: str) -> str:
    """action is "start" or "stop" -- already validated against
    _ALLOWED_ACTIONS by the caller before this is ever reached, so this
    never shells out with anything other than a fixed, known-safe argv.

    "start" on a container that has NEVER been created (image built, but
    `docker run` never done -- confirmed this is the real current state of
    the droplet as of 2026-08-26) can't use `docker start`, which only
    works on an already-created, stopped container -- it must `docker run`
    instead, using the exact same flags DEPLOY.md documents for the manual
    first-time deploy, so this button and a manual deploy produce an
    identical container. "stop" always just stops (never removes) the
    container, matching `--restart unless-stopped`'s own semantics -- a
    stopped container stays stopped until this endpoint (or a human) starts
    it again, it does not auto-restart on its own."""
    status = _docker_status()

    if action == "start":
        if status == "not_found":
            result = subprocess.run(
                [
                    "docker", "run", "-d",
                    "--name", _CONTAINER_NAME,
                    "--restart", "unless-stopped",
                    "--env-file", _ENV_FILE,
                    "-v", f"{_VOLUME_NAME}:/app/agent/browser_profiles",
                    _CONTAINER_NAME,
                ],
                capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
            )
        else:
            result = subprocess.run(
                ["docker", "start", _CONTAINER_NAME],
                capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
            )
    else:  # "stop"
        if status == "not_found":
            return "not_found"
        result = subprocess.run(
            ["docker", "stop", _CONTAINER_NAME],
            capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )

    if result.returncode != 0:
        raise RuntimeError(f"docker {action} failed: {result.stderr.strip()}")
    return _docker_status()


def _send_reply(tenant_id: str, lead_id: str, message_id: str) -> dict:
    """
    Real gap fixed 2026-09-06: a tenant-written reply from "Reply Here" on
    Instagram/LinkedIn sat at send_status "pending" forever unless the
    Python scheduler's fast reply-send poll happened to be running --
    which it never has been in production (see DEPLOY.md's "do not start
    the scheduler unsupervised" gate). Email replies bypass this by calling
    Resend directly from the Next.js server action (sendIfEmailChannel),
    but Instagram/LinkedIn sends need the actual browser-automation
    process, which only exists here on the droplet -- so this endpoint is
    that same "send it right now, don't wait for a poll" bridge, for the
    two channels email's approach can't reach directly.

    Imports are deliberately local to this function, not at module level --
    this control server also handles the lightweight start/stop/status
    actions on every request, and importing Playwright/the full sending
    stack unconditionally would slow down and fatten every one of those
    unrelated calls for a code path most requests never take.
    """
    from agent.db import repositories as repo
    from agent.sending import instagram_send, linkedin_send

    # LIVE-CONFIRMED 2026-09-06: send_reply() (both instagram_send's and
    # linkedin_send's) makes its OWN internal repo calls (get_lead,
    # get_account, update_message, ...) using tenant_scope's ambient
    # context, not an explicit tenant_id argument -- scoping only the
    # initial messages_for_lead() fetch and closing the `with` block before
    # calling send_reply() left those internal calls with no active scope
    # at all, hit live as "NoTenantInScope: No tenant_id was passed and no
    # tenant_scope(...) is active." The whole dispatch, not just the
    # lookup, needs to run inside one tenant_scope block.
    with repo.tenant_scope(tenant_id):
        messages = repo.messages_for_lead(lead_id, tenant_id=tenant_id)
        message = next((m for m in messages if m["id"] == message_id), None)
        if not message:
            raise RuntimeError(f"Message {message_id} not found for lead {lead_id}.")

        channel = message.get("channel")
        if channel == "instagram":
            instagram_send.send_reply(message)
        elif channel == "linkedin":
            linkedin_send.send_reply(message)
        else:
            raise RuntimeError(f"send_reply is only for instagram/linkedin, got channel={channel!r}.")

    return {"sent": True, "channel": channel}


class ControlHandler(BaseHTTPRequestHandler):
    # LIVE-VERIFIED 2026-08-26: BaseHTTPRequestHandler defaults to
    # "HTTP/1.0" -- worked fine calling the droplet directly (curl doesn't
    # care), but broke once Caddy reverse-proxied it: responses came back
    # with the correct status/headers but an EMPTY body every time (curl -v
    # showed Content-Length: 0 despite the handler writing real JSON bytes).
    # Caddy's own upstream connections use HTTP/1.1 by default, and an
    # HTTP/1.0 response's Content-Length + connection-handling semantics
    # don't line up cleanly with that -- explicitly declaring HTTP/1.1 here
    # fixed it, confirmed via curl through https://agent.nxrs.tech/control
    # returning the real JSON body instead of an empty one.
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
        if self.path != "/control":
            self._send_json(404, {"error": "Not found."})
            return

        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_json(401, {"error": "Missing bearer token."})
            return
        token = auth_header[len("Bearer "):]

        try:
            verify_control_token(token)
        except TokenInvalid as exc:
            logger.warning("Rejected agent-control request: %s", exc)
            self._send_json(401, {"error": str(exc)})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(content_length) or b"{}")
        except (ValueError, TypeError):
            self._send_json(400, {"error": "Invalid JSON body."})
            return

        action = body.get("action")
        if action not in _ALLOWED_ACTIONS:
            self._send_json(400, {"error": f"action must be one of {sorted(_ALLOWED_ACTIONS)}."})
            return

        if action == "send_reply":
            tenant_id = body.get("tenantId")
            lead_id = body.get("leadId")
            message_id = body.get("messageId")
            if not tenant_id or not lead_id or not message_id:
                self._send_json(400, {"error": "send_reply requires tenantId, leadId, and messageId."})
                return
            try:
                logger.info("Agent control: send_reply (message=%s)", message_id)
                result = _send_reply(tenant_id, lead_id, message_id)
            except Exception as exc:  # noqa: BLE001 -- e.g. no message button, selector mismatch, browser crash
                logger.exception("send_reply failed")
                self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            self._send_json(200, result)
            return

        try:
            if action == "status":
                status = _docker_status()
            else:
                logger.info("Agent control: %s (%s)", action, _CONTAINER_NAME)
                status = _docker_action(action)
        except Exception as exc:  # noqa: BLE001 -- e.g. docker CLI missing/unreachable, timeout, bad state
            logger.exception("Docker control action failed")
            self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return

        self._send_json(200, {"status": status})


def main() -> None:
    if not config.AUTH_SECRET:
        raise RuntimeError("AUTH_SECRET must be set (agent/.env) before starting the control server.")
    # ThreadingHTTPServer, not HTTPServer -- send_reply's real browser send
    # can take 10-30+ seconds; the plain single-threaded HTTPServer would
    # block every other request (including a status/start/stop check) for
    # that whole window, which defeats the point of this being a
    # lightweight always-available control endpoint.
    server = ThreadingHTTPServer(("127.0.0.1", config.AGENT_CONTROL_PORT), ControlHandler)
    logger.info("agent control server listening on 127.0.0.1:%s", config.AGENT_CONTROL_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
