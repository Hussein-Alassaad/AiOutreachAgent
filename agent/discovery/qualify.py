"""
Decides whether a discovered profile is a real business worth pursuing.

A reasoning decision, not a single keyword filter: several independent signals
are weighed together, and the result comes with the reasons that drove it, not
just a bare yes/no. Skips personal accounts, inactive pages, anything with no
clear product or service, and obvious niche misses.

This is Phase 3's CHEAP first-pass filter, run on every discovered profile
before it's worth spending anything on Phase 4's deep Claude analysis. It does
not call any AI model -- Phase 4 is where the AI-powered deep dive happens,
once a Claude API key exists. Everything here is deterministic Python logic,
which also means it's fully testable today with sample data, with no live
LinkedIn/Instagram session required.

Both discovery/linkedin.py and discovery/instagram.py normalise their raw
scraped fields into this common shape before calling qualify_profile(), so
this module never needs to know which platform a profile came from:

    {
        "platform": "linkedin" | "instagram",
        "display_name": str,
        "bio": str,               # LinkedIn company description / IG bio
        "has_website": bool,
        "post_count": int | None,
        "recent_activity": bool,  # posted within roughly the last month
        "follower_or_headcount": int | None,
    }
"""

from __future__ import annotations

import re

# Below this, a profile needs another strong signal (bio, website) to pass --
# on its own, a low count isn't disqualifying (a brand-new real business looks
# exactly like this), it just isn't enough evidence by itself.
MIN_FOLLOWER_OR_HEADCOUNT = 20

# A bio shorter than this reads as "empty" for scoring purposes, even if it's
# not literally zero characters (e.g. just an emoji or a single word).
MIN_MEANINGFUL_BIO_LENGTH = 12

# Two Title-Case words and nothing else ("Sarah Khalil") is the classic shape
# of a personal name, as opposed to a business name ("Khalil Fitness Studio").
_PERSONAL_NAME_PATTERN = re.compile(r"^[A-Z][a-z]+\s+[A-Z][a-z]+$")

# Words that flip a personal-looking name back into a business one --
# "Sarah Khalil Studio" no longer matches the plain two-word pattern anyway,
# but this also catches cases like "Sarah Khalil | Photography".
_BUSINESS_WORDS = {
    "studio", "agency", "clinic", "gym", "salon", "boutique", "restaurant",
    "cafe", "shop", "store", "group", "consulting", "solutions", "official",
    "co", "company", "inc", "llc", "services",
}


def _looks_like_personal_name(display_name: str) -> bool:
    name = display_name.strip()
    if any(word in name.lower() for word in _BUSINESS_WORDS):
        return False
    return bool(_PERSONAL_NAME_PATTERN.match(name))


def _mentions_niche(bio: str, niche: str) -> bool:
    """
    True if the bio contains at least one meaningful (3+ letter) word from
    the target niche. An empty niche always passes -- Phase 1 seeds
    `settings.target_niche` empty by default, meaning "no filter configured
    yet", and an empty niche should never reject every profile from day one.

    This is a plain keyword check, not semantic matching -- a legitimately
    on-niche bio that just phrases things differently (e.g. niche "bakery",
    bio "artisan sourdough breads daily") won't match. That's a real, known
    limitation of doing this deterministically without a live account or a
    Claude call, which this module deliberately avoids (see module docstring)
    -- it's why a miss costs points rather than being an instant reject.
    """
    words = [w for w in re.findall(r"[a-zA-Z]+", niche.lower()) if len(w) > 2]
    if not words:
        return True
    bio_lower = bio.lower()
    return any(word in bio_lower for word in words)


def qualify_profile(profile: dict, niche: str = "") -> tuple[bool, list[str]]:
    """
    Decide whether a normalised profile dict is worth pursuing.

    `niche` is the dashboard-configured target niche (settings.target_niche);
    pass "" (the default) to skip the relevance check entirely, which is also
    what happens automatically when no niche is configured yet.

    Returns (qualifies, reasons) -- reasons lists every signal that
    contributed to the decision, in the order they were checked, so a skipped
    profile's record shows exactly why, not just a bare rejection.
    """
    reasons: list[str] = []
    score = 0

    display_name = profile.get("display_name") or ""
    bio = profile.get("bio") or ""
    has_website = bool(profile.get("has_website"))
    post_count = profile.get("post_count")
    recent_activity = bool(profile.get("recent_activity"))
    follower_or_headcount = profile.get("follower_or_headcount")

    if _looks_like_personal_name(display_name):
        reasons.append(f"Name '{display_name}' matches a personal-name pattern")
        score -= 2
    else:
        reasons.append("Name does not match a personal-name pattern")
        score += 1

    if len(bio.strip()) >= MIN_MEANINGFUL_BIO_LENGTH:
        reasons.append("Has a descriptive bio/description")
        score += 2
    else:
        reasons.append("Bio is missing or too short to describe a business")
        score -= 1

    if has_website:
        reasons.append("Has a linked website")
        score += 2

    if post_count is not None and post_count == 0:
        reasons.append("Zero posts -- likely inactive or a placeholder account")
        score -= 2
    elif not recent_activity:
        reasons.append("No recent activity detected")
        score -= 1
    else:
        reasons.append("Shows recent activity")
        score += 1

    if follower_or_headcount is not None and follower_or_headcount < MIN_FOLLOWER_OR_HEADCOUNT:
        reasons.append(
            f"Follower/headcount count ({follower_or_headcount}) is low on its own"
        )
        score -= 1

    if _mentions_niche(bio, niche):
        reasons.append("Bio mentions the target niche")
        score += 1
    else:
        reasons.append("Bio does not mention the target niche -- possible relevance miss")
        score -= 2

    qualifies = score >= 1
    reasons.append(f"Final score: {score} -> {'QUALIFIES' if qualifies else 'SKIP'}")
    return qualifies, reasons
