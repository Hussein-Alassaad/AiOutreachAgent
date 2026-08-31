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
    """
    locator.click()
    for char in text:
        locator.press_sequentially(char, delay=random.uniform(20, 90))
