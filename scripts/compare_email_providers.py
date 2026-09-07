"""
Compare email-finder providers on the SAME real domains, side by side.

Written 2026-09-07 to settle the Hunter vs Anymail Finder vs Icypeas
decision with measured data instead of vendor marketing -- no independent
benchmark covers Lebanon/MENA, which is the only market that matters here.

Only domain-search (domain -> email) is compared, deliberately: every
LinkedIn lead in the real database today has a website but NO founder_name,
so scheduler.py's _maybe_find_email() falls through to
find_company_emails() essentially every time. Person lookup is the path
that almost never runs, so benchmarking it would measure the wrong thing.

Usage (no key needed for a provider you're not testing -- it's skipped):

    HUNTER_API_KEY=... ANYMAIL_API_KEY=... python scripts/compare_email_providers.py

Costs real credits: one lookup per domain per configured provider. All three
providers charge only for a FOUND result, so misses are free.
"""

from __future__ import annotations

import os
import sys

import httpx

# Real domains pulled from the production leads table on 2026-09-07 -- the
# actual market being sold into, not a sample of US/EU tech companies that
# every vendor benchmark is built from.
DOMAINS = [
    "computel.com.lb",
    "insurancecommission.gov.lb",
    "zamfm.co.uk",
    "graphit.am",
    "wehbeinsured.com",
    "aqlonsystems.com",
    "housekeepingclub.co.uk",
]

TIMEOUT = 20.0


def hunter_domain_search(domain: str, key: str) -> tuple[str | None, str]:
    """Returns (email_or_None, note). Mirrors discovery/hunter.py's own call."""
    try:
        r = httpx.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "api_key": key, "limit": 1},
            timeout=TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 -- a network failure is a result worth printing, not a crash
        return None, f"network error: {exc}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:80]}"
    emails = (r.json().get("data") or {}).get("emails") or []
    if not emails:
        return None, "no email found"
    return emails[0].get("value"), f"confidence={emails[0].get('confidence')}"


def anymail_domain_search(domain: str, key: str) -> tuple[str | None, str]:
    try:
        r = httpx.post(
            "https://api.anymailfinder.com/v5.0/search/company.json",
            headers={"Authorization": f"Bearer {key}"},
            json={"domain": domain},
            timeout=TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"network error: {exc}"
    # 404 is Anymail's documented "not found" -- a real answer, not an error.
    if r.status_code == 404:
        return None, "no email found"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:80]}"
    results = r.json().get("results") or {}
    email = results.get("email")
    return (email, f"validation={results.get('validation')}") if email else (None, "no email found")


def icypeas_domain_search(domain: str, key: str) -> tuple[str | None, str]:
    try:
        r = httpx.post(
            "https://app.icypeas.com/api/domain-search",
            headers={"Authorization": key, "Content-Type": "application/json"},
            json={"domain": domain},
            timeout=TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"network error: {exc}"
    if r.status_code not in (200, 201):
        return None, f"HTTP {r.status_code}: {r.text[:80]}"
    data = r.json()
    # Icypeas is async: this returns a request id, results are polled/webhooked.
    # Surfaced honestly rather than silently reported as a miss.
    return None, f"async API -- returned {str(data)[:70]}"


PROVIDERS = [
    ("Hunter", "HUNTER_API_KEY", hunter_domain_search),
    ("AnymailFinder", "ANYMAIL_API_KEY", anymail_domain_search),
    ("Icypeas", "ICYPEAS_API_KEY", icypeas_domain_search),
]


def main() -> int:
    active = [(n, os.environ.get(e), f) for n, e, f in PROVIDERS if os.environ.get(e)]
    if not active:
        print("No API keys set. Set at least one of: HUNTER_API_KEY, ANYMAIL_API_KEY, ICYPEAS_API_KEY")
        return 1

    print(f"Testing {len(DOMAINS)} real domains against: {', '.join(n for n, _, _ in active)}\n")
    hits = {name: 0 for name, _, _ in active}

    for domain in DOMAINS:
        print(f"{domain}")
        for name, key, fn in active:
            email, note = fn(domain, key)
            if email:
                hits[name] += 1
                print(f"   {name:15} FOUND  {email:38} ({note})")
            else:
                print(f"   {name:15} --     {note}")
        print()

    print("=" * 62)
    print(f"{'PROVIDER':16} {'FOUND':>7} {'OF':>4} {'HIT RATE':>10}")
    for name, _, _ in active:
        rate = hits[name] / len(DOMAINS) * 100
        print(f"{name:16} {hits[name]:>7} {len(DOMAINS):>4} {rate:>9.0f}%")
    print("=" * 62)
    print("\nAll three bill only for found results, so misses above cost nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
