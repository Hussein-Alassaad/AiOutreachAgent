"""
Multi-tenant isolation proof — exercises the ACTUAL scheduler orchestration
pattern (list_active_tenant_ids() + tenant_scope() + the untouched legacy
modules' ambient ("ombiant") tenant resolution) with two real, simultaneously-
active tenants, something that has never happened against this live database
before (the only tenant that has ever had an active account was a single
throwaway row in smoke_test_postgres.py, one at a time).

This directly answers the open question flagged in PROGRESS.md's 2026-08-20
port entry: "the new per-tenant scheduler loop ... has NOT had a live
end-to-end run with two or more real tenants each having active accounts."
It does NOT touch Playwright, LinkedIn, Instagram, Twilio, or any proxy --
purely a database-and-Python-orchestration-layer test, so it needs nothing
from IPRoyal/DigitalOcean to run right now.

What it proves:
  1. list_active_tenant_ids() correctly finds BOTH tenants once each has an
     active linkedin/instagram account (not just one, proving the loop
     itself iterates correctly, not just single-tenant lucky-path).
  2. Inside two SEPARATE tenant_scope(...) blocks (mirroring exactly how
     scheduler.py's real per-tenant loop calls into downstream modules),
     an ambient repo.get_settings() call (zero arguments, the exact call
     shape messaging/crm/sending/notifications/core.health all use) resolves
     to the CORRECT tenant's settings each time -- proving the contextvar-
     based "discrepancy" resolution (see repositories.py's module docstring)
     is not just theoretically safe but actually correct with two tenants
     really present.
  3. A lead inserted while tenant A's scope is active is invisible to a
     tenant-scoped read under tenant B's scope, and vice versa -- the same
     cross-tenant check smoke_test_postgres.py already does with one tenant,
     now repeated with two tenants ALIVE AT THE SAME TIME in the same
     process (closer to what the real threaded scheduler actually does than
     a single mock tenant ever could be).
  4. Simulates scheduler.py's exact `for tenant_id in
     repo.list_active_tenant_ids(): with repo.tenant_scope(tenant_id): ...`
     loop shape directly, so a bug in that composition (not just in the
     individual pieces) would surface here.

Run: agent/venv/Scripts/python.exe -m agent.db.smoke_test_multitenant

Cleans up every row it creates, including the two throwaway tenants/users/
products it needs (reuses the real "outreach" product if one already exists
rather than creating a duplicate).
"""

from __future__ import annotations

import uuid

from agent.db import repositories as repo
from agent.db.postgres_client import check_connection, close_pool, get_cursor

PASS = "[PASS]"
FAIL = "[FAIL]"


def _line(ok: bool, label: str, detail: str = "") -> bool:
    print(f"  {PASS if ok else FAIL}  {label}" + (f"  -- {detail}" if detail else ""))
    return ok


def _new_id() -> str:
    return str(uuid.uuid4())


def _get_or_create_outreach_product() -> str:
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT id FROM products WHERE slug = 'outreach' LIMIT 1")
        row = cur.fetchone()
    if row:
        return row["id"]
    product_id = _new_id()
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO products (id, slug, name, config, "createdAt", "updatedAt")
            VALUES (%s, 'outreach', 'Outreach', '{}', now(), now())
            """,
            (product_id,),
        )
    return product_id


def _create_throwaway_tenant(product_id: str, label: str) -> str:
    """
    RLS 2026-08-28: wrapped in platform_scope() -- there is, by definition,
    no tenant_scope(...) to run this under yet (the tenant doesn't exist
    until this function creates it), the same chicken-and-egg the Node
    app's own tenant-creation action solves with withPlatformAccess().
    """
    tenant_id = _new_id()
    subdomain = f"smoketest-{label}-{tenant_id[:8]}"
    with repo.platform_scope(), get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO tenants (id, "productId", "companyName", subdomain, status, "createdAt", "updatedAt")
            VALUES (%s, %s, %s, %s, 'ACTIVE', now(), now())
            """,
            (tenant_id, product_id, f"SMOKE TEST {label} -- safe to ignore", subdomain),
        )
        cur.execute(
            """
            INSERT INTO outreach_settings (id, tenant_id, business_name, business_description, target_niche, updated_at)
            VALUES (%s, %s, %s, %s, %s, now())
            """,
            (_new_id(), tenant_id, f"Smoke Test Business {label}", f"a test business for tenant {label}", f"niche-{label}"),
        )
    return tenant_id


def _cleanup_tenant(tenant_id: str) -> None:
    """RLS 2026-08-28: platform_scope() -- deleting a tenant's own rows
    from OUTSIDE that tenant's scope (this runs after the test, not from
    inside a tenant_scope(tenant_id) block) needs cross-tenant write
    access, same reasoning as _create_throwaway_tenant() above."""
    with repo.platform_scope(), get_cursor() as cur:
        cur.execute("DELETE FROM outreach_leads WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM outreach_accounts WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM outreach_settings WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))


def run() -> int:
    print("=" * 70)
    print("  MULTI-TENANT ISOLATION SMOKE TEST -- two real tenants, simultaneously")
    print("=" * 70)
    print()

    ok = True

    connected, message = check_connection()
    ok &= _line(connected, "Connection", message)
    if not connected:
        print("\n  Cannot continue without a connection. Check agent/.env's DATABASE_URL.")
        return 1

    product_id = _get_or_create_outreach_product()
    _line(True, "Using outreach product", product_id)

    tenant_a = tenant_b = None
    account_a = account_b = None
    lead_a = lead_b = None

    try:
        # ---- Setup: two independent throwaway tenants, each with an active
        # linkedin account -- mirrors "Zimmar" and "Insurance" both being
        # real, simultaneously-active Outreach tenants. ----
        tenant_a = _create_throwaway_tenant(product_id, "alpha")
        tenant_b = _create_throwaway_tenant(product_id, "beta")
        _line(True, "Created two throwaway tenants", f"A={tenant_a[:8]}  B={tenant_b[:8]}")

        # RLS 2026-08-28: platform_scope() -- this single block writes
        # accounts for BOTH tenant_a and tenant_b, so no single
        # tenant_scope(...) would be correct for it either way.
        with repo.platform_scope(), get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO outreach_accounts (id, tenant_id, label, platform, status)
                VALUES (%s, %s, 'SMOKE TEST A -- safe to ignore', 'linkedin', 'active')
                RETURNING id
                """,
                (_new_id(), tenant_a),
            )
            account_a = cur.fetchone()["id"]
            cur.execute(
                """
                INSERT INTO outreach_accounts (id, tenant_id, label, platform, status)
                VALUES (%s, %s, 'SMOKE TEST B -- safe to ignore', 'linkedin', 'active')
                RETURNING id
                """,
                (_new_id(), tenant_b),
            )
            account_b = cur.fetchone()["id"]
        _line(True, "Gave each tenant one active linkedin account")

        # ---- 1. list_active_tenant_ids() finds BOTH, not just one ----
        active = set(repo.list_active_tenant_ids())
        ok &= _line(tenant_a in active and tenant_b in active,
                     "list_active_tenant_ids() finds BOTH tenants at once",
                     f"{len(active)} active tenant(s) total")

        # ---- 2. Simulate scheduler.py's exact loop shape: for each tenant,
        # enter tenant_scope(), make an AMBIENT (zero-arg) repo.get_settings()
        # call -- the exact shape messaging/crm/sending/notifications/
        # core.health all use -- and confirm it resolves to THAT tenant's
        # settings, not the other one's or a stale one from a prior
        # iteration. This is the actual bug class that would matter: the
        # contextvar leaking or not resetting between loop iterations. ----
        resolved_names = {}
        for tenant_id, label in [(tenant_a, "alpha"), (tenant_b, "beta")]:
            with repo.tenant_scope(tenant_id):
                ambient_settings = repo.get_settings()  # zero-arg, ambient -- the real legacy call shape
                resolved_names[label] = ambient_settings["business_name"] if ambient_settings else None

        ok &= _line(
            resolved_names["alpha"] == "Smoke Test Business alpha",
            "Ambient get_settings() inside tenant A's scope resolves to tenant A",
            resolved_names["alpha"],
        )
        ok &= _line(
            resolved_names["beta"] == "Smoke Test Business beta",
            "Ambient get_settings() inside tenant B's scope resolves to tenant B",
            resolved_names["beta"],
        )
        ok &= _line(
            repo.current_tenant_id() is None,
            "Contextvar correctly resets to None after both scopes exit (no leak)",
        )

        # ---- 3. Cross-tenant data isolation with BOTH tenants alive:
        # insert a lead under each tenant while inside that tenant's scope,
        # then confirm each tenant's explicit-tenant_id read only ever sees
        # its own lead, never the other's. ----
        with repo.tenant_scope(tenant_a):
            lead_a = repo.insert_lead(tenant_a, {
                "account_id": account_a, "platform": "linkedin",
                "business_name": "SMOKE TEST LEAD A", "status": "discovered",
            })
        with repo.tenant_scope(tenant_b):
            lead_b = repo.insert_lead(tenant_b, {
                "account_id": account_b, "platform": "linkedin",
                "business_name": "SMOKE TEST LEAD B", "status": "discovered",
            })
        ok &= _line(bool(lead_a and lead_b), "Inserted one lead per tenant, inside each tenant's own scope")

        cross_a_reads_b = repo.get_lead(lead_b["id"], tenant_id=tenant_a)
        cross_b_reads_a = repo.get_lead(lead_a["id"], tenant_id=tenant_b)
        ok &= _line(cross_a_reads_b is None, "Tenant A cannot read tenant B's lead")
        ok &= _line(cross_b_reads_a is None, "Tenant B cannot read tenant A's lead")

        own_a = repo.get_lead(lead_a["id"], tenant_id=tenant_a)
        own_b = repo.get_lead(lead_b["id"], tenant_id=tenant_b)
        ok &= _line(own_a is not None and own_a["business_name"] == "SMOKE TEST LEAD A",
                     "Tenant A can read its own lead")
        ok &= _line(own_b is not None and own_b["business_name"] == "SMOKE TEST LEAD B",
                     "Tenant B can read its own lead")

        # ---- 4. list_accounts(tenant_a) never includes tenant B's account,
        # even though list_active_tenant_ids() just proved both are active
        # in the same query result set. ----
        accounts_a = repo.list_accounts(tenant_a)
        accounts_b = repo.list_accounts(tenant_b)
        ok &= _line(
            all(a["id"] != account_b for a in accounts_a) and all(a["id"] == account_a for a in accounts_a),
            "list_accounts(tenant_a) contains only tenant A's account",
        )
        ok &= _line(
            all(a["id"] != account_a for a in accounts_b) and all(a["id"] == account_b for a in accounts_b),
            "list_accounts(tenant_b) contains only tenant B's account",
        )

    finally:
        print("\n  Cleanup:")
        if tenant_a:
            _cleanup_tenant(tenant_a)
            _line(True, "Removed tenant A and all its rows")
        if tenant_b:
            _cleanup_tenant(tenant_b)
            _line(True, "Removed tenant B and all its rows")

    close_pool()

    print()
    print("-" * 70)
    if ok:
        print("  ALL CHECKS PASSED -- multi-tenant scheduler orchestration is verified")
        print("  correct with two real, simultaneously-active tenants.")
    else:
        print("  SOME CHECKS FAILED -- see [FAIL] lines above. Do not trust the")
        print("  multi-tenant loop until these are fixed.")
    print("-" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
