"""
Postgres port checkpoint proof — verifies the agent can reach the main
Next.js SaaS's own multi-tenant Postgres database and that repositories.py's
tenant-scoped queries actually work, end to end, against real data.

PORTED 2026-08-20 from db/smoke_test.py's original Supabase-checking pattern
(read that file first for the shape this mirrors). Run this AFTER filling in
agent/.env's DATABASE_URL (same value as the main app's own .env):

    agent/venv/Scripts/python.exe -m agent.db.smoke_test_postgres

It checks, in order:
  1. Connection works (DATABASE_URL + network + the pgbouncer-param-stripping
     fix in db/postgres_client.py's _sanitize_dsn()).
  2. Finds a real tenant to test against -- NOT prisma/seed.ts's "vantage"
     demo tenant (the live database this actually ran against, 2026-08-20,
     has a single real tenant, "Zimmar"/zimmar, created through the real
     signup flow, not the seed script -- seed.ts apparently hasn't been run
     against this particular database). That tenant has an OutreachSettings
     row but ZERO OutreachAccount rows yet (nobody has added a LinkedIn/
     Instagram account through the real UI), so list_active_tenant_ids()
     correctly returns empty at rest -- this is verified as CORRECT
     behavior, not a failure, before the test creates its own throwaway
     account to exercise the rest of the path live.
  3. A full round-trip: insert a throwaway OutreachAccount (status=active,
     platform=linkedin) scoped to that tenant, confirm
     list_active_tenant_ids() now finds the tenant, confirm
     repo.list_accounts()/get_account() see it, insert a throwaway lead
     under that account (exercising JSON-as-TEXT read/write), confirm
     tenant-scoping actually blocks a cross-tenant read, then delete both
     rows. Wrapped in try/finally so this leaves the database exactly as it
     found it, even if an assertion above it fails.

Nothing here touches Playwright, LinkedIn, Instagram, or Twilio.
"""

from __future__ import annotations

from agent.db import repositories as repo
from agent.db.postgres_client import check_connection, close_pool, get_cursor

PASS = "[PASS]"
FAIL = "[FAIL]"


def _line(ok: bool, label: str, detail: str = "") -> bool:
    """Print one result line and return ok so callers can accumulate failures."""
    print(f"  {PASS if ok else FAIL}  {label}" + (f"  -- {detail}" if detail else ""))
    return ok


def _find_a_real_tenant_id() -> str | None:
    """Any tenant with an OutreachSettings row -- i.e. any real Outreach
    tenant, whether or not it has accounts configured yet. Deliberately not
    list_active_tenant_ids() (that's exactly the function under test)."""
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT tenant_id FROM outreach_settings LIMIT 1")
        row = cur.fetchone()
    return row["tenant_id"] if row else None


def run() -> int:
    print("=" * 60)
    print("  POSTGRES PORT SMOKE TEST -- multi-tenant database verification")
    print("=" * 60)
    print()

    ok = True

    # ---- 1. Connection ----
    connected, message = check_connection()
    ok &= _line(connected, "Connection", message)
    if not connected:
        print("\n  Cannot continue without a connection. Check agent/.env's DATABASE_URL.")
        return 1

    # ---- 2. Find a real tenant to test against ----
    tenant_id = _find_a_real_tenant_id()
    ok &= _line(tenant_id is not None, "Found a real Outreach tenant", tenant_id or "NONE FOUND")
    if tenant_id is None:
        print("\n  Cannot continue -- no tenant has an outreach_settings row at all.")
        return 1

    print(f"\n  Testing against tenant: {tenant_id}")

    baseline_active = repo.list_active_tenant_ids()
    print(f"  (baseline: list_active_tenant_ids() currently returns {len(baseline_active)} tenant(s)"
          f" before this test adds anything)")

    # ---- 3. Full round-trip: account + lead, tenant-scoped, cleaned up ----
    print("\n  Read/write round-trip (tenant-scoped):")
    test_account = None
    test_lead = None
    try:
        existing_accounts = repo.list_accounts(tenant_id)  # sanity call before insert
        _line(True, "list_accounts() callable pre-insert", f"{len(existing_accounts)} existing account(s)")

        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO outreach_accounts (id, tenant_id, label, platform, status)
                VALUES (gen_random_uuid()::text, %s, 'SMOKE TEST -- safe to ignore', 'linkedin', 'active')
                RETURNING *
                """,
                (tenant_id,),
            )
            test_account = dict(cur.fetchone())
        ok &= _line(bool(test_account.get("id")), "insert throwaway active linkedin account", test_account.get("id", ""))

        found_via_get = repo.get_account(test_account["id"], tenant_id=tenant_id)
        ok &= _line(found_via_get is not None, "get_account() reads it back")

        now_active = repo.list_active_tenant_ids()
        ok &= _line(
            tenant_id in now_active,
            "list_active_tenant_ids() now includes this tenant",
            f"{len(now_active)} active tenant(s) total",
        )

        test_lead = repo.insert_lead(tenant_id, {
            "account_id": test_account["id"],
            "platform": "linkedin",
            "business_name": "SMOKE TEST -- safe to ignore",
            "status": "discovered",
            "weak_points": ["placeholder weak point"],  # exercises JSON-as-TEXT write
        })
        ok &= _line(bool(test_lead.get("id")), "insert throwaway lead under that account", test_lead.get("id", ""))

        read_back = repo.get_lead(test_lead["id"], tenant_id=tenant_id)
        ok &= _line(
            read_back is not None and read_back["business_name"] == test_lead["business_name"],
            "read it back",
        )
        ok &= _line(
            read_back is not None and read_back["weak_points"] == ["placeholder weak point"],
            "JSON-as-TEXT field round-trips as a real list, not a string",
            repr(read_back["weak_points"]) if read_back else "N/A",
        )

        # Confirm tenant isolation: a bogus/different tenant_id must NOT see this row.
        cross_tenant_read = repo.get_lead(test_lead["id"], tenant_id="nonexistent-tenant-id")
        ok &= _line(cross_tenant_read is None, "tenant scoping blocks cross-tenant read")

        # Exercise an update too.
        updated = repo.update_lead(test_lead["id"], {"status": "analyzed"}, tenant_id=tenant_id)
        ok &= _line(updated.get("status") == "analyzed", "update_lead() writes through")
    finally:
        # Always clean up, even if a check above failed mid-way -- leave zero
        # residual test data.
        if test_lead and test_lead.get("id"):
            with get_cursor() as cur:
                cur.execute(
                    "DELETE FROM outreach_leads WHERE id = %s AND tenant_id = %s",
                    (test_lead["id"], tenant_id),
                )
            _line(True, "deleted throwaway lead (DB left clean)")
        if test_account and isinstance(test_account, dict) and test_account.get("id"):
            with get_cursor() as cur:
                cur.execute(
                    "DELETE FROM outreach_accounts WHERE id = %s AND tenant_id = %s",
                    (test_account["id"], tenant_id),
                )
            _line(True, "deleted throwaway account (DB left clean)")

    final_active = repo.list_active_tenant_ids()
    ok &= _line(
        final_active == baseline_active,
        "list_active_tenant_ids() back to baseline after cleanup",
        f"{len(final_active)} active tenant(s)",
    )

    close_pool()

    print()
    print("-" * 60)
    if ok:
        print("  ALL CHECKS PASSED -- Postgres port is verified against live data.")
    else:
        print("  SOME CHECKS FAILED -- see the [FAIL] lines above.")
    print("-" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
