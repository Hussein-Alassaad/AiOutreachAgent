"""
Postgres connection pool for the agent -- talks to the main Next.js SaaS's
own multi-tenant database instead of the old standalone Supabase project
(see db/client.py, now superseded, kept only for historical reference).

Library choice: psycopg2 (via psycopg2-binary), not psycopg3. Reasoning:
  - psycopg2.pool.ThreadedConnectionPool is a small, synchronous, thread-safe
    pool with a getconn()/putconn() API that maps directly onto this agent's
    existing style (sync Playwright, sync APScheduler jobs, no asyncio
    anywhere in the codebase) -- psycopg3's headline advantage (native async)
    buys nothing here.
  - psycopg2-binary was ALREADY installed in agent/venv (confirmed before
    writing this file), so this is zero new build/wheel risk on whatever
    server this eventually deploys to.
  - Supabase's transaction-mode pooler (port 6543, pgbouncer=true) holds
    server-side connections only for the duration of a single transaction --
    it does not support session-level features (prepared statements,
    SET/session GUCs) that some psycopg3 pooling conveniences lean on, so a
    plain, explicit pool with connections returned promptly after each unit
    of work is the safer fit regardless of driver.

A single module-level ThreadedConnectionPool is created lazily on first use
(same lazy pattern db/client.py used for its Supabase client), sized small
(min 1, max 10) since this process is a scheduler running bounded per-
account/per-tenant loops, not a web server fielding concurrent requests.

Every caller MUST return connections via `put_connection()` (or use the
`get_cursor()` context manager below, which does this automatically) --
holding a connection open indefinitely starves the pool and, worse, holds a
pgbouncer transaction-mode slot that Supabase expects released quickly.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg2
import psycopg2.extras
import psycopg2.pool

from agent import config

_pool: psycopg2.pool.ThreadedConnectionPool | None = None

_MIN_CONNECTIONS = 1
_MAX_CONNECTIONS = 10

# Query params that are meaningful to Prisma's own connection string handling
# (see the main Next.js app's DATABASE_URL) but that plain libpq/psycopg2
# doesn't understand and will reject outright with "invalid dsn: invalid URI
# query parameter" -- confirmed live against the real Supabase pooler URL,
# which includes `pgbouncer=true`. Stripped before connecting; psycopg2 talks
# to the same pgbouncer transaction-mode pooler either way; the flag only
# ever mattered to Prisma's own client-side prepared-statement handling.
_PRISMA_ONLY_QUERY_PARAMS = {"pgbouncer", "connection_limit", "pool_timeout", "schema"}


def _sanitize_dsn(raw_dsn: str) -> str:
    """Strip Prisma-only query params from DATABASE_URL so psycopg2 accepts
    it. Both apps deliberately share the exact same DATABASE_URL value (see
    .env.example) -- this only affects how the connection string is parsed
    on this side, not what either app writes to .env."""
    parts = urlsplit(raw_dsn)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k not in _PRISMA_ONLY_QUERY_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


class DatabaseNotConfigured(RuntimeError):
    """Raised when the agent needs the database but DATABASE_URL is missing."""


def is_configured() -> bool:
    """True when DATABASE_URL is present. Lets callers degrade gracefully."""
    return not config.missing_required(["DATABASE_URL"])


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Return the shared connection pool, creating it on first call."""
    global _pool

    if _pool is None:
        if not is_configured():
            raise DatabaseNotConfigured(
                "Missing DATABASE_URL in agent/.env -- copy .env.example and set it to "
                "the SAME Postgres connection string the main Next.js app uses."
            )
        _pool = psycopg2.pool.ThreadedConnectionPool(
            _MIN_CONNECTIONS,
            _MAX_CONNECTIONS,
            dsn=_sanitize_dsn(config.DATABASE_URL),
        )

    return _pool


def get_connection():
    """Check out one connection from the pool. Caller MUST call
    put_connection() when done -- prefer get_cursor() below instead of
    calling this directly."""
    return get_pool().getconn()


def put_connection(conn) -> None:
    """Return a connection to the pool. Safe to call even if `conn` came
    from a pool that's since been recreated (shouldn't happen in practice,
    since the pool is a single module-level singleton)."""
    if _pool is not None:
        _pool.putconn(conn)


@contextmanager
def get_cursor(commit: bool = True) -> Iterator[Any]:
    """
    The main entry point every repositories.py function should use:

        with get_cursor() as cur:
            cur.execute("SELECT * FROM outreach_leads WHERE tenant_id = %s", (tenant_id,))
            rows = cur.fetchall()

    Yields a RealDictCursor (rows come back as dicts, matching the old
    Supabase client's `res.data` shape so repositories.py's callers don't
    need to change how they read a row). Commits on clean exit (unless
    commit=False, for pure reads), rolls back on exception, and always
    returns the connection to the pool in a `finally` -- this is what keeps
    the pgbouncer transaction-pooler slot held for the shortest possible
    time, which is the whole reason a real pool exists instead of one bare
    long-lived connection.

    RLS 2026-08-28: as the FIRST statement on the checked-out connection,
    before the caller's own query runs, sets the Postgres session variable
    `app.current_tenant_id` to repositories.py's ambient tenant_scope(...)
    value via `SELECT set_config(..., true)` -- the `true` (is_local) makes
    this SET LOCAL semantics, scoped to THIS transaction only, so it can
    never leak onto a different tenant's query even though this connection
    came from a shared pool and gets reused by a later, different caller
    (mirrors the Node app's identical withTenant()/withPlatformAccess()
    helpers in src/lib/db.ts -- same mechanism, same reasoning, see that
    file's own comments). Every table this matters for now has
    `FORCE ROW LEVEL SECURITY` (see prisma/migrations_manual/enable_rls.sql)
    and this pool now connects as app_user (NOBYPASSRLS), not postgres --
    without this, every query here would silently return zero rows.

    A repositories.py call made with NO tenant_scope(...) active (and no
    explicit tenant_id -- see _resolve_tenant()'s own NoTenantInScope) sets
    app.current_tenant_id to an empty string, which matches no real tenant
    row -- RLS correctly returns nothing rather than everything, the same
    fail-closed behavior _resolve_tenant() already enforces by raising
    before ever reaching here in that case.

    A platform_scope(...) block (repositories.py) additionally sets
    app.is_platform_admin='true' -- the Python-side equivalent of Node's
    withPlatformAccess() -- for the handful of genuinely cross-tenant reads
    (right now, just list_active_tenant_ids()) that have no single tenant
    to scope to. See that function's own docstring for why it's needed at
    all and why it's used sparingly.

    Deferred import (not at module top) to avoid a circular import --
    repositories.py imports get_cursor from this module, so this module
    cannot import repositories.py at load time; resolving it here, at call
    time, is safe since both modules are always fully loaded by then.
    """
    from agent.db.repositories import current_tenant_id, is_platform_scope

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT set_config('app.current_tenant_id', %s, true)", (current_tenant_id() or "",))
            if is_platform_scope():
                cur.execute("SELECT set_config('app.is_platform_admin', 'true', true)")
            yield cur
        if commit:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def check_connection() -> tuple[bool, str]:
    """
    Verify the agent can actually reach the database. Returns (ok, message)
    rather than raising, same contract as the old client.py's
    check_connection() -- used by health checks where a failure is
    informational, not fatal.
    """
    if not is_configured():
        return False, "DATABASE_URL not set in agent/.env"

    try:
        with get_cursor(commit=False) as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True, "Connected to Postgres"
    except Exception as exc:  # noqa: BLE001 -- surface any connection problem verbatim
        return False, f"Postgres connection failed: {exc}"


def close_pool() -> None:
    """Close every connection in the pool. Call at process shutdown (not
    required for a short-lived script like the smoke test, but good
    hygiene for the long-running scheduler process)."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
