"""
Phase 1 checkpoint proof — verifies the database is real and read/write works.

Run this AFTER creating the Supabase project, filling agent/.env, and running
both SQL files (02_SUPABASE_SCHEMA.sql then database/policies.sql):

    agent/venv/Scripts/python.exe -m agent.db.smoke_test

It checks, in order:
  1. Connection works (credentials + network).
  2. All 9 tables exist and are queryable.
  3. The seed data is present (3 accounts + 1 settings row).
  4. A round-trip write works: insert a throwaway lead, read it back, delete it.

Step 4 cleans up after itself, so running this leaves the database exactly as it
was. Nothing here touches your real accounts or sends anything.
"""

from __future__ import annotations

from agent.db import repositories as repo
from agent.db.client import check_connection, get_client

# Every table the schema creates. `runs` is included even though the Phase 1
# checklist omitted it — it's the data source for the Run Status panel.
TABLES = [
    "accounts", "settings", "leads", "messages", "pipeline_history",
    "follow_ups", "client_history", "notifications_log", "runs",
]

PASS = "[PASS]"
FAIL = "[FAIL]"


def _line(ok: bool, label: str, detail: str = "") -> bool:
    """Print one result line and return ok so callers can accumulate failures."""
    print(f"  {PASS if ok else FAIL}  {label}" + (f"  — {detail}" if detail else ""))
    return ok


def run() -> int:
    print("=" * 60)
    print("  PHASE 1 SMOKE TEST — database verification")
    print("=" * 60)
    print()

    ok = True

    # ---- 1. Connection ----
    connected, message = check_connection()
    ok &= _line(connected, "Connection", message)
    if not connected:
        print("\n  Cannot continue without a connection. Check agent/.env.")
        return 1

    # ---- 2. All 9 tables exist ----
    print("\n  Tables:")
    client = get_client()
    for table in TABLES:
        try:
            client.table(table).select("*", count="exact").limit(0).execute()
            _line(True, table)
        except Exception as exc:  # noqa: BLE001
            ok &= _line(False, table, str(exc))

    # ---- 3. Seed data present ----
    print("\n  Seed data:")
    accounts = repo.list_accounts()
    ok &= _line(len(accounts) == 3, "3 accounts seeded", f"found {len(accounts)}")
    settings = repo.get_settings()
    ok &= _line(settings is not None, "settings row seeded",
                f"style={settings.get('message_style') if settings else 'MISSING'}")

    # ---- 4. Round-trip write/read/delete ----
    print("\n  Read/write round-trip:")
    test_lead = None
    try:
        test_lead = repo.insert_lead({
            "platform": "linkedin",
            "business_name": "SMOKE TEST — safe to ignore",
            "status": "discovered",
        })
        ok &= _line(bool(test_lead.get("id")), "insert test lead", test_lead.get("id", ""))

        read_back = repo.get_lead(test_lead["id"])
        ok &= _line(
            read_back is not None and read_back["business_name"] == test_lead["business_name"],
            "read it back",
        )
    finally:
        # Always clean up, even if a check above failed mid-way.
        if test_lead and test_lead.get("id"):
            get_client().table("leads").delete().eq("id", test_lead["id"]).execute()
            _line(True, "deleted test lead (DB left clean)")

    print()
    print("-" * 60)
    if ok:
        print("  ALL CHECKS PASSED — Phase 1 database is ready.")
    else:
        print("  SOME CHECKS FAILED — see the [FAIL] lines above.")
    print("-" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
