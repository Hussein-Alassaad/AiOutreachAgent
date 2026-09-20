"""
Checks Instagram for replies to leads we've messaged, via a real logged-in
browser session -- Instagram's equivalent of linkedin_reply_check.py.

============================================================================
LIVE-VERIFIED 2026-09-07 against a real conversation with a genuine
incoming reply ("Thanks", from hussein._.alassaad) -- the ORIGINAL
selectors below were all wrong and have been replaced; see each constant's
own comment for exactly what real inspection found.
============================================================================

APPROACH: pull-based -- for every "contacted" OR "replied" lead reached
via Instagram, open the account's own DM inbox, find that lead's thread by
its business_name, and read the ENTIRE thread (not just the newest
message -- see _sync_thread_messages()'s own docstring for the real gap
that fixed, 2026-09-07). Every message not already recorded is backfilled
into the correct table by direction: a lead's message becomes a new
OutreachReply row (and advances the pipeline via handle_reply_detected()),
our own message (including one sent manually from the real Instagram app,
outside this platform entirely) becomes a new OutreachMessage row -- so
Reply Here shows the REAL, complete conversation regardless of how each
message was actually sent. Reuses instagram_send.py's
_CONVERSATION_LIST_ITEM_SELECTOR constant so the two modules can't
silently drift out of sync on how a thread is located.
"""

from __future__ import annotations

import datetime as dt

from playwright.sync_api import Page

from agent.core.pacing import human_delay
from agent.core.session import ProxyIpMismatch, SessionManager
from agent.crm.reply_detection import handle_reply_detected
from agent.db import repositories as repo
from agent.sending.instagram_send import (
    INSTAGRAM_INBOX_URL,
    CONVERSATION_LIST_ITEM_SELECTOR,
    _raise_if_logged_out,
    SessionLoggedOut,
    _HOME_URL,
)

# LIVE-CONFIRMED 2026-09-07: the original div[role='row'] selector matched
# ZERO elements in a real open thread -- Instagram's message bubbles carry
# no ARIA role at all, and there's no labeled/roled container wrapping the
# message list either (walked every ancestor from a real reply's text node
# to the document root: exactly one had any role/aria-label at all, a
# role='button' hover target on the bubble itself, not a list container --
# confirmed live, not assumed). Each message bubble (both incoming and
# outgoing) IS a div[role='presentation'], confirmed by finding they line
# up 1:1 with the visible message bubbles in chronological order.
#
# The one real wrinkle: the FIRST role='presentation' match on the page is
# sometimes unrelated sidebar chrome ("What's new... Your note"), not a
# message -- present only when that inbox-wide prompt hasn't been
# dismissed. Since there's no clean container to scope into instead, this
# is filtered by content instead of position: an element whose direct text
# is exactly that sidebar prompt's own copy is excluded, everything else
# role='presentation' on the page is treated as a message bubble. Scoped
# to whatever the caller navigated to (a specific /direct/t/<id>/ thread
# URL), not the inbox list page, so this never picks up unrelated
# role='presentation' elements from a different part of the app.
_SIDEBAR_PROMPT_TEXT = "What's new"
_THREAD_MESSAGE_SELECTOR = "div[role='presentation']"
_THREAD_MESSAGE_BODY_SELECTOR = "div[dir='auto']"


def _has_instagram_sent(lead_id: str) -> bool:
    return any(
        m.get("channel") == "instagram" and m.get("send_status") == "sent"
        for m in repo.messages_for_lead(lead_id)
    )


def _open_thread_for_lead(page: Page, account: dict, business_name: str) -> bool:
    """
    account is required (not just page) so a genuinely logged-out session
    is detected and persisted the same way instagram_send.py's send paths
    already do -- LIVE-CONFIRMED 2026-09-07: before this, a logged-out
    session made _open_thread_for_lead silently return False (the
    conversation list item just never "found"), which reported an
    identical "replied": False result as a lead that genuinely hasn't
    replied yet -- a real false negative with no visible error, exactly
    the failure mode a human scanning "not yet replied" against their own
    real inbox (see this codebase's own Reply Here warning banner) exists
    to catch, but silently, indefinitely, is a much worse outcome than
    surfacing it as a real error the moment it happens.

    LIVE-CONFIRMED 2026-09-07, second fix: an instant .count() check
    right after page.goto() reads 0 even when the conversation genuinely
    exists and renders moments later -- same timing race found and fixed
    in every other Instagram/LinkedIn selector tonight. wait_for() catches
    it once actually rendered.

    Third fix, same night: landing on the home feed first (not straight
    into the inbox) before checking replies, matching the same warm-up
    added to instagram_send.py's send_reply() -- see that function's own
    comment for the reasoning. This read-only check itself never actually
    lost a session tonight (only the SEND path did), but applying the same
    more-human navigation pattern here too is cheap defense-in-depth, not
    a reaction to a failure specific to this function.
    """
    page.goto(_HOME_URL, timeout=30_000, wait_until="domcontentloaded")
    _raise_if_logged_out(page, account)
    human_delay(1.0, 2.5)
    page.goto(INSTAGRAM_INBOX_URL, timeout=30_000, wait_until="domcontentloaded")
    _raise_if_logged_out(page, account)
    item = page.locator(CONVERSATION_LIST_ITEM_SELECTOR, has_text=business_name).first
    try:
        item.wait_for(state="visible", timeout=10_000)
    except Exception:  # noqa: BLE001 -- Playwright's TimeoutError means no matching thread exists, a real "no" not a crash
        return False
    human_delay()
    # REAL BUG found 2026-09-19 (real lead "xarkloofficial", reproduced
    # twice): item.click() timed out at 30s even though the SAME locator's
    # wait_for(state="visible") had just succeeded a moment earlier -- the
    # inbox list re-renders/re-sorts on its own (new activity, a
    # notification badge clearing), and Instagram's DOM has no stable
    # per-item id (see CONVERSATION_LIST_ITEM_SELECTOR's own comment), so
    # the element `item` is bound to can go stale between the wait and the
    # click. force=True skips Playwright's actionability re-checks (which
    # is what was hanging for the full 30s against a now-stale/covered
    # element) and clicks the CURRENT element at that DOM location
    # instead -- safe here specifically because wait_for already confirmed
    # a real, visible, text-matching item exists at this locator.
    try:
        item.click(timeout=10_000)
    except Exception:  # noqa: BLE001 -- see comment above; retry once against a freshly re-queried locator before giving up
        # REAL BUG found 2026-09-20, live-confirmed 3x against real lead
        # "titus.logistics" (same timeout, same lead, across 3 separate
        # runs -- a genuinely reproducible failure, not one-off flakiness):
        # force=True on the retry SKIPS Playwright's normal actionability
        # checks, which is exactly what auto-scrolls a below-the-fold item
        # into view before clicking -- so a still-off-screen item was being
        # force-clicked at a stale screen position instead. Explicit
        # scroll_into_view_if_needed() first restores that scroll step
        # without reintroducing the full 30s actionability wait the
        # original bug was about.
        item = page.locator(CONVERSATION_LIST_ITEM_SELECTOR, has_text=business_name).first
        try:
            item.scroll_into_view_if_needed(timeout=5_000)
        except Exception:  # noqa: BLE001 -- best-effort; the force click below still runs either way
            pass
        item.click(timeout=10_000, force=True)
    # LIVE-CONFIRMED 2026-09-07, third fix in this function: clicking the
    # conversation updates an in-page panel rather than navigating (page.url
    # stays on /direct/inbox/ throughout -- confirmed live), so there's no
    # navigation event to wait on, and human_delay() alone returned before
    # the thread's own messages had rendered. Reading the messages at that
    # point found only stale/empty content and reported "no reply" for a
    # thread that genuinely had one -- the exact false negative this whole
    # function exists to avoid. A real settle wait here is what actually
    # makes the read see the conversation that just opened.
    #
    # REAL BUG found 2026-09-19 (real lead "fadeltradingcompany", a genuine
    # owner-reported missed reply): the flat 3s sleep alone wasn't always
    # enough -- confirmed live, this exact lead's thread opened
    # successfully (found=True) but _read_thread_messages() then read ZERO
    # bubbles, meaning the panel genuinely hadn't finished rendering yet on
    # this resource-constrained droplet. Waiting for the thread's own
    # message selector to actually appear (same selector
    # _read_thread_messages() itself reads) is real evidence the panel is
    # populated, instead of a fixed guess at how long that takes. Falls
    # back to the original flat sleep if the wait itself times out --
    # _read_thread_messages() already tolerates finding nothing (returns an
    # empty list, treated as "no reply this poll" rather than a crash), so
    # this fallback keeps the exact previous behavior as a safety net
    # rather than ever raising here.
    try:
        page.wait_for_selector(_THREAD_MESSAGE_SELECTOR, timeout=8_000)
    except Exception:  # noqa: BLE001 -- fall through to the flat sleep below; _read_thread_messages tolerates an empty result
        pass
    page.wait_for_timeout(3_000)
    return True


def _read_thread_messages(page: Page) -> list[dict]:
    """
    Reads EVERY message bubble currently in the open thread, in
    chronological order, as {"text", "left"} -- NOT yet tagged with a
    direction. Real gap fixed 2026-09-07: the original version of this
    function only ever looked at the SINGLE NEWEST message -- live-confirmed
    the same night, a message sent manually from the real Instagram app
    (not through this platform) was correctly excluded from
    outreach_replies (it's genuinely ours, not a reply), but that also
    meant it never showed up anywhere on the dashboard at all -- Reply Here
    only ever displays OutreachMessage rows (platform-originated sends)
    plus OutreachReply rows (detected incoming replies), so a real, genuine
    part of the conversation was invisible. Reading the WHOLE thread, not
    just the tail, is what lets the caller backfill a manually-sent
    outgoing message the same way it already backfills an incoming reply.

    REAL BUG FOUND AND FIXED 2026-09-16 -- direction used to be decided
    RIGHT HERE, by comparing each bubble's horizontal position against the
    THREAD's OWN AVERAGE left offset ("us" if right of average, "lead" if
    left). That silently assumes both sides are actually represented in
    the thread. Instagram's DOM carries no reliable absolute per-bubble
    signal for "sent by me" (confirmed 2026-09-07 -- no distinguishing
    ARIA role/class on either side, see this module's own docstring), so a
    thread holding ONLY our own messages (the common case: a fresh lead
    that hasn't replied yet) has no real "left" cluster at all -- the
    average sits in the middle of OUR OWN bubbles, and roughly half of our
    own outgoing messages end up left of it and get misclassified as if
    they came from the lead. LIVE-CONFIRMED against 5 real leads
    (meteorintheyks, hnmoverseas, al_mosbah_, lafe.leb,
    lets_travel_and_discover): each had a thread with ONLY our own
    template's opening/closing lines in it, no real reply ever received,
    yet this logic fabricated 3 "lead" messages per thread out of our own
    pitch text, flipping the lead to "replied" on fake evidence.

    Fix: this function no longer guesses a direction at all -- it just
    returns each bubble's text and left offset. _sync_thread_messages()
    below does the actual classification, anchored to CONTENT already
    known to be ours or the lead's (not position), and only falls back to
    position when the thread has at least one bubble already confirmed on
    EACH side to calibrate against. See that function's own docstring for
    the full reasoning and the safe-skip fallback when no such anchor
    exists yet.
    """
    messages = page.locator(_THREAD_MESSAGE_SELECTOR)
    count = messages.count()
    if count == 0:
        return []

    boxes = []
    for i in range(count):
        el = messages.nth(i)
        text = (el.text_content(timeout=2_000) or "").strip()
        if not text or text.startswith(_SIDEBAR_PROMPT_TEXT):
            continue
        box = el.bounding_box()
        if box:
            boxes.append({"left": box["x"], "text": text})

    return boxes


def check_instagram_replies() -> list[dict]:
    """
    Checks both "contacted" leads (never replied yet) AND "replied" leads
    (an ongoing back-and-forth) reached via Instagram, opens the owning
    account's DM inbox, and records the lead's newest message if it's both
    from the lead AND genuinely new -- not the same reply already recorded
    from a previous run.

    Real gap fixed 2026-09-07: this originally only ever checked
    "contacted" leads, on the assumption that one reply ends the detection
    cycle for that lead -- true for the FIRST reply (which is what moves a
    lead onto "replied" in the first place), but wrong for any reply after
    that: a lead who's already "replied" and sends a second message in the
    same conversation was invisible to every future run, silently, since
    "replied" leads were never even looked at again. Now dedup happens by
    CONTENT (compare the newest message against the most recently recorded
    reply for this lead, repo.replies_for_lead()'s own ordering) instead of
    by lead STATUS, so a genuinely new message in an ongoing conversation
    is caught, while the same already-recorded reply read again on a later
    run is correctly skipped rather than inserted twice.
    """
    results = []
    leads = [
        lead for lead in repo.leads_by_status("contacted") + repo.leads_by_status("replied")
        if lead.get("platform") == "instagram"
    ]
    if not leads:
        return results

    accounts_by_id = {}
    with SessionManager() as sessions:
        for lead in leads:
            if not _has_instagram_sent(lead["id"]):
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
                try:
                    repo.insert_error({
                        "stage": "proxy_ip_mismatch", "channel": "instagram",
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
                live_messages = _read_thread_messages(page) if found else []
            except SessionLoggedOut as exc:
                # login_status is already persisted "failed" by
                # _raise_if_logged_out itself -- record this as a real
                # error, not a silent "no reply" (see _open_thread_for_lead's
                # own docstring for why that distinction matters).
                results.append({"lead_id": lead["id"], "replied": False, "error": str(exc)})
                continue
            except Exception as exc:  # noqa: BLE001 -- REAL BUG found 2026-09-19
                # live-confirmed: an uncaught Locator.click TimeoutError on
                # ONE lead's thread (xarkloofficial -- item found "visible"
                # but a click on it 30s later still timed out, likely a
                # stale/re-rendered element) crashed this entire function's
                # for-loop, silently skipping EVERY remaining lead in the
                # batch -- including fadeltradingcompany and titus.logistics,
                # two real leads with genuine unread replies that never even
                # got checked because they happened to sort after the bad
                # one. Every other per-lead failure path in this same loop
                # (ProxyIpMismatch, SessionLoggedOut above) already isolates
                # itself and continues; a plain timeout/exception from
                # _open_thread_for_lead or _read_thread_messages was the one
                # gap. Same isolation now applies here.
                try:
                    repo.insert_error({
                        "stage": "instagram_reply_check", "channel": "instagram",
                        "account_id": account["id"], "error_message": str(exc), "is_expected": False,
                    })
                except Exception:  # noqa: BLE001 -- logging itself must never crash this run
                    pass
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
    Real gap fixed 2026-09-07: Reply Here only ever displayed
    OutreachMessage rows (platform-originated sends) and OutreachReply
    rows (detected incoming replies) -- a message sent manually from the
    real Instagram app, outside this platform, was correctly excluded
    from being mistaken for a reply, but that also meant it was invisible
    on the dashboard entirely, even though it's a genuine part of the
    real conversation.

    Dedup by CONTENT within each direction, not a stable message id --
    Instagram's DOM exposes no per-message identifier to key off (see
    _read_thread_messages' own docstring), so a body already known on the
    matching side is treated as already-recorded. Known real limitation:
    two literally-identical messages on the same side (e.g. sending "K"
    twice) are indistinguishable this way and the second one won't be
    backfilled -- accepted the same way the single-newest-reply version
    of this function already accepted it for the incoming side alone.

    Compares NORMALIZED text (collapsed whitespace, see
    linkedin_reply_check._normalized_for_dedup's own comment for the real
    bug this closes -- "identical logic" per this function's own docstring
    above means this file inherited the exact same one): our stored body
    keeps real paragraph breaks, but the live DOM can render the identical
    message as one flat run with no line breaks at all, which read as "a
    new message" on every poll and silently inserted a duplicate DB row
    for a message that was only ever really sent once.

    REAL BUG FOUND AND FIXED 2026-09-16 -- direction used to arrive
    pre-decided on each `live_messages` entry (`msg["from"]`), computed by
    _read_thread_messages() from bubble position alone. See that
    function's own docstring for the live-confirmed false-positive this
    caused on 5 real leads. Direction is now decided HERE, per bubble,
    anchored to CONTENT already known to be ours or the lead's:

      1. A bubble whose normalized text is already in `known_outgoing` (a
         body we already have on file for this lead) or `known_incoming`
         (a reply we already recorded) is that side, full stop -- no
         position involved, and this is also what makes the existing
         dedup-by-content below a no-op for anything already on file.
      2. A genuinely NEW bubble (matches neither known set) gets a
         position-based guess whenever THIS READ already has at least one
         bubble it could content-match to `known_outgoing` (our own sent
         message) -- `confirmed_us_lefts` below. The guess compares the
         new bubble's `left` against the average `left` of those
         confirmed-"us" bubbles.
      3. Otherwise (this read has no bubble it can content-match at all --
         e.g. a stale/empty page load) direction is UNKNOWN and the bubble
         is skipped -- neither recorded as a reply nor backfilled.

    REAL BUG FOUND AND FIXED 2026-09-19: step 2 used to ALSO require
    `known_incoming` to be non-empty (a reply already confirmed on a
    PRIOR run) before ever trusting a position-based guess -- reasoned as
    "the thread is confirmed two-sided". That reasoning silently excluded
    the single most common real case: a lead's FIRST EVER reply, which by
    definition happens while known_incoming is still empty. Two real
    leads (fadeltradingcompany, titus.logistics) each sent a real first
    reply on Instagram that this exact gate skipped outright -- confirmed
    live, zero rows in outreach_replies for either despite a real reply
    screenshot from the owner. Every lead's first reply hit this same
    silent gap, every time, since that is always the moment
    known_incoming is empty. The real risk the original fix protected
    against -- a thread with ONLY our own messages having no genuine
    "left" cluster to average against -- is fully covered by requiring
    confirmed_us_lefts (content-matched "us" bubbles from THIS read)
    to be non-empty; requiring a PRIOR confirmed reply on top of that
    added no extra safety, it only ever cost the first-reply case.

    Returns (new_replies_recorded, new_outgoing_backfilled).
    """
    def _normalized(text: str) -> str:
        return "".join((text or "").split())

    known_incoming = {_normalized(r.get("body")) for r in repo.replies_for_lead(lead["id"])}
    known_outgoing = {
        _normalized(m.get("edited_body") or m.get("body"))
        for m in repo.messages_for_lead(lead["id"])
        if m.get("channel") == "instagram"
    }
    # "us" lefts among bubbles THIS READ can already attribute by content --
    # the calibration anchor for step 2 above, computed fresh each call
    # since it only ever needs bubbles from the current live read. This
    # alone is the real safety condition (see 2026-09-19 fix note above):
    # a PRIOR confirmed reply is no longer also required.
    confirmed_us_lefts = [
        b["left"] for b in live_messages if _normalized(b["text"]) in known_outgoing and "left" in b
    ]

    new_replies = 0
    new_outgoing = 0
    for msg in live_messages:
        text = msg["text"]
        normalized = _normalized(text)

        if normalized in known_outgoing:
            direction = "us"
        elif normalized in known_incoming:
            direction = "lead"
        elif confirmed_us_lefts and "left" in msg:
            avg_us_left = sum(confirmed_us_lefts) / len(confirmed_us_lefts)
            direction = "lead" if msg["left"] < avg_us_left else "us"
        else:
            # No content match and no safe anchor to guess from -- skip
            # rather than risk fabricating a reply from our own text.
            continue

        if direction == "lead":
            if normalized in known_incoming:
                continue
            handle_reply_detected(
                lead["id"],
                channel="instagram",
                body=text,
                replied_at=dt.datetime.now(dt.timezone.utc),
                account_id=account["id"],
            )
            known_incoming.add(normalized)
            new_replies += 1
        else:
            if normalized in known_outgoing:
                continue
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            repo.insert_message({
                "lead_id": lead["id"],
                "channel": "instagram",
                "body": text,
                "approval_status": "approved",
                "approved_at": now_iso,
                "send_status": "sent",
                "sent_at": now_iso,
                "sent_via_account": account["id"],
            })
            known_outgoing.add(normalized)
            new_outgoing += 1

    return new_replies, new_outgoing
