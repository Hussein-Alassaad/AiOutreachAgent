"""
Supabase connection for the agent.

One shared client for the whole process. Created lazily on first use so that importing
this module never fails when credentials aren't set yet (Phase 0 runs without them).
"""

from supabase import Client, create_client

from agent import config

# Module-level cache — create_client opens a connection pool, so we make exactly one.
_client: Client | None = None


class SupabaseNotConfigured(RuntimeError):
    """Raised when the agent needs the database but credentials are missing."""


def get_client() -> Client:
    """
    Return the shared Supabase client, creating it on first call.

    Uses the service role key: the agent runs on a trusted server and needs full
    read/write access across all tables, bypassing Row Level Security.
    """
    global _client

    if _client is None:
        missing = config.missing_required(["SUPABASE_URL", "SUPABASE_SERVICE_KEY"])
        if missing:
            raise SupabaseNotConfigured(
                f"Missing {', '.join(missing)} in agent/.env — "
                "copy .env.example and fill in your Supabase project details."
            )
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)

    return _client


def is_configured() -> bool:
    """True when Supabase credentials are present. Lets callers degrade gracefully."""
    return not config.missing_required(["SUPABASE_URL", "SUPABASE_SERVICE_KEY"])


def check_connection() -> tuple[bool, str]:
    """
    Verify the agent can actually reach the database.

    Returns (ok, message) rather than raising, because this is used by the Phase 0
    health check where a failure is informational, not fatal.
    """
    if not is_configured():
        return False, "Supabase credentials not set in agent/.env"

    try:
        # settings is a single-row table that exists from the schema onward. Selecting
        # one row is the cheapest possible proof that the connection and keys work.
        get_client().table("settings").select("id").limit(1).execute()
        return True, "Connected to Supabase"
    except Exception as exc:  # noqa: BLE001 — surface any connection problem verbatim
        return False, f"Supabase connection failed: {exc}"
