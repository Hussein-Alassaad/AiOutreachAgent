"""
Opens a real, visible browser through an account's own proxy so the client
can log into LinkedIn/Instagram with their OWN credentials -- never typed
into or stored by the Nexaris app. Once they log in and this script detects
a logged-in page, it saves the session the same way the agent itself would
(core/session.py's _storage_path: browser_profiles/{account_id}.json), so
every later automated run reuses this session instead of needing a
password on file at all.

Run through the account's real proxy (same IP the agent will always use
for that account) so the saved session's fingerprint matches what the
agent presents later -- logging in through a different IP than the one
that gets reused would look like exactly the kind of account-hijack
signal LinkedIn/Instagram are built to catch.

Usage:
    python scripts/manual_login.py <account_id> <tenant_id> <platform>

    platform is "linkedin" or "instagram" -- picks the login URL.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from playwright.sync_api import sync_playwright
from agent.core.session import build_proxy_config, _storage_path, LOGIN_URLS, LOGGED_IN_CHECK
from agent.db import repositories as repo


def main():
    if len(sys.argv) != 4:
        print("Usage: python scripts/manual_login.py <account_id> <tenant_id> <platform>")
        sys.exit(1)

    account_id, tenant_id, platform = sys.argv[1], sys.argv[2], sys.argv[3]
    if platform not in LOGIN_URLS:
        print(f"platform must be 'linkedin' or 'instagram', got {platform!r}")
        sys.exit(1)

    with repo.tenant_scope(tenant_id):
        account = repo.get_account(account_id)
    if not account:
        print(f"No account found with id {account_id} for tenant {tenant_id}")
        sys.exit(1)

    proxy = build_proxy_config(account)
    if not proxy:
        print(f"Account {account['label']} has no proxy configured -- set one in Account Health first.")
        sys.exit(1)

    print(f"Opening a real browser for: {account['label']} ({platform})")
    print(f"Through proxy: {proxy['server']}")
    print("Log in with the CLIENT'S OWN account. This window will wait until you're logged in.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(proxy=proxy)
        page = context.new_page()
        page.goto(LOGIN_URLS[platform], timeout=30000)

        check = LOGGED_IN_CHECK[platform]
        print("Waiting for login to complete (checking every 3s, up to 10 minutes)...")
        for _ in range(200):
            page.wait_for_timeout(3000)
            try:
                if check(page):
                    break
            except Exception:
                continue
        else:
            print("Timed out waiting for login. Closing without saving -- rerun once ready.")
            browser.close()
            sys.exit(1)

        storage_path = _storage_path(account_id)
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(storage_path))
        print(f"Session saved to {storage_path}")
        print("The agent will reuse this session on every future run -- no password needed on file.")
        browser.close()


if __name__ == "__main__":
    main()
