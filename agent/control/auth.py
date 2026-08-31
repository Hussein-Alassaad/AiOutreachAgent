"""
Verifies the short-lived agent-control token minted by the Next.js app's
agentControlAction() (src/lib/actions/agent-control.ts).

Same deliberately narrow posture as live_login/auth.py's
verify_connect_token(): signature + claims verification only, same shared
AUTH_SECRET/HS256 scheme, no session/DB lookup on this side. Unlike
live_login's token, this one is NOT scoped to a tenant or account -- it's a
platform-wide control action, so there's no accountId/tenantId claim to
match against a URL path. The worst-case misuse of a leaked token (replayed
within its ~60s window) is starting or stopping the scheduler process --
a real, meaningful action, which is exactly why the Next.js side gates
minting this token behind a PLATFORM-only "agent-control" permission before
it's ever issued (see agentControlAction's own guard() call).
"""

from __future__ import annotations

import jwt

from agent import config


class TokenInvalid(Exception):
    """Raised for any reason an agent-control token should be rejected --
    bad signature, expired, or wrong purpose. Callers respond with a 401
    JSON error rather than distinguishing the exact cause to the client."""


def verify_control_token(token: str) -> dict:
    """
    Returns the decoded claims dict (purpose, action, adminUserId, iat, exp)
    if the token is valid. Raises TokenInvalid otherwise.
    """
    if not config.AUTH_SECRET:
        raise TokenInvalid("AUTH_SECRET is not configured on this server.")

    try:
        claims = jwt.decode(token, config.AUTH_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise TokenInvalid(f"Token verification failed: {exc}") from exc

    if claims.get("purpose") != "agent_control":
        raise TokenInvalid("Token is not an agent-control token.")

    return claims
