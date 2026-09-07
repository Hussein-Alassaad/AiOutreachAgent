"""
Checks LinkedIn for replies to leads we've messaged, via a real logged-in
browser session -- the piece crm/reply_detection.py's module docstring
explicitly deferred until linkedin_send.py's own live-account access existed.

============================================================================
RE-VERIFIED LIVE 2026-08-08 against Hussein's real messaging inbox, after
two real sends (Paul Bakery Beirut, George Gemayel) -- 3 real, confirmed
bugs found and fixed. No actual reply existed yet to test the "lead
replied" branch itself (both leads had a single message: our own outbound
send) -- see the note on sender-detection below for what that means for
confidence level.
============================================================================
1. `_CONVERSATION_LINK_SELECTOR` assumed an `<a href="...">` to match
   against `profile_url`'s slug. The real element
   (`msg-conversation-listitem__link`) is a plain `<div>` with NO href
   attribute at all -- confirmed by inspecting the raw captured HTML, not
   guessed. That made every lookup silently fail; `_open_thread_for_lead`
   would return False for every lead, always. Fixed by matching on the
   conversation's visible participant-name text instead (real DOM:
   `h3.msg-conversation-listitem__participant-names` contains the exact
   business name, e.g. "Paul Bakery Beirut" -- confirmed present verbatim
   for both real leads messaged so far) rather than a URL that was never
   there.

2. `_THREAD_MESSAGE_TIMESTAMP_SELECTOR`'s `<time>` element has NO `datetime`
   attribute in the real DOM -- confirmed live, it only contains human text
   ("8:29 PM"). The old code's `get_attribute("datetime")` always returned
   None, so `_newest_message_if_from_lead` always bailed out immediately
   regardless of whether a reply existed. There's no reliable absolute
   timestamp in the DOM at all for a same-day message (LinkedIn shows
   relative/short text, not ISO), so this function now only checks whether
   the newest message's sender is the lead, dropping the
   newer-than-last-send timestamp comparison entirely (see below).

3. The docstring's own claim -- "absence of a sender-link element means
   this is our own message" -- was the wrong way around. The real captured
   thread's ONLY message so far is OUR OWN outbound send, and it DOES carry
   a `msg-s-message-group__profile-link` span with the sender's name
   ("Hussein Alassaad") inside it. LinkedIn appears to label every message
   group's sender, not just the other party's. Fixed by matching the
   sender-name text against the LEAD's business_name specifically, instead
   of inferring identity from whether a link element exists at all.

STILL NOT FULLY VERIFIED as of 2026-08-08: neither test lead had actually
replied yet at that point, so the true positive path (a real reply
detected and correctly attributed) had not been exercised end to end.

2026-09-07: ported the same real-conversation-sync rework already
live-verified on instagram_reply_check.py's identical function (see that
module's own docstring for the specifics that were confirmed live there,
against a genuine multi-message conversation with real incoming replies
and manually-sent outgoing messages both present). The underlying
mechanics -- content-based dedup, checking "replied" leads too, not just
"contacted", backfilling manually-sent outgoing messages so Reply Here
shows the real full conversation -- are IDENTICAL logic between the two
platforms (see _sync_thread_messages() below, byte-for-byte the same
approach as Instagram's). What's genuinely different, and still NOT
live-verified on LinkedIn specifically, is the sender-identification
mechanism this relies on (_read_thread_messages()'s reliance on LinkedIn's
own message-group sender labels, carried forward across consecutive
same-sender messages) -- every account available the night this was
written was already degraded from unrelated testing, so this hasn't had
its own real multi-message LinkedIn conversation to confirm against yet.
Watch this closely against the first real one.

APPROACH: pull-based, same shape as whatsapp_reply_check.py. For every
lead at "contacted" OR "replied" that we reached via LinkedIn (not
WhatsApp -- that channel's own checker already covers WhatsApp-contacted
leads), open the account's own messaging inbox, find that lead's
conversation thread by its business_name, and read every message in it,
tagged by sender name.

This is real browser automation, not a public API -- the same
automation-detection exposure linkedin_send.py itself already carries.
Runs on its own fast poll now (scheduler.py's run_reply_detection_poll(),
~every 3 min), not the old once-daily full-pipeline cadence -- see that
function's own docstring for why that changed 2026-09-07.
============================================================================
"""

from __future__ import annotations

import datetime as dt

from playwright.sync_api import Page

from agent.core.pacing import human_delay
from agent.core.session import ProxyIpMismatch, SessionManager
from agent.crm.reply_detection import handle_reply_detected
from agent.db import repositories as repo

LINKEDIN_MESSAGING_URL = "https://www.linkedin.com/messaging/"
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"


class SessionLoggedOut(RuntimeError):
    """
    Raised when a saved session (cookies restored from storage_state) is no
    longer actually authenticated on LinkedIn's side -- live-confirmed
    2026-09-06: an account the dashboard displayed as "Connected" the whole
    time silently failed every real send because navigating any real page
    redirected to LinkedIn's own login/authwall. The account's
    login_status is already corrected to "failed" by the caller before
    this is raised, so the dashboard reflects reality on its next read
    instead of staying stuck on a stale "Connected".

    Defined HERE, not in linkedin_send.py, even though the send paths are
    what originally needed it 2026-09-06 -- linkedin_send.py already
    imports selectors/URLs from this module (see its own import block), so
    the reverse import this module would otherwise need to reuse a
    definition living in linkedin_send.py would be circular. This module
    has no outgoing dependency on linkedin_send.py, so defining it here and
    having linkedin_send.py import it instead is the one direction that
    actually works.
    """


def _raise_if_logged_out(page: Page, account: dict) -> None:
    """
    LIVE-CONFIRMED 2026-09-06, two real behaviors, both need checking:
      1. A PERSON profile URL (linkedin.com/in/...) genuinely redirects to
         /login, /authwall, or /uas/login when the session is invalid --
         the original version of this check, and still correct for that
         case.
      2. A COMPANY page URL does NOT redirect at all with an empty/invalid
         session -- LinkedIn renders the public logged-out view at the
         SAME url instead (confirmed live: page.url stayed exactly
         ".../company/partners-insurance-consultancy/" with zero cookies
         loaded). The only real, reliable signal there is the logged-out
         page's own "Join now"/"Sign in" chrome, which a real authenticated
         session never shows.
    Checking both is what actually covers every profile_url shape this
    module sends to, not just the one that happens to redirect.
    """
    url_redirected = "/login" in page.url or "/authwall" in page.url or "/uas/login" in page.url
    # LIVE-CONFIRMED 2026-09-06: checking immediately after
    # wait_until="domcontentloaded" (no settle time) reads 0 for a real
    # logged-out page -- the "Join now" chrome hadn't rendered yet, a false
    # negative that let a genuinely logged-out session sail through
    # undetected. wait_for() with a short timeout catches it once rendered
    # without slowing down the common case (a real authenticated session
    # never shows this element, so the wait always exhausts silently there
    # -- unavoidable, bounded, and still far cheaper than misreporting the
    # account as healthy).
    try:
        page.get_by_text("Join now", exact=True).first.wait_for(state="visible", timeout=4_000)
        logged_out_chrome = True
    except Exception:  # noqa: BLE001 -- Playwright's TimeoutError means the element never showed, i.e. a real session
        logged_out_chrome = False
    if not (url_redirected or logged_out_chrome):
        return
    repo.update_account(account["id"], {"login_status": "failed", "login_error": "Session logged out on LinkedIn -- reconnect via the extension."})
    raise SessionLoggedOut(
        f"Account {account.get('label') or account['id']} is no longer logged in on LinkedIn "
        f"(url={page.url!r}, logged_out_chrome={logged_out_chrome})."
    )


# RE-VERIFIED live 2026-08-08 against Hussein's real inbox -- see module
# docstring for exactly what changed and why.
_CONVERSATION_LIST_ITEM_SELECTOR = "li.msg-conversation-listitem"
_THREAD_MESSAGE_SELECTOR = "div.msg-s-event-listitem"
_THREAD_MESSAGE_SENDER_SELECTOR = "span.msg-s-message-group__profile-link"
_THREAD_MESSAGE_BODY_SELECTOR = "p.msg-s-event-listitem__body"


def _has_linkedin_sent(lead_id: str) -> bool:
    """
    True if this lead was ever actually LinkedIn-messaged (nothing to check
    a reply against otherwise). Replaces the old _last_linkedin_sent_at()'s
    timestamp value -- see module docstring point 2 for why an absolute
    "newer than X" comparison isn't possible here: the real thread's <time>
    element has no machine-readable datetime attribute, only human text
    ("8:29 PM"), confirmed live 2026-08-08.
    """
    return any(
        m.get("channel") == "linkedin" and m.get("send_status") == "sent"
        for m in repo.messages_for_lead(lead_id)
    )


def _open_thread_for_lead(page: Page, account: dict, business_name: str) -> bool:
    """
    Finds this lead's conversation in the messaging inbox by matching the
    thread's visible participant-name text against business_name, and opens
    it. Returns False if no matching thread exists yet -- treated as "no
    reply to check" rather than an error, same as a missing WhatsApp number.

    RE-VERIFIED 2026-08-08: previously matched profile_url's slug against
    the conversation link's href -- confirmed live that
    msg-conversation-listitem__link is a plain <div> with NO href attribute
    at all, so that match could never succeed. Real DOM does carry the
    lead's exact business name as visible text
    (h3.msg-conversation-listitem__participant-names), confirmed against
    both real leads messaged so far ("Paul Bakery Beirut", "George Gemayel |
    Bakery Consultancy - Aliments Est." both appeared verbatim) -- matching
    on that instead.

    2026-09-07: `account` added (was page-only) so a genuinely logged-out
    session is detected and persisted -- see linkedin_send.py's
    _raise_if_logged_out() for the same fix already applied to every
    LinkedIn send path, same reasoning: silently returning False for a
    logged-out session reports an identical "no reply" result as a lead
    that genuinely hasn't replied, a false negative with no visible error.
    Also lands on the feed first (not straight into messaging) before
    checking replies -- see instagram_send.py's send_reply() for the fuller
    reasoning on why a cold session's first-ever request landing directly
    on a messaging surface is worth avoiding, applied here for the same
    defense-in-depth reason instagram_reply_check.py's equivalent got it.
    """
    page.goto(LINKEDIN_FEED_URL, timeout=30_000, wait_until="domcontentloaded")
    _raise_if_logged_out(page, account)
    human_delay(1.0, 2.5)
    # Same wait_until="domcontentloaded" fix applied across every other
    # LinkedIn navigation in this codebase 2026-08-03 -- see
    # discovery/linkedin.py's module docstring for the real, reproducible
    # timeouts that "load" caused on LinkedIn's heavy SPA pages.
    page.goto(LINKEDIN_MESSAGING_URL, timeout=30_000, wait_until="domcontentloaded")
    _raise_if_logged_out(page, account)
    # LIVE-CONFIRMED 2026-09-07 (same race fixed in linkedin_send.send_reply,
    # see its comment): the inbox renders its conversation list client-side
    # AFTER domcontentloaded, so an instant .count() reads 0 while the thread
    # is still rendering. Here that returned False silently -- a real, waiting
    # reply was skipped with no error surfaced anywhere, which is strictly
    # worse than the send path's loud failure.
    item = page.locator(_CONVERSATION_LIST_ITEM_SELECTOR, has_text=business_name).first
    try:
        item.wait_for(state="visible", timeout=15_000)
    except Exception:  # noqa: BLE001 -- Playwright TimeoutError means this lead genuinely has no thread yet
        return False
    human_delay()
    item.click()
    return True


def _read_thread_messages(page: Page, business_name: str) -> list[dict]:
    """
    Reads EVERY message currently in the open thread, in chronological
    order, each tagged "lead" or "us" by matching its sender name against
    business_name. Real gap fixed 2026-09-07, mirroring the identical fix
    already made to instagram_reply_check.py's equivalent: the original
    version of this function only ever looked at the SINGLE NEWEST
    message, so a message sent manually (not through this platform) was
    invisible on the dashboard, and only the first reply in an ongoing
    conversation was ever recorded (see check_linkedin_replies()'s own
    docstring history for that half of the fix).

    RE-VERIFIED 2026-08-08 (still true): LinkedIn labels every message
    group's sender, not just the other party's -- confirmed live, our own
    outbound send DOES carry a real sender name
    (span.msg-s-message-group__profile-link). NOT yet live-verified,
    though realistic given LinkedIn's own messaging UI convention:
    consecutive messages from the SAME sender may share one label instead
    of repeating it per-message -- handled by carrying forward the last
    seen sender name to any message with no label of its own, rather than
    assuming a missing label means something else.
    """
    messages = page.locator(_THREAD_MESSAGE_SELECTOR)
    count = messages.count()
    if count == 0:
        return []

    results = []
    last_sender_name: str | None = None
    for i in range(count):
        el = messages.nth(i)
        sender = el.locator(_THREAD_MESSAGE_SENDER_SELECTOR).first
        if sender.count() > 0:
            sender_text = (sender.text_content(timeout=2_000) or "").strip()
            if sender_text:
                last_sender_name = sender_text
        if last_sender_name is None:
            continue  # no sender identified yet for this or any prior message -- skip rather than guess
        body = (el.locator(_THREAD_MESSAGE_BODY_SELECTOR).first.text_content(timeout=2_000) or "").strip()
        if not body:
            continue
        results.append({"from": "lead" if last_sender_name == business_name else "us", "text": body})

    return results


def check_linkedin_replies() -> list[dict]:
    """
    Checks both "contacted" leads (never replied yet) AND "replied" leads
    (an ongoing back-and-forth) reached via LinkedIn, opens the owning
    account's messaging inbox, and records whatever's genuinely new on
    either side of the conversation. Meant to run on the same cadence as
    whatsapp_reply_check.check_whatsapp_replies() (see
    scheduler.run_reply_detection_poll()).

    Real gap fixed 2026-09-07, mirroring the identical fix already made to
    instagram_reply_check.check_instagram_replies(): this originally only
    checked "contacted" leads and only the single newest message, so (1) a
    second reply in an ongoing conversation was invisible once the lead
    moved to "replied", and (2) a message sent manually from LinkedIn's
    real site (not through this platform) never appeared anywhere on the
    dashboard. See _sync_thread_messages()'s own docstring for the
    content-based dedup/backfill logic shared with Instagram's version.

    Returns one result dict per lead actually checked -- leads never
    LinkedIn-messaged, or with no matching thread found, are skipped.
    """
    results = []
    leads = [
        lead for lead in repo.leads_by_status("contacted") + repo.leads_by_status("replied")
        if lead.get("platform") == "linkedin"
    ]
    if not leads:
        return results

    accounts_by_id = {}
    with SessionManager() as sessions:
        for lead in leads:
            if not _has_linkedin_sent(lead["id"]):
                continue

            business_name = lead.get("business_name") or ""
            account = accounts_by_id.get(lead["account_id"])
            if account is None:
                account = repo.get_account(lead["account_id"])
                accounts_by_id[lead["account_id"]] = account
            if not account:
                continue

            try:
                context, page, new_verified_ip = sessions.open(account)
            except ProxyIpMismatch as exc:
                # Skip just this lead's reply check -- every other lead under
                # a DIFFERENT account still gets checked this run. The same
                # mismatched account will be retried (and skipped again) on
                # the next run until Account Health's proxy fields are fixed;
                # not worth a separate "already warned this run" cache here
                # since accounts_by_id already avoids repeating the
                # repo.get_account() lookup. repo.insert_error() directly
                # (not scheduler.log_error(), which lives in scheduler.py --
                # scheduler.py itself imports this module, so importing back
                # from it here would be circular) -- same underlying error
                # log table, tenant_id resolved from the active
                # tenant_scope() the same way, this file just doesn't have
                # its own error-logging wrapper the way scheduler.py's
                # try/except blocks do.
                try:
                    repo.insert_error({
                        "stage": "proxy_ip_mismatch", "channel": "linkedin",
                        "account_id": account["id"], "error_message": str(exc), "is_expected": False,
                    })
                except Exception:  # noqa: BLE001 -- logging itself must never crash this run
                    pass
                results.append({"lead_id": lead["id"], "replied": False, "error": str(exc)})
                continue
            if new_verified_ip and not account.get("verified_proxy_ip"):
                repo.update_account(account["id"], {"verified_proxy_ip": new_verified_ip})
                account["verified_proxy_ip"] = new_verified_ip
            try:
                found = _open_thread_for_lead(page, account, business_name)
                live_messages = _read_thread_messages(page, business_name) if found else []
            except SessionLoggedOut as exc:
                # login_status is already persisted "failed" by
                # _raise_if_logged_out itself -- record this as a real
                # error, not a silent "no reply" (see _open_thread_for_lead's
                # own docstring for why that distinction matters).
                results.append({"lead_id": lead["id"], "replied": False, "error": str(exc)})
                continue
            finally:
                sessions.close(account["id"], context)

            if not live_messages:
                results.append({"lead_id": lead["id"], "replied": False})
                continue

            new_replies, new_outgoing = _sync_thread_messages(lead, account, live_messages)
            results.append({
                "lead_id": lead["id"],
                "replied": new_replies > 0,
                "new_replies": new_replies,
                "new_outgoing_backfilled": new_outgoing,
            })

    return results


def _sync_thread_messages(lead: dict, account: dict, live_messages: list[dict]) -> tuple[int, int]:
    """
    Identical logic to instagram_reply_check.py's _sync_thread_messages()
    -- see that function's own docstring for the full reasoning. Dedup by
    CONTENT within each direction (no stable per-message id available from
    either platform's DOM), so a body already known on the matching side is
    treated as already-recorded; a genuinely new message on either side
    gets backfilled into the correct table.

    Returns (new_replies_recorded, new_outgoing_backfilled).
    """
    known_incoming = {r.get("body") for r in repo.replies_for_lead(lead["id"])}
    known_outgoing = {
        m.get("edited_body") or m.get("body")
        for m in repo.messages_for_lead(lead["id"])
        if m.get("channel") == "linkedin"
    }

    new_replies = 0
    new_outgoing = 0
    for msg in live_messages:
        text = msg["text"]
        if msg["from"] == "lead":
            if text in known_incoming:
                continue
            handle_reply_detected(
                lead["id"],
                channel="linkedin",
                body=text,
                replied_at=dt.datetime.now(dt.timezone.utc),
                account_id=account["id"],
            )
            known_incoming.add(text)
            new_replies += 1
        else:
            if text in known_outgoing:
                continue
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            repo.insert_message({
                "lead_id": lead["id"],
                "channel": "linkedin",
                "body": text,
                "approval_status": "approved",
                "approved_at": now_iso,
                "send_status": "sent",
                "sent_at": now_iso,
                "sent_via_account": account["id"],
            })
            known_outgoing.add(text)
            new_outgoing += 1

    return new_replies, new_outgoing
