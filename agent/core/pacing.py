"""
Randomized delays between browser actions, so automated clicking/typing
doesn't have the instant, identical-every-time rhythm that's a real bot
fingerprint (a genuine human never clicks a button 0ms after a page loads,
or types a 200-character message in a single instant fill).

Used by every LinkedIn/Instagram browser-automation module before a
click/fill on something the platform is likely watching (opening a message
composer, sending). Not used for purely internal waits (e.g. waiting for a
selector to exist) -- those already have their own explicit timeouts and
adding randomness there wouldn't fool anything, only slow down debugging.
"""

from __future__ import annotations

import random
import time


def human_delay(min_seconds: float = 0.8, max_seconds: float = 2.4) -> None:
    """Block for a random, human-scale pause before the next action."""
    time.sleep(random.uniform(min_seconds, max_seconds))


def human_type(locator, text: str) -> None:
    """
    Fill a field character-by-character with small random per-character
    delays, instead of Playwright's default .fill() which sets the whole
    value in one instant DOM write -- the instant-fill pattern is itself a
    detectable signal, real typing has per-keystroke timing variance.

    REAL BUG FOUND AND FIXED 2026-09-16 -- the "one DM arrived as four
    separate messages" incident. A lead (mik.export) received Zimmar's
    Instagram template as FOUR distinct message bubbles, all stamped
    11:31:49, one per paragraph -- it reads as spam to the recipient.

    Root cause: this function fed EVERY character to press_sequentially(),
    including the "\\n" characters separating the template's paragraphs.
    press_sequentially() types a newline as a real Enter keypress, and in
    Instagram's (and LinkedIn's) chat composer bare Enter means SEND. So a
    body with three "\\n\\n" paragraph breaks sent the text typed so far,
    then kept typing into the now-empty composer, and the final Send click
    flushed the last paragraph -- four real, separately-delivered messages
    from one approved body.

    Fix: newlines are never typed as characters. The text is split on
    "\\n" and each segment is typed normally; between segments an explicit
    Shift+Enter inserts a soft line break, which every one of these web
    composers treats as "new line, do NOT send". A blank line between
    paragraphs ("\\n\\n") is two consecutive newlines, which yields two
    Shift+Enter presses, so the visual paragraph spacing the owner
    approved is preserved exactly rather than collapsed to a single break.

    Safe for single-line fields too (login email/password via
    core/session.py): a string with no "\\n" produces exactly one segment
    and zero Shift+Enter presses, i.e. the identical behavior as before.
    """
    locator.click()
    # splitlines() is deliberately NOT used -- it also splits on \r, \v,
    # \x0b,   and friends, which would silently turn an exotic
    # character inside a real message body into an extra line break. Only
    # a literal \n is a paragraph break in these templates.
    segments = text.split("\n")
    for index, segment in enumerate(segments):
        if index > 0:
            # One Shift+Enter per newline that was in the original text,
            # so "\n\n" stays a blank line rather than collapsing.
            locator.press("Shift+Enter", delay=random.uniform(20, 90))
        for char in segment:
            locator.press_sequentially(char, delay=random.uniform(20, 90))
