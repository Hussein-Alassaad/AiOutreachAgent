"""
The core of the "Connect account" feature: launches a real Playwright
browser (visible, on this container's own Xvfb display -- see
live_login/Dockerfile/entrypoint.sh) through an account's own proxy,
detects a successful LinkedIn/Instagram login, and saves the session the
exact same way scripts/manual_login.py and the scheduler's own
SessionManager do.

Video/input no longer flow through this module at all -- x11vnc (talking
to the same Xvfb display Chromium renders onto) and websockify handle that
entirely at the OS/VNC-protocol level (see live_login/server.py's own
docstring for why that replaced the previous hand-rolled CDP screencast +
Input.dispatch*Event relay). This module's only remaining job: launch the
browser onto the right display with the right proxy, watch for a
successful login, and persist the result -- exposed as a small handle
object (LoginSessionHandle) so server.py can race "login detected" against
"client's websocket closed" without this module needing to know anything
about websockets at all.

Uses Playwright's ASYNC API (not the sync API core/session.py's
SessionManager uses elsewhere in this codebase) -- a deliberate, isolated
deviation, matching the reasoning that already existed here: this module
runs inside an asyncio event loop alongside server.py's websocket-proxying
work, and the sync API would block that loop for the entire lifetime of a
login session (up to 10 minutes).

Reuses core/session.py's build_proxy_config, _storage_path, LOGIN_URLS,
and LOGGED_IN_CHECK directly rather than reimplementing them -- those stay
the single source of truth for proxy-config shape, storage-path
convention, and login-detection selectors. The anti-detection browser
launch settings (UA, disable-automation-controlled flag, navigator.webdriver
override, heavy-media blocking) are replicated here by hand since
SessionManager itself is written against the sync API and can't be called
from async code -- keep these in sync with core/session.py's
SessionManager.__enter__/open() if either changes. Note these mitigations
do NOT make Google's own "Continue with Google" button reliable -- that's
a separate, known, still-open limitation (Google's detection targets the
CDP-attached/automation-launched browser itself, not the input transport
this file changed).
"""

from __future__ import annotations

import asyncio
import logging
import os

from playwright.async_api import async_playwright, Page
from Xlib import X, XK, display as xlib_display
from Xlib.ext import xtest

from agent.core.session import build_proxy_config, _storage_path, LOGIN_URLS, LOGGED_IN_CHECK_ASYNC, CHALLENGE_URL_MARKERS
from agent.db import repositories as repo

logger = logging.getLogger(__name__)

_LOGIN_TIMEOUT_SECONDS = 600  # 10 minutes, matching manual_login.py's wait
_LOGIN_POLL_INTERVAL_SECONDS = 2
_IP_CHECK_URL = "https://api.ipify.org?format=json"

_DESKTOP_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
# LIVE-CONFIRMED 2026-08-31: this used to also block "image" (the CPU/
# bandwidth-saving intent was reasonable in isolation, but wrong for THIS
# flow specifically) -- a real tenant hit a LinkedIn security checkpoint
# (an "identify the traffic lights" CAPTCHA) during a live Connect Account
# session and every challenge image came back blank, making the checkpoint
# physically impossible to complete. Unlike a page background or decorative
# asset, this session is VNC-streamed live to a real person who has to be
# able to see and click on exactly what's rendered -- blocking images here
# doesn't just save bandwidth invisibly, it removes content the person in
# front of the screen directly depends on. "font"/"media" (video/audio)
# stay blocked -- neither is ever load-bearing for reading or clicking a
# login form or a challenge.
_BLOCKED_RESOURCE_TYPES = {"media", "font"}


class LiveLoginError(Exception):
    """Raised for any condition that should end the session before it ever
    reaches the login-wait phase -- proxy missing, browser launch failure,
    proxy IP verification failure, etc. Caller (server.py) reports this as
    the account's terminal login_error and closes the client websocket."""


async def _block_heavy_media(route) -> None:
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


class LoginSessionHandle:
    """
    Returned by start_login_session() once the browser is launched,
    proxy-verified, and sitting on the real login page -- i.e. once there's
    something worth VNC-streaming to a client. Owns the Playwright
    browser/context/playwright objects for this one session and is
    responsible for their cleanup via close(), which is always safe to call
    (idempotent, tolerates partial/failed setup).
    """

    def __init__(self, playwright_cm, browser, context, page, account: dict, platform: str):
        self._playwright_cm = playwright_cm
        self._browser = browser
        self._context = context
        self._page = page
        self._account = account
        self._platform = platform
        self._timeout_reason = f"Timed out waiting for login ({_LOGIN_TIMEOUT_SECONDS // 60} minutes)."

    async def wait_for_login(self) -> bool:
        """Polls LOGGED_IN_CHECK_ASYNC until it's true or the timeout
        elapses. Runs as its own cancellable task from server.py's side
        (raced against the client websocket relay) -- cancellation here
        just stops the poll loop, it does not need special handling since
        the caller's finally block is what closes the browser either way.

        LIVE-CONFIRMED 2026-08-31: a real tenant's correct credentials
        still failed here -- LinkedIn showed them a security checkpoint
        (routine for a login from a proxy IP/location LinkedIn hasn't seen
        for that account before, nothing to do with a code bug), and the
        client saw no indication anything was different from a normal
        login -- just the same generic "timed out" message after the full
        10 minutes, as if they'd never submitted the form at all. This
        keeps polling through a checkpoint (open_or_login()'s unattended
        path fails immediately on one since nobody's there to clear it,
        but a live VNC session has a real person who often CAN complete
        it -- an OTP, "is this you", etc. -- so cutting the session the
        instant a checkpoint URL appears would take away their chance to
        finish it) but now remembers having seen one via
        CHALLENGE_URL_MARKERS (the same markers open_or_login() already
        treats as a distinct failure -- see core/session.py), so if the
        full timeout is still reached, timeout_reason() below reports the
        real cause instead of the generic message.
        """
        check = LOGGED_IN_CHECK_ASYNC[self._platform]
        challenge_markers = CHALLENGE_URL_MARKERS[self._platform]
        saw_challenge = False
        elapsed = 0
        while elapsed < _LOGIN_TIMEOUT_SECONDS:
            try:
                if await check(self._page):
                    return True
                if any(marker in self._page.url for marker in challenge_markers):
                    saw_challenge = True
            except Exception:
                pass
            await asyncio.sleep(_LOGIN_POLL_INTERVAL_SECONDS)
            elapsed += _LOGIN_POLL_INTERVAL_SECONDS
        if saw_challenge:
            self._timeout_reason = (
                f"{self._platform.capitalize()} asked for extra verification (a security checkpoint, phone/email "
                "code, etc.) during this attempt and it wasn't completed in time -- this is expected the first "
                "time an account logs in from a new location/proxy. Reconnect and complete whatever "
                f"{self._platform.capitalize()} shows before the 10-minute window runs out."
            )
        return False

    def timeout_reason(self) -> str:
        return self._timeout_reason

    async def save_and_mark_connected(self) -> None:
        account_id = self._account["id"]
        tenant_id = self._account["tenant_id"]
        storage_path = _storage_path(account_id)
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        await self._context.storage_state(path=str(storage_path))

        with repo.tenant_scope(tenant_id):
            repo.update_account(account_id, {
                "login_status": "connected",
                "login_connected_at": _utcnow_iso(),
                "login_connecting_at": None,
                "login_error": None,
            })

    async def close(self) -> None:
        try:
            if self._context:
                await self._context.close()
        except Exception:
            logger.exception("Error closing context for account %s", self._account.get("id"))
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            logger.exception("Error closing browser for account %s", self._account.get("id"))
        try:
            await self._playwright_cm.__aexit__(None, None, None)
        except Exception:
            logger.exception("Error stopping Playwright for account %s", self._account.get("id"))


async def start_login_session(account: dict, platform: str) -> LoginSessionHandle:
    """
    Launches the browser (visible, onto this container's Xvfb display),
    verifies the proxy IP, and navigates to the real login page. Raises
    LiveLoginError for any failure up through that point. Returns a handle
    once the browser is genuinely sitting on the login page and ready to be
    VNC-streamed and waited on.
    """
    account_id = account["id"]
    proxy = build_proxy_config(account)
    if not proxy:
        raise LiveLoginError("This account has no proxy configured.")

    playwright_cm = async_playwright()
    p = await playwright_cm.__aenter__()
    browser = None
    context = None
    try:
        # headless=False: this is the whole point of the VNC rewrite --
        # Chromium renders onto the container's real Xvfb display (see
        # entrypoint.sh) instead of an invisible headless surface, so
        # x11vnc has an actual picture to stream and can deliver real X11
        # input events into it. --start-maximized + no viewport override
        # lets fluxbox's window management and the Xvfb screen's own
        # 1366x768 size (entrypoint.sh) determine the window, matching what
        # VIEWPORT_WIDTH/HEIGHT the frontend expects to see.
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                # This droplet is 1 vCPU with no real GPU (Xvfb is a
                # software-only virtual display) -- LIVE-VERIFIED via `top`
                # that CPU, not network, is the real bottleneck behind the
                # lag reported during testing. Without these, Chromium
                # still tries to use GPU compositing/rasterization, which
                # on a virtual display means slow CPU-side software GPU
                # emulation instead of skipping that work entirely.
                "--disable-gpu",
                "--disable-gpu-compositing",
                "--disable-software-rasterizer",
                # Chrome throttles background tabs/timers by default, which
                # is irrelevant here (this is always the one foreground
                # tab) but the throttling bookkeeping itself still costs
                # cycles -- disable it outright.
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ],
        )
        storage_path = _storage_path(account_id)
        context = await browser.new_context(
            proxy=proxy,
            storage_state=str(storage_path) if storage_path.exists() else None,
            user_agent=_DESKTOP_CHROME_UA,
            no_viewport=True,  # let the real window fill the real (Xvfb) screen instead of a fixed viewport
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        await context.route("**/*", _block_heavy_media)
        page = await context.new_page()
        await _reset_caps_lock()

        await _verify_proxy_ip_async(account, page)
        await page.goto(LOGIN_URLS[platform], timeout=30000)

        return LoginSessionHandle(playwright_cm, browser, context, page, account, platform)
    except Exception as exc:
        if context:
            try:
                await context.close()
            except Exception:
                pass
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        try:
            await playwright_cm.__aexit__(None, None, None)
        except Exception:
            pass
        if isinstance(exc, LiveLoginError):
            raise
        logger.exception("Failed to start live login session for account %s", account_id)
        raise LiveLoginError(f"Unexpected error: {exc}") from exc


def _reset_caps_lock_sync() -> None:
    """
    LIVE-CONFIRMED 2026-08-31: a real tenant's Caps Lock keypresses over
    the VNC session did nothing -- Shift worked fine, only Caps Lock was
    broken. Root cause: Xvfb's own Caps Lock modifier-lock state is a
    single persistent toggle for the container's ENTIRE lifetime, not
    reset between sessions -- and noVNC's own client-side correction for
    this (rfb.js's _handleKeyEvent, meant to detect a mismatch between the
    browser's real Caps Lock LED and Xvfb's remote one and auto-correct
    it) depends on x11vnc actually sending LedState updates, which isn't
    guaranteed. If Xvfb's Caps Lock was ever left ON by an earlier session
    (crashed, killed by the watchdog, this container reused across many
    test connections during this same debugging session), every later
    session inherits that same stuck ON state with nothing to notice or
    fix it -- from the tenant's side, Caps Lock just silently does
    nothing.

    LIVE-TESTED 2026-08-31 (directly against the real droplet, see this
    session's own debugging): the first fix attempted here drove Chromium
    via Playwright's page.keyboard.press("CapsLock") and read back what
    landed in an <input> -- confirmed NOT to work. Playwright's keyboard
    API is CDP-level synthetic input; Chromium's renderer processes it
    for its OWN internal notion of modifier state (what JS's
    getModifierState() reports), but that is a completely separate,
    disconnected value from Xvfb's actual X11-level modifier-lock state --
    proven by directly toggling Caps Lock via Playwright and reading
    Xvfb's real LockMask straight back unchanged. Only a genuine XTEST-
    injected key press+release (exactly what a real physical keyboard, or
    x11vnc relaying a real client keystroke, produces) actually flips
    Xvfb's real modifier-lock state -- verified directly via this same
    XTEST call against the real Xvfb display, confirmed via
    root.query_pointer().mask & X.LockMask flipping true->false and
    back. python-xlib's core Xlib.display has no way to read or set
    modifier-lock state at all (its get_keyboard_control().led_mask is
    LED indicator state, a DIFFERENT thing, confirmed unaffected by this
    call) -- only the XTEST extension (Xlib.ext.xtest) can drive it.

    Runs synchronously against Xvfb directly (a fresh, unauthenticated
    core X11 connection -- same trust boundary as Xvfb's own -ac flag in
    entrypoint.sh, this connection never leaves the container), entirely
    independent of Chromium/noVNC/x11vnc and whatever gap exists between
    them. Wrapped in asyncio.to_thread() by its caller since python-xlib
    itself is synchronous.
    """
    d = xlib_display.Display(os.environ.get("DISPLAY", ":1"))
    try:
        root = d.screen().root
        locked = bool(root.query_pointer().mask & X.LockMask)
        if not locked:
            return
        logger.warning("Xvfb's Caps Lock was ON at session start -- correcting to OFF.")
        caps_keycode = d.keysym_to_keycode(XK.XK_Caps_Lock)
        xtest.fake_input(d, X.KeyPress, caps_keycode)
        d.sync()
        xtest.fake_input(d, X.KeyRelease, caps_keycode)
        d.sync()
        still_locked = bool(root.query_pointer().mask & X.LockMask)
        if still_locked:
            logger.error("Caps Lock correction attempt didn't take effect -- still locked afterward.")
    finally:
        d.close()


async def _reset_caps_lock() -> None:
    """Best-effort -- if this probe/correction fails for any reason
    (display connection hiccup, etc.), don't block the whole session over
    it; worst case Caps Lock behaves the way it did before this fix."""
    try:
        await asyncio.to_thread(_reset_caps_lock_sync)
    except Exception:
        logger.exception("Caps Lock reset failed -- continuing without it.")


async def _verify_proxy_ip_async(account: dict, page: Page) -> None:
    """
    Async-API equivalent of core/session.py's verify_proxy_ip() -- same
    logic (navigate through this context to an IP-echo endpoint, compare
    against OutreachAccount.verifiedProxyIp), reimplemented here because
    that function is written against the sync Page API and this module
    runs entirely on the async one.
    """
    response = await page.goto(_IP_CHECK_URL, timeout=15000)
    if response is None or not response.ok:
        raise LiveLoginError("Could not verify this account's proxy IP before starting login.")
    body = await response.json()
    current_ip = body.get("ip")
    if not current_ip:
        raise LiveLoginError("Proxy IP check returned no IP address.")

    expected_ip = account.get("verified_proxy_ip")
    if expected_ip and current_ip != expected_ip:
        raise LiveLoginError(
            f"This account is running through {current_ip}, but its proxy previously verified as "
            f"{expected_ip}. Refusing to proceed -- check the proxy credentials in Account Health."
        )
    if not expected_ip:
        with repo.tenant_scope(account["tenant_id"]):
            repo.update_account(account["id"], {"verified_proxy_ip": current_ip})


def _utcnow_iso() -> str:
    """Matches this codebase's established convention for timestamp fields
    written via repo.update_account() -- see scheduler.py's own
    login_connected_at write (line ~392) for the same pattern."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
