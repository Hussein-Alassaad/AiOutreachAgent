"""
Verifies the short-lived agent-control token minted by the Next.js app's
agentControlAction() (src/lib/actions/agent-control.ts) or, for the
send_reply action specifically, sendReplyViaAgent() (outreach-replies.ts).

Same deliberately narrow posture as live_login/auth.py's
verify_connect_token(): signature + claims verification only, same shared
AUTH_SECRET/HS256 scheme, no session/DB lookup on this side. start/stop/
status are genuinely platform-wide (not tenant data actions), so those
carry no tenantId claim -- the worst-case misuse of a leaked token there
(replayed within its ~60s window) is starting or stopping the scheduler
process, which is exactly why the Next.js side gates minting those tokens
behind a PLATFORM-only "agent-control" permission (see agentControlAction's
own guard() call).

send_reply IS a tenant data action (it sends a specific tenant's message),
so found missing in the 2026-09-09 platform review: its token previously
carried no tenantId claim at all, meaning this server had no way to verify
the tenantId in the request body actually matched what the token was
minted for -- any valid token plus an arbitrary body tenantId would be
accepted. verify_control_token now takes an optional expected_tenant_id and
enforces it when the token itself carries a tenantId claim.
"""

from __future__ import annotations

import jwt

from agent import config


class TokenInvalid(Exception):
    """Raised for any reason an agent-control token should be rejected --
    bad signature, expired, or wrong purpose. Callers respond with a 401
    JSON error rather than distinguishing the exact cause to the client."""


def verify_control_token(token: str, expected_tenant_id: str | None = None) -> dict:
    """
    Returns the decoded claims dict (purpose, action, adminUserId, iat, exp,
    and tenantId when present) if the token is valid. Raises TokenInvalid
    otherwise.

    expected_tenant_id: pass the tenantId the caller claims in its request
    body (send_reply's case) so it's checked against the token's own signed
    tenantId claim, not just accepted at face value. If the token carries no
    tenantId claim at all (the platform-wide start/stop/status case), no
    comparison is made -- callers that need tenant scoping are responsible
    for minting a token that actually carries the claim in the first place.
    """
    if not config.AUTH_SECRET:
        raise TokenInvalid("AUTH_SECRET is not configured on this server.")

    try:
        claims = jwt.decode(token, config.AUTH_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise TokenInvalid(f"Token verification failed: {exc}") from exc

    if claims.get("purpose") != "agent_control":
        raise TokenInvalid("Token is not an agent-control token.")

    if expected_tenant_id is not None:
        token_tenant_id = claims.get("tenantId")
        if not token_tenant_id:
            raise TokenInvalid("Token is missing the required tenantId claim.")
        if token_tenant_id != expected_tenant_id:
            raise TokenInvalid("Token's tenantId does not match the requested tenant.")

    return claims
