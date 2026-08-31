"""
Verifies the short-lived connect-account token minted by the Next.js app's
startConnectAccountAction() (src/lib/actions/outreach-live-login.ts).

This is deliberately narrow: signature + claims verification only, using the
SAME AUTH_SECRET/HS256 scheme src/lib/auth.ts already uses for real session
cookies (see that file's SignJWT/jwtVerify calls). It is NOT a session
lookup -- there is no UserSession row check, no revocation list, nothing
that would require this droplet to have Prisma/DB access into the User
table. The token is scoped to exactly one account, expires in ~120 seconds,
and its worst-case misuse (a leaked token, replayed within its short window)
only opens a live login session for one specific LinkedIn/Instagram account
that already needs a proxy configured -- a small, intentional blast radius,
not a full auth system reimplementation.
"""

from __future__ import annotations

import jwt

from agent import config


class TokenInvalid(Exception):
    """Raised for any reason a connect-account token should be rejected --
    bad signature, expired, wrong purpose, or claims mismatched against the
    account_id in the URL. Callers close the websocket with a 4401/4403-style
    reason rather than distinguishing the exact cause to the client."""


def verify_connect_token(token: str, account_id: str, purpose: str = "connect_account") -> dict:
    """
    Returns the decoded claims dict (accountId, tenantId, purpose, iat, exp)
    if the token is valid AND its accountId claim matches the account_id
    from the websocket/request path. Raises TokenInvalid otherwise.

    `purpose` defaults to "connect_account" (the original, only caller for
    a long time) but also accepts "disconnect_account" (see
    live_login/server.py's disconnect handler) -- checking it explicitly
    per-call means a token minted for one purpose can never be replayed for
    the other, even though both share this same verification path and the
    same AUTH_SECRET.
    """
    if not config.AUTH_SECRET:
        raise TokenInvalid("AUTH_SECRET is not configured on this server.")

    try:
        claims = jwt.decode(token, config.AUTH_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise TokenInvalid(f"Token verification failed: {exc}") from exc

    if claims.get("purpose") != purpose:
        raise TokenInvalid(f"Token is not a {purpose} token.")
    if claims.get("accountId") != account_id:
        raise TokenInvalid("Token does not match the requested account.")
    if not claims.get("tenantId"):
        raise TokenInvalid("Token is missing tenantId.")

    return claims
