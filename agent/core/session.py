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
from agent.db.crypto import decrypt_secret

# Where each account's persistent login state (cookies, local storage) is saved
# between runs. Gitignored since Phase 0 -- this is real session data, equivalent
# to being logged into the account.
STORAGE_DIR = pathlib.Path(__file__).parent.parent / "browser_profiles"

# Shared by every code path that needs to drive a real LinkedIn/Instagram login
# page directly (scripts/manual_login.py's local CLI flow, live_login/session.py's
# remote CDP-streamed flow) -- one source of truth for the login URL and the
# DOM/URL marker that means "login succeeded", so the two flows can never drift
# out of sync with each other.
LOGIN_URLS = {
    "linkedin": "https://www.linkedin.com/login",
    "instagram": "https://www.instagram.com/accounts/login/",
}

LOGGED_IN_CHECK = {
    "linkedin": lambda page: page.locator("input[placeholder='Search']").count() > 0
    or "/feed" in page.url,
    "instagram": lambda page: page.locator("svg[aria-label='Home']").count() > 0
    or "/accounts/onetap" in page.url,
}

# Async-API equivalent of LOGGED_IN_CHECK above, for live_login/session.py
# (which runs on Playwright's async API, not the sync one every other module
# in this file uses -- see that module's docstring for why). Same selectors,
# same either/or logic -- keep these two dicts in sync by hand if either
# platform's login-success marker ever needs to change.
async def _linkedin_logged_in(page) -> bool:
    return await page.locator("input[placeholder='Search']").count() > 0 or "/feed" in page.url


async def _instagram_logged_in(page) -> bool:
    return await page.locator("svg[aria-label='Home']").count() > 0 or "/accounts/onetap" in page.url


LOGGED_IN_CHECK_ASYNC = {
    "linkedin": _linkedin_logged_in,
    "instagram": _instagram_logged_in,
}

# Shared with live_login/session.py's wait_for_login() (see its own comment)
# so the manual VNC login flow can tell "still typing/hasn't submitted yet"
# apart from "LinkedIn/Instagram threw up a security checkpoint that no
# amount of waiting will clear on its own" -- same URL markers
# open_or_login() below already uses for the unattended/automated login path.
# One source of truth so the two flows can't drift apart on what counts as
# a challenge redirect.
CHALLENGE_URL_MARKERS = {
    "linkedin": ("/checkpoint/", "/uas/verify", "/authwall"),
    "instagram": ("/challenge/", "/accounts/suspended/"),
}


def build_proxy_config(account: dict) -> dict[str, str] | None:
    """
    Turn an account row's proxy_* columns into the dict Playwright expects, or
    None if no proxy is configured (the schema allows this slot to be empty).

    PORTED 2026-08-20: `account["proxy_password"]` used to be plaintext in
    the old standalone schema. It's now `proxy_password_enc` -- AES-256-GCM
    ciphertext written by the Next.js dashboard when an owner saves a proxy
    password (see src/lib/outreach/crypto.ts, mirrored read-side-only in
    db/crypto.py). Decrypted here, right before handing it to Playwright,
    rather than in repositories.py, so the plaintext password never sits in
    a lead/account dict any longer than the single call site that actually
    needs it.

    If decryption fails for any reason (missing/mismatched
    OUTREACH_ENCRYPTION_KEY, corrupt stored value) decrypt_secret() returns
    None rather than raising (see its own docstring) -- that's treated the
    same as "no password set": the proxy still gets its host/port/username,
    just without a password, rather than crashing this account's whole
    session setup over one bad credential.
    """
    host = account.get("proxy_host")
    if not host:
        return None

    proxy: dict[str, str] = {"server": f"http://{host}:{account['proxy_port']}"}
    if account.get("proxy_username"):
        proxy["username"] = account["proxy_username"]
        encrypted = account.get("proxy_password_enc")
        if encrypted:
            decrypted = decrypt_secret(encrypted)
            if decrypted:
                proxy["password"] = decrypted
    return proxy


def _storage_path(account_id: str) -> pathlib.Path:
    return STORAGE_DIR / f"{account_id}.json"


# Resource types Playwright's own classification never needs to actually
# download for this agent's purposes -- every DOM element, selector, and
# text field it reads still loads normally; only the visual asset bytes
# behind them are skipped. Cuts proxy bandwidth substantially (images/video
# are the bulk of a modern LinkedIn/Instagram page's weight) with zero
# effect on what the agent can see, since analysis/scoring/message
# generation are entirely text-based (see analysis/prompts.py) and never
# inspect image or video content.
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


def _block_heavy_media(route) -> None:
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        route.abort()
    else:
        route.continue_()


class SessionManager:
    """
    Owns one shared Playwright + Browser instance for the duration of a run, and
    hands out isolated, per-account contexts from it.

    Usage:
        with SessionManager() as sessions:
            context, page, verified_ip = sessions.open(account)
            ...
            sessions.close(account["id"], context)
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> "SessionManager":
        self._playwright = sync_playwright().start()
        # LIVE-VERIFIED 2026-08-21: plain launch(headless=...) with no extra
        # args leaves navigator.webdriver == True (Chromium's own default
        # automation flag) -- confirmed via page.evaluate('navigator.webdriver')
        # against a real LinkedIn login page, and confirmed to matter: the real
        # visible "Sign in" button was found and clicked correctly, but fired
        # ZERO network requests every time with this flag set -- LinkedIn's
        # client-side JS silently no-ops the submit rather than showing any
        # error, so the failure is invisible without checking network traffic
        # directly (this took an explicit page.on('request', ...) listener to
        # even notice). --disable-blink-features=AutomationControlled is
        # Chromium's own documented flag for removing that fingerprint at the
        # browser level. This is discovery's account_pool sessions too, not
        # just the new login flow -- every context from this browser instance
        # benefits, and discovery was never confirmed clean of the same signal.
        self._browser = self._playwright.chromium.launch(
            headless=config.HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
        )
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def open(self, account: dict) -> tuple[BrowserContext, "Page", str | None]:  # noqa: F821
        """
        Create this account's isolated context: its own proxy (if configured)
        and its own restored cookies/storage from the last run, if any exist.
        Also verifies (via verify_proxy_ip()) that a configured proxy
        resolves to the same real IP this account has always used, BEFORE
        returning the context to the caller -- raises ProxyIpMismatch if it
        doesn't, closing the mismatched context itself first so callers
        never have to remember to clean up on this specific failure path.

        Returns (context, page, verified_ip) -- verified_ip is None when no
        proxy is configured for this account at all (nothing to verify),
        otherwise the real IP just measured, for the caller to persist via
        repo.update_account(account_id, {"verified_proxy_ip": ip}) the
        first time (this module never touches repositories.py directly,
        same separation every other module in this codebase keeps -- see
        db/repositories.py's own module docstring, so it cannot write this
        itself).

        Does NOT attempt a credential login itself -- that's a distinct,
        riskier action (see ensure_logged_in()'s docstring) that only makes
        sense to attempt when there's no saved session to reuse yet, and
        whose outcome needs to be reported back to the database by whoever
        called open() (this module never touches repositories.py directly,
        same separation every other module in this codebase keeps -- see
        db/repositories.py's own module docstring). Call
        open_or_login(account) instead of open() directly from a call site
        that's prepared to persist a login outcome.
        """
        assert self._browser is not None, "SessionManager must be used as a context manager"

        STORAGE_DIR.mkdir(exist_ok=True)
        storage_path = _storage_path(account["id"])

        context = self._browser.new_context(
            proxy=build_proxy_config(account),
            storage_state=str(storage_path) if storage_path.exists() else None,
            # Playwright's default Chromium UA includes "HeadlessChrome" when
            # headless=True, and always identifies the exact Playwright-bundled
            # Chromium build/version -- both are checkable automation signals
            # independent of navigator.webdriver. A realistic recent desktop
            # Chrome UA removes that specific tell. Not living in config.py:
            # this is a fixed anti-detection default, not a per-deployment
            # setting anyone should need to tune.
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
        )
        # LIVE-VERIFIED 2026-08-21: --disable-blink-features=AutomationControlled
        # (set at browser launch, see __enter__ above) did NOT by itself flip
        # navigator.webdriver to false/undefined when re-checked after adding
        # it -- Chromium's CDP-based automation still exposes the property via
        # a different path than that flag covers. This init script explicitly
        # deletes/overrides it at the JS layer, re-applied on every new
        # document in this context (add_init_script runs before any page
        # script, including on internal navigations) rather than a one-time
        # page.evaluate() call, which would only patch the current page and
        # miss it again after LinkedIn's own client-side routing/redirects.
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        context.route("**/*", _block_heavy_media)
        page = context.new_page()

        verified_ip: str | None = None
        if build_proxy_config(account) is not None:
            try:
                verified_ip = verify_proxy_ip(account, page)
            except ProxyIpMismatch:
                context.close()
                raise

        return context, page, verified_ip

    def open_or_login(self, account: dict) -> tuple[BrowserContext, "Page", str | None, str | None]:  # noqa: F821
        """
        Like open(), but for an account with saved login_email/
        login_password_enc and NO existing saved session yet (a brand-new
        credential-connected account, or one whose session expired/was
        logged out): attempts ensure_logged_in() on the fresh context before
        handing it back. Inherits open()'s proxy-IP verification -- a
        ProxyIpMismatch from that check propagates straight out of this
        method too (login is never attempted on a context whose proxy
        already failed verification).

        Returns (context, page, login_error, verified_ip) -- login_error is
        None when no login attempt was needed (a session already existed)
        or the attempt succeeded; otherwise it's the human-readable
        LoginFailed message, for the caller to write to
        OutreachAccount.loginError via repo.update_account(). verified_ip is
        open()'s own return value passed through unchanged -- non-None only
        on this account's very FIRST successful check (verified_proxy_ip
        was still unset going in), for the caller to persist as the new
        baseline; None on every later call once a baseline already exists,
        since open() only measures then, it doesn't return the value again
        on every run. The context/page are always returned even on a failed
        login (some pages -- e.g. a challenge screen -- may still be worth a
        screenshot/log in the future), it's the caller's job to decide
        whether to proceed to discovery/sending after checking login_error,
        not this method's.
        """
        storage_path = _storage_path(account["id"])
        has_saved_session = storage_path.exists()
        context, page, verified_ip = self.open(account)
        # open() returns the measured IP whenever a proxy is configured, not
        # only on a first-time check -- only surface it to the caller (who
        # writes it to the database) when there was no prior baseline,
        # otherwise every single run would needlessly re-write an unchanged
        # value.
        new_baseline_ip = verified_ip if not account.get("verified_proxy_ip") else None

        needs_login_attempt = (
            not has_saved_session and account.get("login_email") and account.get("login_password_enc")
        )
        if not needs_login_attempt:
            return context, page, None, new_baseline_ip

        try:
            ensure_logged_in(account, page)
            return context, page, None, new_baseline_ip
        except LoginFailed as exc:
            return context, page, str(exc), new_baseline_ip

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


class LoginFailed(RuntimeError):
    """
    Raised when a credential-based login attempt (see ensure_logged_in())
    doesn't reach a logged-in state. The message is written to
    OutreachAccount.loginError as-is -- keep it specific enough for a
    non-technical tenant reading it in their dashboard to act on (e.g.
    "LinkedIn asked for a verification code" is actionable; a raw stack
    trace is not).
    """


class ProxyIpMismatch(RuntimeError):
    """
    Raised by verify_proxy_ip() when this account's context resolved to a
    DIFFERENT real outbound IP than the one recorded the last time this
    account ran successfully -- the runtime enforcement of this module's own
    documented "HARD RULE: contexts and proxy IPs never mix between
    accounts" (see module docstring), which previously existed only as
    intent, never actually checked. A mismatch means something changed on
    the proxy provider's side (a lapsed/renewed subscription that got
    reassigned a different IP, a misconfigured proxy credential pointing at
    the wrong account, etc.) -- exactly the scenario that would make
    LinkedIn/Instagram see this account suddenly logging in from a new
    location, the single strongest automated-abuse signal both platforms
    watch for. Callers must treat this as a hard stop for the run (do not
    proceed to login/discovery/sending on this account), not a warning to
    log and continue past.
    """


_IP_CHECK_URL = "https://api.ipify.org?format=json"
_IP_CHECK_TIMEOUT_MS = 15_000


def verify_proxy_ip(account: dict, page: "Page") -> str:  # noqa: F821
    """
    Confirms this account's context is actually exiting through the real IP
    its proxy is supposed to give it, and that this IP matches
    OutreachAccount.verifiedProxyIp from the last time this account ran --
    not just that a proxy config with the right host/port was PASSED to
    Playwright (build_proxy_config() above already guarantees that part),
    but that it actually resolved to the SAME real-world IP address as
    before. Navigates to a plain IP-echo API through this context (so the
    request genuinely goes through the configured proxy, the same way any
    other page load in this context would) and reads the real outbound IP
    back.

    Returns the verified IP string. Raises ProxyIpMismatch if
    verified_proxy_ip was already set for this account and the IP just
    measured doesn't match it -- callers must not proceed past that.
    First-ever check for an account (verified_proxy_ip still null) always
    passes and just returns the measured IP for the caller to persist via
    repo.update_account(account_id, {"verified_proxy_ip": ip}) -- this
    function only measures and compares, it never writes to the database
    itself (same separation of concerns every other module in core/ and
    discovery/ already keeps from db/repositories.py, per that module's own
    docstring).
    """
    response = page.goto(_IP_CHECK_URL, timeout=_IP_CHECK_TIMEOUT_MS)
    if response is None or not response.ok:
        raise RuntimeError(
            f"Could not verify proxy IP for account {account.get('id')} -- "
            f"IP-check request failed (status {response.status if response else 'no response'})."
        )
    body = response.json()
    current_ip = body.get("ip")
    if not current_ip:
        raise RuntimeError(f"IP-check response had no 'ip' field: {body!r}")

    expected_ip = account.get("verified_proxy_ip")
    if expected_ip and current_ip != expected_ip:
        raise ProxyIpMismatch(
            f"Account {account.get('label') or account.get('id')} is running through {current_ip}, "
            f"but its proxy previously verified as {expected_ip}. Refusing to proceed -- this account's "
            f"proxy assignment may have changed. Check Account Health and confirm the proxy credentials "
            f"before resuming this account."
        )
    return current_ip


# ============================================================================
# LinkedIn: PARTIALLY live-verified 2026-08-21. Instagram: still unverified.
# ============================================================================
# Every other selector in this codebase (discovery/linkedin.py,
# discovery/instagram.py, sending/linkedin_send.py) was checked against a
# real, live page before being trusted -- that discipline is the reason this
# project's docs (PROGRESS.md) distinguish "built" from "live-tested"
# everywhere. A first live-verification pass on 2026-08-21 (fake credentials,
# no real account -- see PROGRESS.md's dated entry) already replaced this
# module's original, WRONG guesses (id="username"/id="password" -- LinkedIn's
# real login page uses React-generated random ids like «Refvtkejj356d5j6»,
# confirmed live, not stable ids at all) with what was actually confirmed on
# the page. What that pass found and fixed:
#   - Email/password fields: no stable id/name exists -- select by
#     input[type=email]:visible / input[type=password]:visible instead
#     (LinkedIn renders TWO of each, one hidden -- a naive selector without
#     :visible grabs the wrong, non-interactive one).
#   - Submit button: NOT button[type='submit'] (no such element exists on the
#     page at all) and NOT reliably get_by_role('button', name='Sign in')
#     either (that role/name also matches a same-named, zero-height, non-
#     visible duplicate element elsewhere on the page -- confirmed via
#     bounding-box inspection, not assumption). The real, correctly-positioned
#     visible submit sits at [role=button], positioned directly below the
#     password field -- selected below by that positional relationship
#     instead of by role+name alone, which this pass proved isn't unique
#     enough on its own.
#   - navigator.webdriver was True with a plain launch() (Chromium's default
#     automation fingerprint) and LinkedIn's client-side JS silently
#     swallowed every submit click with ZERO network request firing as a
#     result -- no visible error, nothing -- confirmed via an explicit
#     page.on('request', ...) listener, not by watching the page. Fixed at
#     the browser level in __enter__/open() above
#     (--disable-blink-features=AutomationControlled + an add_init_script
#     override); NOT yet re-confirmed end-to-end that a real submit fires a
#     real network request after that fix -- the live-testing pass that would
#     confirm it was intentionally stopped before completing (see
#     PROGRESS.md). That confirmation, or a real supervised login, is still
#     the next required step before this path is trusted with a real
#     account -- same caution already standing for linkedin_send.py's first
#     real Send click.
#
# Instagram's selectors below have had NONE of this live-verification pass --
# they're the same kind of "long-stable elsewhere, not confirmed on this
# page" values LinkedIn's originally-wrong ones were, and LinkedIn's own
# experience above is a concrete demonstration of why that distinction
# matters: do not trust these without the same live-verification treatment
# first.
# ============================================================================

_LOGIN_URLS = {
    "linkedin": "https://www.linkedin.com/login",
    "instagram": "https://www.instagram.com/accounts/login/",
}

# LIVE-VERIFIED 2026-08-21 (see module docstring above for what this replaced
# and why). :visible is load-bearing, not decorative -- LinkedIn renders a
# second, non-interactive copy of each field.
_LINKEDIN_EMAIL_SELECTOR = "input[type='email']:visible"
_LINKEDIN_PASSWORD_SELECTOR = "input[type='password']:visible"
# Any of these appearing after submit means LinkedIn wants something this
# agent can't provide unattended -- treated as LoginFailed with a specific,
# actionable message rather than a generic timeout.
_LINKEDIN_CHALLENGE_URL_MARKERS = CHALLENGE_URL_MARKERS["linkedin"]

# UNVERIFIED -- see module docstring. Same live-testing pass that fixed
# LinkedIn's selectors above did not reach Instagram; treat these with the
# same suspicion LinkedIn's original id="username"/id="password" deserved.
_INSTAGRAM_EMAIL_SELECTOR = "input[name='username']:visible"
_INSTAGRAM_PASSWORD_SELECTOR = "input[name='password']:visible"
_INSTAGRAM_CHALLENGE_URL_MARKERS = CHALLENGE_URL_MARKERS["instagram"]

_POST_LOGIN_TIMEOUT_MS = 20_000


def _find_visible_submit_button(page: "Page", *, min_y: float = 0) -> Any:  # noqa: F821
    """
    LIVE-VERIFIED 2026-08-21 against LinkedIn's real login page: neither
    button[type='submit'] (no such element exists) nor
    get_by_role('button', name='Sign in') (matches a same-named, zero-height,
    non-visible duplicate elsewhere on the page, confirmed via bounding-box
    inspection) reliably finds LinkedIn's real submit button. What DOES work,
    confirmed live: the real button is the first VISIBLE [role=button]
    element positioned below the password field (min_y filters out
    higher-up decoys like the Google/Microsoft/Apple SSO buttons, which sit
    above the email/password fields on both platforms' login pages).

    Not (yet) re-verified against Instagram specifically -- passed the same
    min_y filtering there on the reasoning that Instagram's login page has
    the same general shape (SSO options above, email/password/submit below),
    not because Instagram's own page has been inspected the way LinkedIn's
    was.
    """
    for candidate in page.locator("[role=button]").all():
        if not candidate.is_visible():
            continue
        box = candidate.bounding_box()
        if box and box["height"] > 0 and box["y"] >= min_y:
            return candidate
    return None


def ensure_logged_in(account: dict, page: "Page") -> None:  # noqa: F821
    """
    Attempt a credential-based login on `page` (already navigated to nothing
    in particular -- this function does its own navigation) if this account
    has no working session yet, using account["login_email"] and the
    decrypted account["login_password_enc"].

    This is the ONE unsupervised, credential-typing login path in this
    codebase -- every other account connection in this project's history
    (tools/capture_session.py) was a human typing their own password into a
    real browser, specifically because an automated login is the single
    highest-risk moment for a platform security challenge (SMS code, "is
    this you?" prompt) that no unattended script can solve. This function
    exists anyway at the tenant's explicit, informed request (see
    prisma/schema.prisma's OutreachAccount.loginEmail/loginPasswordEnc
    comment and PROGRESS.md's dated entry on this feature) -- it does not
    attempt to solve a challenge if one appears; it fails cleanly and
    reports why, rather than hanging or guessing.

    Raises LoginFailed on any non-success outcome (wrong platform, missing
    credentials, wrong password, a security challenge, or the page simply
    not looking logged-in afterward). Never raises on success -- returns
    None. Does NOT persist the session to disk itself -- that's
    SessionManager.close()'s job, called by whoever calls this, same as
    every other page-full of actions a caller takes with `page` today.
    """
    platform = account.get("platform")
    login_url = _LOGIN_URLS.get(platform)
    if not login_url:
        raise LoginFailed(f"No credential-based login exists for platform {platform!r} (only linkedin/instagram).")

    email = account.get("login_email")
    encrypted_password = account.get("login_password_enc")
    if not email or not encrypted_password:
        raise LoginFailed("No login email/password saved for this account yet.")

    password = decrypt_secret(encrypted_password)
    if not password:
        raise LoginFailed(
            "Saved login password couldn't be decrypted -- OUTREACH_ENCRYPTION_KEY may not match what the "
            "dashboard encrypted it with. Re-enter the password in Account Health to retry."
        )

    email_selector, password_selector, challenge_markers = (
        (_LINKEDIN_EMAIL_SELECTOR, _LINKEDIN_PASSWORD_SELECTOR, _LINKEDIN_CHALLENGE_URL_MARKERS)
        if platform == "linkedin"
        else (_INSTAGRAM_EMAIL_SELECTOR, _INSTAGRAM_PASSWORD_SELECTOR, _INSTAGRAM_CHALLENGE_URL_MARKERS)
    )

    page.goto(login_url, timeout=30_000, wait_until="domcontentloaded")

    try:
        page.locator(email_selector).first.wait_for(state="visible", timeout=10_000)
    except Exception as exc:  # noqa: BLE001 -- the login form itself not appearing is its own distinct failure
        raise LoginFailed(
            f"{platform}'s login page didn't show the expected email field ({email_selector}) -- "
            "the page layout may have changed since this was last checked, or the proxy/network blocked it."
        ) from exc

    # human_delay()/human_type() (agent/core/pacing.py) are used everywhere
    # else this codebase types into a real page (linkedin_send.py) -- same
    # reasoning applies here: instant, atomic field fills are themselves an
    # automation signal, doubly so on a login form specifically, which is
    # the page every platform's bot-detection watches most closely.
    from agent.core.pacing import human_delay, human_type  # local import: avoids a cycle with pacing's own imports

    human_delay()
    human_type(page.locator(email_selector).first, email)
    human_delay()
    password_field = page.locator(password_selector).first
    human_type(password_field, password)
    human_delay()

    # See _find_visible_submit_button's docstring: neither a plain
    # button[type=submit] selector nor role+name alone reliably finds
    # LinkedIn's real submit button (confirmed live 2026-08-21 -- the
    # role+name match hit a same-named, zero-height decoy elsewhere on the
    # page). Using the password field's own bottom edge as the min_y filter
    # instead of a hardcoded pixel value, so this isn't tied to the specific
    # 1366x768 viewport this was tested at.
    password_box = password_field.bounding_box()
    min_y = password_box["y"] if password_box else 0
    submit_button = _find_visible_submit_button(page, min_y=min_y)
    if submit_button is None:
        raise LoginFailed(
            f"Couldn't find {platform}'s submit button after filling in credentials -- the page layout may "
            "have changed since this was last checked."
        )

    # LIVE-CONFIRMED 2026-08-21: a click that finds and "clicks" the right
    # element can still fire ZERO network requests -- LinkedIn's client-side
    # JS silently swallowed the submit when navigator.webdriver was
    # detectable, with no visible error and no URL change, indistinguishable
    # from a slow page unless request traffic is actually watched. This
    # listener is what caught that bug originally; it stays permanently so
    # that specific failure mode (bot-blocked before the login was even
    # attempted server-side) gets its own distinct, actionable error instead
    # of being misreported as "wrong password" below.
    login_requests: list[str] = []
    page.on("request", lambda req: login_requests.append(req.url) if "login" in req.url.lower() else None)

    submit_button.click()

    try:
        page.wait_for_load_state("domcontentloaded", timeout=_POST_LOGIN_TIMEOUT_MS)
    except Exception:  # noqa: BLE001 -- fall through to the URL/DOM check below regardless
        pass

    current_url = page.url
    if any(marker in current_url for marker in challenge_markers):
        raise LoginFailed(
            f"{platform.capitalize()} is asking for extra verification (redirected to {current_url}) -- "
            "this can't be completed automatically. The account owner needs to log in manually once "
            "(ask them to use tools/capture_session.py) to clear whatever check was triggered, then retry here."
        )

    if login_url in current_url or "/login" in current_url:
        if not login_requests:
            raise LoginFailed(
                f"The submit click didn't actually reach {platform.capitalize()} at all (no network request "
                "fired) -- this usually means the browser was detected as automated. Not a wrong password; "
                "this needs a code fix, not a credential re-entry. Flag this to support."
            )
        raise LoginFailed(
            f"{platform.capitalize()} rejected the login (still on the login page after submitting) -- "
            "double-check the email and password saved for this account."
        )
