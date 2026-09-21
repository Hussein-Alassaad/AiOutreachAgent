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

# HARD floors, added 2026-09-13 after a real run saved "ETECH Estimating"
# (2 LinkedIn followers) and "Housekeeping Club" (11) as leads. Both scored
# well above the pass threshold because a tiny audience only cost -1 while a
# bio (+2) and a website (+2) earned 4 -- the penalty was never decisive.
# A company page nobody follows and that posts nothing is not a real
# prospect: outreach to it reaches no one and wastes a daily send slot.
#
# INSTAGRAM ONLY, deliberately. There is no LinkedIn equivalent of this
# constant and one must not be re-added: on LinkedIn this same
# `follower_or_headcount` field carries the company's EMPLOYEE COUNT, not a
# follower count (linkedin.extract_company_profile fills it from
# _parse_headcount("51-200 employees")). A LinkedIn "follower floor" of 100
# therefore silently became a 100-EMPLOYEE floor that overrode the owner's
# own configured company-size minimums and rejected every 51-99 employee
# company -- exactly Insurance's target band. LIVE-CONFIRMED 2026-09-13.
# LinkedIn company size is enforced per-tenant by scheduler's
# min_company_size check, which is where it belongs.
MIN_INSTAGRAM_FOLLOWERS_HARD = 300

# Owner's steer 2026-09-13: "presence is more important than the
# followers". A page with a modest audience that actually posts and has a
# real website is a better prospect than a bigger but dormant one, so
# PRESENCE is its own hard requirement rather than a few soft points that a
# bio and a website could outweigh (which is exactly how "ETECH
# Estimating", 2 followers, scored 6/10 and got saved as a lead).
#
# Real presence = the page is actually alive: it posts, and it has either a
# website or a real description. A page failing this is a shell regardless
# of follower count.
MIN_POSTS_FOR_REAL_PRESENCE = 1

# Added 2026-09-13 (real owner rule: "the bio should have signals that it is
# a business or company... website point is good... need to see 9/10
# qualified leads"). Real overnight failures this closes: drfood.worldwide
# (2M followers, bio gave zero clue what it actually sells), c.big.moe
# (123K followers, same problem), ab.art.pro (classified "art education" --
# unclear if it's a school, a shop, or one artist's page). All three had
# a bio and passed every existing check; nothing here actually asked
# "can a reader tell what this business DOES or SELLS."
#
# This is a CONCRETE-OPERATION vocabulary: words that describe a real,
# ongoing commercial activity -- selling, serving, delivering, installing,
# manufacturing something -- as opposed to a bio that's just a tagline,
# hashtags, or an emoji with no stated business activity at all. This is
# NOT the same list as qualify's personal-detection trade words -- this one
# is deliberately generic (verbs and business-shape nouns any real company
# uses) rather than industry-specific, since it has to work across every
# tenant's completely different target industries.
_CONCRETE_BUSINESS_SIGNALS = [
    # what a company DOES
    "we sell", "we offer", "we provide", "we supply", "we deliver",
    "we install", "we manufacture", "we build", "we design", "we create",
    "we specialize", "we specialise", "we serve", "we distribute",
    "we import", "we export", "we produce",
    # what a company IS / how it's structured
    "founded in", "established in", "since 19", "since 20", "family owned",
    "family-owned", "years of experience", "years in business",
    "our services", "our products", "our clients", "our customers",
    "order now", "shop now", "book now", "contact us", "visit us",
    "delivery available", "free delivery", "wholesale", "retail",
    "showroom", "branch", "branches", "locations",
]


def _bio_names_concrete_business(bio: str) -> bool:
    """
    True when the bio actually says what the business DOES or SELLS, not
    just that a page exists. See _CONCRETE_BUSINESS_SIGNALS' comment for the
    real leads (drfood.worldwide, c.big.moe, ab.art.pro) this is built to
    reject -- each had a bio, a website field, and heavy activity, but no
    reader could tell what the business actually does from the bio alone.

    Checks TWO independent things, either one is enough: an explicit phrase
    ("we sell", "order now") OR a plain product/service NOUN
    (_BUSINESS_TRADE_WORDS -- furniture, logistics, jewelry, cafe, etc.).
    LIVE-CAUGHT 2026-09-13: phrase-matching alone rejected real plain bios
    like "Cedar Furniture, Beirut" and "Sparrow Carriers - trusted shipping
    and delivery" -- neither uses "we sell" framing, but both plainly name
    a real product/service the moment a trade noun is allowed to count too.

    Deliberately a SOFT signal (contributes to score, checked alongside
    _mentions_business_word), not a hard reject on its own -- a real small
    business's bio can be as plain as "Cedar Furniture, Beirut" with none of
    these exact phrases, and the owner's own instruction is not to be "very
    very strict."
    """
    lowered = bio.lower()
    if any(phrase in lowered for phrase in _CONCRETE_BUSINESS_SIGNALS):
        return True
    tokens = [t for t in re.split(r"[^a-z]+", lowered) if t]
    return any(t in _BUSINESS_TRADE_WORDS or t in _FOOD_RETAIL_TRADE_WORDS for t in tokens)


# A LIGHT signal only, per the owner's explicit instruction not to be very
# strict on location -- a company doesn't need to advertise its address to
# be real, and requiring this would reject genuine businesses that just
# don't mention geography in their bio. Contributes a small amount to score,
# never a hard reject on its own.
_LOCATION_SIGNAL_WORDS = [
    "lebanon", "beirut", "tripoli", "sidon", "saida", "tyre", "sour",
    "jounieh", "byblos", "jbeil", "zahle", "baabda", "achrafieh",
    "hamra", "dbayeh", "jal el dib", "antelias", "bourj hammoud",
    "based in", "located in", "serving all of", "across lebanon",
    "nationwide delivery",
]


def _mentions_a_location(bio: str) -> bool:
    """Light positive signal only -- see _LOCATION_SIGNAL_WORDS' comment."""
    lowered = bio.lower()
    return any(word in lowered for word in _LOCATION_SIGNAL_WORDS)

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

# The short entries above are only safe as WHOLE tokens, never as bare
# substrings. LIVE-CONFIRMED 2026-09-13: "co" matched inside ali.coach,
# nour.cosmetics, mike.cooking, sara.content, rami_coder, sami.coffee and
# every other handle merely containing those two letters -- and because a
# business word short-circuits _looks_like_personal_handle() at its first
# check, each of those individuals bypassed the Instagram individual filter
# entirely. That is precisely the "for instagram, we should try to not get
# individuals" complaint. "inc" had the same flaw (vince, prince, sincere).
# Longer entries stay substring-matched: they are distinctive enough that an
# accidental match isn't realistic, and substring matching is what lets
# "anthony.security.systems"-style compound handles still read as businesses.
_SHORT_BUSINESS_WORDS = {w for w in _BUSINESS_WORDS if len(w) <= 3}
_LONG_BUSINESS_WORDS = _BUSINESS_WORDS - _SHORT_BUSINESS_WORDS


def _mentions_business_word(text: str) -> bool:
    """
    True when `text` contains a business-identifying word.

    Long words match anywhere; short ones ("co", "gym", "inc", "llc") must
    appear as a separate token, split on the separators handles and display
    names actually use. See _SHORT_BUSINESS_WORDS' comment for the real bug
    this prevents.
    """
    lowered = text.lower()
    if any(word in lowered for word in _LONG_BUSINESS_WORDS):
        return True
    tokens = [t for t in re.split(r"[^a-z]+", lowered) if t]
    return any(t in _SHORT_BUSINESS_WORDS for t in tokens)


# Common given names seen on real Lebanese/Arabic Instagram handles. Used
# only as ONE signal among several (see _looks_like_personal_handle) --
# "anthony.security.systems" contains a first name but is plainly a
# business, so a name alone never decides it.
_PERSONAL_NAME_TOKENS = {
    "anthony", "mike", "michael", "ali", "ahmad", "ahmed", "mohamad", "mohammad",
    "mohammed", "muhammad", "hassan", "hussein", "omar", "khaled", "karim",
    "rami", "sami", "fadi", "george", "joseph", "elie", "charbel", "tony",
    "maya", "rana", "nour", "sara", "sarah", "lara", "dana", "jana", "zeina",
    "moustapha", "mustafa", "mahmoud", "jaafer", "jaafar", "youssef", "yousef",
    "bilal", "walid", "ziad", "nabil", "samir", "tarek", "wassim", "marwan",
    "abdallah", "abdullah", "ibrahim", "jad", "rayan", "adam", "adnan",
}

# Handle fragments that signal a personal/creator account rather than a
# company -- an individual's own page, not a business's.
_PERSONAL_HANDLE_MARKERS = {
    "official_", "_official", "the.real", "therealj", "itsme", "its_",
    "im_", "i.am", "iam_", "mr.", "mr_", "mrs.", "mrs_", "dr.", "dr_",
    "coach", "influencer", "blogger", "vlog", "creator", "artist",
    "photographer", "designer", "freelance", "personal",
}

# What a business DOES. Distinct from _BUSINESS_WORDS (which is what a
# business IS -- "llc", "company", "studio"): a founder very often names the
# page after themselves plus their trade, e.g. "anthony.security.systems".
# Without this, the given-name check below flags every such handle as an
# individual -- a false positive that silently costs a real lead, and the
# exact case this function's own docstring promises not to get wrong.
# LIVE-CONFIRMED 2026-09-13: "anthony.security.systems" was being rejected.
#
# Deliberately EXCLUDED, do not add: fitness, beauty, cosmetics, sports,
# academy, training, marketing, media, digital, fashion, consult. Those are
# exactly the categories individual creators name themselves after
# ("anthony_fitness", "nour.cosmetics" -- verified: adding them flipped both
# of those from correctly-flagged individuals to businesses). Every word
# here must be one a person would NOT use for a personal brand.
_BUSINESS_TRADE_WORDS = {
    "security", "systems", "system", "tech", "technology", "technologies",
    "electric", "electrical", "electronics", "engineering", "contracting",
    "construction", "trading", "traders", "import", "export", "supply",
    "supplies", "equipment", "industrial", "industries", "manufacturing",
    "furniture", "decor", "interiors", "kitchen", "bakery", "catering",
    "travel", "tours", "logistics", "transport", "shipping", "rental",
    "rentals", "motors", "auto", "garage", "pharma", "pharmacy", "medical",
    "dental", "optics", "insurance", "realestate", "properties", "property",
    "developers", "builders", "paints", "steel", "aluminum", "glass",
    "textiles", "jewelry", "watches",
    "school", "institute",
    "software", "solutions",
    "cctv", "cameras", "alarm", "networks", "telecom", "energy", "solar",
    "plumbing", "hvac", "cooling", "heating", "cleaning", "maintenance",
    "printing", "packaging", "plastics", "chemicals", "agriculture", "farms",
}

# A SEPARATE, narrower set from _BUSINESS_TRADE_WORDS above -- deliberately
# NOT merged into it. LIVE-CAUGHT 2026-09-13: adding "coffee"/"cafe" to the
# shared set broke _looks_like_personal_handle's own test suite --
# "sami.coffee" (a real personal food-content handle in that suite) started
# reading as a business the instant "coffee" became a business word there,
# for the identical reason "beauty"/"fitness" were kept out of that set
# (see its own comment): food/drink words are exactly what a food
# influencer names themselves after, so they're safe for judging BIO
# CONTENT (a company bio literally selling coffee) but unsafe for judging a
# HANDLE (a person's own coffee-content account). Used only by
# _bio_names_concrete_business, never by _looks_like_personal_handle.
_FOOD_RETAIL_TRADE_WORDS = {
    "cafe", "coffee", "restaurant", "bistro", "eatery", "diner",
    "grocery", "supermarket", "butcher", "seafood", "produce",
}


# Added 2026-09-13, real owner instruction: "no agencies only companies and
# businesses" (Zimmar/Insurance -- an agency doesn't have the physical
# premises/assets/insurable operation a CCTV install or an insurance policy
# is actually for; it's a service reseller, not the end operating business).
# Owner confirmed this means ANY agency type (marketing, ad, recruitment,
# real estate, etc.), not just marketing/creative agencies specifically.
# Matched as a whole word, not a bare substring -- "agency" alone would also
# match inside an unrelated word; checked against both the handle/display
# name and the bio.
_AGENCY_MARKERS = [
    "agency", "agencies", "marketing agency", "ad agency", "advertising agency",
    "creative agency", "digital agency", "recruitment agency", "staffing agency",
    "real estate agency", "realty agency", "modeling agency", "talent agency",
    "pr agency", "media agency", "branding agency", "design agency",
]


def _is_agency(display_name: str, bio: str) -> bool:
    """
    True when the handle/display name or bio marks this as an agency-type
    business (reseller/service-provider) rather than an operating company
    with its own premises, assets, or insurable operation -- see
    _AGENCY_MARKERS' own comment for the real owner instruction this
    implements. Whole-word matching via regex \\b so "agency" doesn't
    accidentally match inside an unrelated longer word.
    """
    haystack = f"{display_name or ''} {bio or ''}".lower()
    return any(re.search(r"\b" + re.escape(marker) + r"\b", haystack) for marker in _AGENCY_MARKERS)


# Real personal/creator bios talk in the first person about THEMSELVES;
# a company bio almost never does ("Beirut's #1 furniture showroom" vs "Hi,
# I'm Sara! Welcome to my page"). Added 2026-09-13: the owner's rule is "no
# individuals allowed, even if famous" -- handle-shape matching alone
# (_looks_like_personal_handle) only catches a name pattern in the
# @username itself, so a famous person's own branded handle
# (@meteorintheyks, @c.big.moe, @lets_travel_and_discover -- all real
# accounts a live run saved as "leads") slips through with a big, real
# follower count and no name-pattern match at all. Bio phrasing is a second,
# independent signal that catches exactly that gap: a lifestyle/personal
# account describes a PERSON, a company bio describes a BUSINESS.
_PERSONAL_BIO_PHRASES = [
    "i'm ", "i am ", "my page", "my account", "my journey", "my life",
    "welcome to my", "follow my", "here to share my", "sharing my",
    "content creator", "influencer", "blogger", "vlogger", "youtuber",
    "personal page", "personal account", "my travels", "my adventures",
    "documenting my", "this is my",
]


def _bio_reads_personal(bio: str) -> bool:
    """
    True when the bio's own wording describes a person rather than a
    business -- see _PERSONAL_BIO_PHRASES' comment for the real gap this
    closes. A business bio containing a genuine business word still wins
    (same "business word always overrides" posture as
    _looks_like_personal_handle), since "I'm the founder of Acme Security
    Systems" is a business account despite the first-person opener.
    """
    lowered = bio.lower()
    # Checks BOTH business-word sets: _BUSINESS_WORDS ("studio", "company")
    # AND _BUSINESS_TRADE_WORDS ("security", "systems", "logistics") -- a
    # bio saying "I'm the founder of Acme Security Systems" needs the trade
    # words too, since a bio talks about what the business DOES far more
    # often than a handle does. _mentions_business_word() alone only checks
    # the first set, which is why this is spelled out here instead of
    # reusing it directly.
    if _mentions_business_word(lowered):
        return False
    tokens = [t for t in re.split(r"[^a-z]+", lowered) if t]
    if any(t in _BUSINESS_TRADE_WORDS for t in tokens):
        return False
    return any(phrase in lowered for phrase in _PERSONAL_BIO_PHRASES)


def _looks_like_personal_handle(display_name: str) -> bool:
    """
    True when an INSTAGRAM handle reads as an individual's own account
    rather than a business page.

    LIVE-CONFIRMED 2026-09-13: _looks_like_personal_name() below never
    fired on a single real Instagram lead, because it expects a Title-Case
    "Firstname Lastname" display name and Instagram gives us the @username
    instead (extract_profile() has no display_name field at all -- see its
    docstring). Real individuals that slipped through as "companies":
    anthony_elhachem, moustaphachaaban, gpt.mike, jaafer_3d. This checks
    the shape Instagram actually hands us.

    A business word anywhere in the handle always wins -- "anthony.security
    .systems" is a business regardless of the first name in it.
    """
    handle = display_name.strip().lower()
    if not handle:
        return False
    # "<word>_with_<word>" is checked BEFORE the business-word override.
    # LIVE-CAUGHT 2026-09-13: realestate_with_julia (a real estate agent's
    # personal content page, not a company) was passing as a business
    # because "realestate" is a genuine _BUSINESS_TRADE_WORDS entry --
    # correct for a company handle like "acme.realestate.lb", wrong here,
    # since the "_with_" shape is a stronger, more specific signal of a
    # personal brand than a trade word is of a company. A trade word
    # elsewhere in the handle still wins for every other shape.
    if re.search(r"[._-]with[._-]", handle):
        return True
    if _mentions_business_word(handle):
        return False
    if any(marker in handle for marker in _PERSONAL_HANDLE_MARKERS):
        return True
    # Split on the separators Instagram handles actually use.
    tokens = [t for t in re.split(r"[._\-0-9]+", handle) if t]
    # A trade word means this is a business, even though a founder's given
    # name sits in front of it ("anthony.security.systems"). Checked BEFORE
    # the given-name rule below, which would otherwise claim it first.
    if any(t in _BUSINESS_TRADE_WORDS for t in tokens):
        return False
    if any(t in _PERSONAL_NAME_TOKENS for t in tokens):
        return True
    # "firstnamelastname" run together with no separator at all (e.g.
    # moustaphachaaban) -- check whether any known given name prefixes it.
    if len(tokens) == 1:
        if any(handle.startswith(n) and len(handle) > len(n) + 2 for n in _PERSONAL_NAME_TOKENS):
            return True
    # (the "<word>_with_<word>" check moved to the top of this function --
    # see the comment there for why it must run before the business-word
    # override.)
    # Initials/name-parts shape ("mhd.haidar.ah"): 3+ short fragments AND at
    # least one that looks like an abbreviated given name. The name check is
    # required -- without it this also flagged "ab.art.pro", a real business,
    # which is the kind of false positive that silently costs a lead.
    if len(tokens) >= 3 and all(len(t) <= 6 for t in tokens):
        if any(any(n.startswith(t) for n in _PERSONAL_NAME_TOKENS) for t in tokens if len(t) >= 3):
            return True
    return False


def _looks_like_personal_name(display_name: str) -> bool:
    name = display_name.strip()
    if _mentions_business_word(name):
        return False
    if _PERSONAL_NAME_PATTERN.match(name):
        return True
    # An Instagram @username never matches the Title-Case pattern above, so
    # fall back to handle-shaped detection for those.
    return _looks_like_personal_handle(name)


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

    # LinkedIn is exempt from the personal-name check entirely -- SAME
    # structural reasoning the Instagram-only hard gate below already runs
    # on, just in the opposite direction: LinkedIn company search only ever
    # returns linkedin.com/company/ URLs, so a candidate reaching here from
    # LinkedIn is structurally incapable of being an individual, and there
    # is nothing for this check to catch. On Instagram (hashtag discovery,
    # which genuinely surfaces individuals) it stays exactly as strict as
    # before -- do NOT weaken it there.
    # LIVE-CONFIRMED 2026-09-18: _looks_like_personal_name() false-positives
    # on real company names that happen to be Two Title-Case Words without a
    # term from the small _BUSINESS_WORDS set -- "Orange Business", "Alfa
    # Telecommunications" and "Roman Foods" all returned True in tonight's
    # Insurance run, costing each a real -2 and producing 21 of the -9
    # scores in a single night.
    if profile.get("platform") == "linkedin":
        reasons.append(
            "Personal-name check skipped: LinkedIn company search only returns "
            "/company/ URLs, so this is structurally a company, not an individual"
        )
        score += 1
    elif _looks_like_personal_name(display_name):
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
    else:
        reasons.append("No website linked")
        score -= 1

    # Added 2026-09-13 -- see _bio_names_concrete_business's own comment for
    # the real leads (drfood.worldwide, c.big.moe, ab.art.pro) this closes.
    # Soft signal by design (owner: "do not be very very strict") -- a real
    # plain-spoken small business bio without one of these exact phrases
    # should not be hard-rejected, it just doesn't earn the bonus.
    if _bio_names_concrete_business(bio):
        reasons.append("Bio names a concrete product/service the business sells or does")
        score += 2
    else:
        reasons.append("Bio does not clearly say what the business sells or does")
        score -= 1

    # LIGHT signal only (owner: don't require this strictly -- a camera
    # install needs a real address, but plenty of genuine businesses just
    # don't mention geography in their bio). MJivity in particular can
    # legitimately target an online-only store or agency with no physical
    # premises at all, so this never costs points, only ever adds them.
    if _mentions_a_location(bio):
        reasons.append("Bio mentions a real operating location")
        score += 1

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

    bio_mentions_niche = _mentions_niche(bio, niche)
    if bio_mentions_niche:
        reasons.append("Bio mentions the target niche")
        score += 1
    else:
        reasons.append("Bio does not mention the target niche -- possible relevance miss")
        score -= 2

    qualifies = score >= 1

    # HARD quality floor on real audience size, added 2026-09-13 (real
    # complaint: "one of them has no posts or presence or high followers,
    # those should not be selected"). INSTAGRAM ONLY -- see
    # MIN_INSTAGRAM_FOLLOWERS_HARD's own comment for why there is
    # deliberately no LinkedIn counterpart here. Unknown counts are NOT
    # rejected: the scrape genuinely fails to read this field sometimes, and
    # rejecting on missing data would silently drop real companies.
    platform = profile.get("platform")
    if (
        qualifies
        and platform == "instagram"
        and follower_or_headcount is not None
        and follower_or_headcount < MIN_INSTAGRAM_FOLLOWERS_HARD
    ):
        reasons.append(
            f"Hard reject: only {follower_or_headcount} followers on Instagram "
            f"(minimum {MIN_INSTAGRAM_FOLLOWERS_HARD}) -- too little real presence to be worth contacting."
        )
        qualifies = False

    # HARD floor on REAL PRESENCE -- the owner's own priority ("presence is
    # more important than the followers"). Two independent requirements:
    #
    #   (a) the page actually posts. Previously only a -2 score penalty,
    #       which a bio + website (+4 combined) comfortably outweighed.
    #   (b) the page is identifiable as a real business at all -- it has
    #       EITHER a linked website OR a genuine description. A page with
    #       neither is a placeholder someone registered and abandoned, no
    #       matter how many followers it accumulated.
    #
    # Unknown post_count is not punished (the scrape genuinely fails to read
    # it on some pages) -- same reasoning as the unknown-follower case above.
    if qualifies and post_count is not None and post_count < MIN_POSTS_FOR_REAL_PRESENCE:
        reasons.append(
            f"Hard reject: {post_count} posts -- no real presence, reaching out here reaches nobody."
        )
        qualifies = False

    has_real_description = len(bio.strip()) >= MIN_MEANINGFUL_BIO_LENGTH
    if qualifies and not has_website and not has_real_description:
        reasons.append(
            "Hard reject: no website and no real description -- nothing identifies this as "
            "a real, operating business."
        )
        qualifies = False

    # NARROW hard reject, added 2026-09-13 (owner: "the bio should have
    # signals that it is a business or company... need to see 9/10
    # qualified leads"). The check just above only asks whether a bio is
    # LONG ENOUGH to count as "real" -- it never asks whether that bio
    # actually says anything concrete. drfood.worldwide (2M followers),
    # c.big.moe (123K), and ab.art.pro all had a long-enough bio, no
    # website, heavy activity, and still passed every existing check,
    # because nothing here ever asked "can a reader tell what this
    # business DOES or SELLS."
    #
    # Deliberately narrow -- only fires when BOTH no website AND the bio
    # names no concrete business activity. A real business with neither of
    # those two signals at once essentially never happens; a content/
    # lifestyle/personal-brand account with big-but-meaningless followers
    # constantly does. This is the specific combination, not a general
    # "vague bio" penalty, per the owner's explicit "do not be very very
    # strict" instruction -- the earlier soft score penalty for a vague bio
    # (a few lines up) already covers everything short of this.
    if qualifies and not has_website and not _bio_names_concrete_business(bio):
        reasons.append(
            "Hard reject: no website and the bio never says what this business actually "
            "sells or does -- reads like a content/lifestyle page, not an operating company."
        )
        qualifies = False

    # Dormant page: it has posts historically but nothing recent. Real
    # companies worth contacting are still active; a page last touched years
    # ago usually means nobody is reading its messages either.
    if qualifies and post_count is not None and post_count > 0 and not recent_activity:
        reasons.append(
            "Hard reject: has posts but no recent activity -- page looks dormant, "
            "an outreach message here likely reaches nobody."
        )
        qualifies = False

    # Instagram-only hard gate, found missing in the 2026-09-12 review:
    # hashtag-based discovery (instagram.py) has no way to distinguish "a
    # real business posted under this tag" from "a random individual did"
    # -- unlike LinkedIn, whose company search only ever returns
    # linkedin.com/company/ URLs in the first place. The soft score above
    # let plenty of personal accounts through (a decent bio + a website
    # alone was enough to clear score >= 1 despite the -2 personal-name
    # penalty). This adds a genuine hard reject when SEVERAL "looks
    # personal, not business" signals stack up at once, rather than
    # raising the global threshold for every platform (which would also
    # reject thin-but-real small businesses on LinkedIn). A real niche
    # mention overrides this gate -- a business that explicitly talks about
    # the target niche in its bio has already shown the one signal that
    # matters most, regardless of how thin the rest of its profile is.
    if profile.get("platform") == "instagram" and qualifies:
        # TIGHTENED 2026-09-13. The previous version required personal-name
        # AND thin-bio AND zero-posts all at once, which in practice never
        # fired: a real individual posts plenty and writes a bio, so
        # post_count == 0 was almost never true. Result was that
        # anthony_elhachem, gpt.mike, jaafer_3d and moustaphachaaban all
        # came through as "companies" in a real run. A handle that reads as
        # a person is now decisive on its own -- Zimmar and MJivity both
        # sell to BUSINESSES, so an individual's account is the wrong
        # target no matter how active or well-written it is.
        # _looks_like_personal_handle deliberately returns False the moment
        # a business word appears in the handle, so this can't reject
        # "anthony.security.systems".
        if _looks_like_personal_name(display_name):
            reasons.append(
                f"Instagram hard reject: handle {display_name!r} reads as an individual's "
                "account, not a business page."
            )
            qualifies = False
        # SECOND, INDEPENDENT signal added 2026-09-13: handle-shape alone
        # missed real individuals with no name-pattern in their @username at
        # all -- meteorintheyks, c.big.moe, lets_travel_and_discover were all
        # saved as "leads" in a real live run despite large real follower
        # counts, precisely because nothing about the username itself looked
        # personal. The owner's rule is "no individuals allowed, even if
        # famous" -- a famous person's own account can have any handle, but
        # their bio still talks about a PERSON. See _bio_reads_personal.
        elif _bio_reads_personal(bio):
            reasons.append(
                f"Instagram hard reject: bio reads as a personal/lifestyle account "
                f"({bio.strip()[:60]!r}), not a business page."
            )
            qualifies = False

    reasons.append(f"Final score: {score} -> {'QUALIFIES' if qualifies else 'SKIP'}")
    return qualifies, reasons
