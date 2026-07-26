"""
One isolated Playwright browser context per account, on that account's proxy.

HARD RULE: contexts and proxy IPs never mix between accounts. Each account is
permanently assigned one dedicated sticky residential IP (once proxies are added
in Phase 10). Rotating proxies are never used -- a new IP per request looks like
a bot, which is the opposite of what a sticky proxy is for.

Architecture: ONE shared Chromium process is launched for the whole run (cheap),
and each account gets its own BrowserContext (Playwright's isolated-profile
concept -- separate cookies, storage, and cache per context, like separate
incognito profiles). This gives full isolation between accounts without paying
for 3 separate browser processes.
"""

from __future__ import annotations

import pathlib
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright

from agent import config

# Where each account's persistent login state (cookies, local storage) is saved
# between runs. Gitignored since Phase 0 -- this is real session data, equivalent
# to being logged into the account.
STORAGE_DIR = pathlib.Path(__file__).parent.parent / "browser_profiles"


def build_proxy_config(account: dict) -> dict[str, str] | None:
    """
    Turn an account row's proxy_* columns into the dict Playwright expects, or
    None if no proxy is configured yet (the case for all 3 accounts today --
    proxies are a Phase 10 concern, and the schema allows this slot to be empty
    from day one, per the spec).
    """
    host = account.get("proxy_host")
    if not host:
        return None

    proxy: dict[str, str] = {"server": f"http://{host}:{account['proxy_port']}"}
    if account.get("proxy_username"):
        proxy["username"] = account["proxy_username"]
        proxy["password"] = account["proxy_password"]
    return proxy


def _storage_path(account_id: str) -> pathlib.Path:
    return STORAGE_DIR / f"{account_id}.json"


class SessionManager:
    """
    Owns one shared Playwright + Browser instance for the duration of a run, and
    hands out isolated, per-account contexts from it.

    Usage:
        with SessionManager() as sessions:
            context, page = sessions.open(account)
            ...
            sessions.close(account["id"], context)
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> "SessionManager":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=config.HEADLESS)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def open(self, account: dict) -> tuple[BrowserContext, "Page"]:  # noqa: F821
        """
        Create this account's isolated context: its own proxy (if configured)
        and its own restored cookies/storage from the last run, if any exist.
        """
        assert self._browser is not None, "SessionManager must be used as a context manager"

        STORAGE_DIR.mkdir(exist_ok=True)
        storage_path = _storage_path(account["id"])

        context = self._browser.new_context(
            proxy=build_proxy_config(account),
            storage_state=str(storage_path) if storage_path.exists() else None,
        )
        page = context.new_page()
        return context, page

    def close(self, account_id: str, context: BrowserContext) -> None:
        """
        Save this account's cookies/storage back to its own file before closing,
        so the next run picks up an already-logged-in session rather than
        starting fresh -- re-authenticating from scratch every day is itself a
        signal platforms watch for.
        """
        STORAGE_DIR.mkdir(exist_ok=True)
        context.storage_state(path=str(_storage_path(account_id)))
        context.close()
