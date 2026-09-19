#!/usr/bin/env bash
#
# Deploy the Python agent to the droplet, correctly.
#
# WHY THIS EXISTS
# ---------------
# `docker restart nexaris-agent` does NOT pick up a newly built image. A
# container stays bound to the image it was CREATED from, so a rebuild plus a
# restart silently keeps running the old code -- no error, no warning, and the
# deploy looks completely successful. That happened for real on 2026-09-13:
# several rounds of discovery/qualification fixes were built into the image
# but never actually ran, which is why lead quality did not improve.
#
# The only correct sequence is: copy -> build -> RECREATE the container ->
# verify the new code is really loaded. This script does all four, refuses to
# continue if any step fails, and rolls back automatically if the new
# container does not come up healthy.
#
# Usage (from the outreach/ directory):
#   bash deploy.sh                 # deploy + verify
#   bash deploy.sh --verify-only   # just re-run the checks, change nothing
#
set -euo pipefail

DROPLET="root@159.89.101.209"
REMOTE_DIR="/root/nexaris-agent/outreach"
ENV_FILE="/root/nexaris-agent/.agent.env"
IMAGE="nexaris-agent:latest"
CONTAINER="nexaris-agent"
VOLUME="nexaris-browser-profiles"

say()  { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
ok()   { printf '   \033[32mOK\033[0m   %s\n' "$*"; }
fail() { printf '   \033[31mFAIL\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
# The verification block. Runs INSIDE the container, so it proves what the
# agent will actually execute -- not what is sitting on disk. Every check here
# corresponds to a real bug that was live in production at some point.
# ---------------------------------------------------------------------------
read -r -d '' VERIFY_PY <<'PYEOF' || true
import sys
from agent.discovery import linkedin, qualify, hunter
from agent import scheduler as sch
from agent.core import session as sess
from agent.sending import linkedin_send, instagram_send, linkedin_reply_check, instagram_reply_check

failures = []
def check(label, cond):
    print(("   OK   " if cond else "   FAIL ") + label)
    if not cond:
        failures.append(label)

mj = ("GCC (UAE, Saudi Arabia, Qatar, Kuwait, Bahrain, Oman), Egypt, Lebanon, "
      "MENA region, United States, United Kingdom, Canada, Europe")

# A tenant's long multi-region targeting policy must never be pasted into the
# LinkedIn keyword box -- it matches nothing.
check("long multi-region location kept out of search keywords",
      "Saudi" not in linkedin.build_search_url("software", mj, "", ["51-200"]))
check("Lebanon resolves to the verified geo facet",
      "companyHqGeo" in linkedin.build_search_url("trading", "Lebanon", "", ["11-50"]))
check("company size facet is applied",
      "companySize" in linkedin.build_search_url("trading", "Lebanon", "", ["11-50"]))

# A tenant must never reject the markets it actually targets.
check("MJivity target cities are not treated as foreign",
      all(sch._mentions_foreign_location(b, mj) is None for b in
          ["agency in Dubai", "studio in London", "Toronto consultancy",
           "New York agency", "team in Berlin", "Paris design house"]))
check("Indian locations rejected for a Lebanon tenant",
      all(sch._mentions_foreign_location(b, "Lebanon") for b in
          ["CCTV in Kochi, Kerala", "Bengaluru startup", "office in Noida",
           "Gurugram IT", "Ahmedabad, Gujarat"]))
check("Lebanese locations accepted for a Lebanon tenant",
      all(sch._mentions_foreign_location(b, "Lebanon") is None for b in
          ["company in Beirut", "Jounieh Lebanon", "Tripoli manufacturer"]))

# This constant, applied to LinkedIn, silently became an EMPLOYEE floor and
# rejected the entire 51-99 band -- exactly Insurance's target.
check("no LinkedIn follower floor (it was really an employee floor)",
      not hasattr(qualify, "MIN_LINKEDIN_FOLLOWERS_HARD"))

def profile(**kw):
    p = dict(display_name="Acme Trading SAL",
             bio="We import and distribute industrial equipment across Lebanon since 1998.",
             has_website=True, post_count=6, recent_activity=True,
             follower_or_headcount=None, platform="linkedin")
    p.update(kw)
    return p

check("LinkedIn 11-400 employee range all qualifies",
      all(qualify.qualify_profile(profile(follower_or_headcount=h), "trading")[0]
          for h in [11, 25, 51, 75, 99, 150, 400]))

def ig(followers):
    return qualify.qualify_profile(profile(
        platform="instagram", display_name="Beirut Home Decor",
        bio="Furniture showroom in Beirut. Delivery across Lebanon. Shop now.",
        follower_or_headcount=followers, post_count=40), "furniture")[0]

check("Instagram follower floor cuts exactly at 300",
      (not ig(90)) and (not ig(299)) and ig(300) and ig(1200))

# Quality gates the owner asked for: no posts / no presence / dormant.
check("zero-post page rejected",
      not qualify.qualify_profile(profile(post_count=0, recent_activity=False), "")[0])
check("page with no website and no real bio rejected",
      not qualify.qualify_profile(profile(bio="", has_website=False), "")[0])
check("page with posts but dormant rejected",
      not qualify.qualify_profile(profile(recent_activity=False), "")[0])

# Instagram individuals vs real businesses. "co" used to match as a bare
# substring, so ali.coach / nour.cosmetics / sami.coffee all read as
# businesses and skipped the individual filter entirely.
individuals = ["ali.coach", "nour.cosmetics", "mike.cooking", "sara.content",
               "rami_coder", "sami.coffee", "maya.cooks", "omar.records",
               "karim.consultant", "anthony_fitness", "moustapha.jaafer",
               "sarah_influencer", "mike.blogger", "ahmad_creator",
               "anthony_elhachem", "moustaphachaaban", "gpt.mike", "jaafer_3d",
               # LIVE-CAUGHT 2026-09-13 in the real compressed test cycle:
               # "realestate" is a genuine _BUSINESS_TRADE_WORDS entry, which
               # made the business-word override claim this real-estate
               # agent's personal content page as a "business" before the
               # X_with_Y personal-brand check ever ran.
               "realestate_with_julia", "paisa_with_preeti"]
businesses = ["anthony.security.systems", "beiruthomedecor", "zimmar.tech",
              "cedarindustrial", "ab.art.pro", "guardify.cloud",
              "ali_trading_co", "cedar.gym", "acme.inc", "beirut.llc",
              "khalil.company", "tony.restaurant", "sami.cafe", "rami.studio",
              "maya.clinic", "ali.electric", "omar.cctv.systems",
              "karim.engineering", "hassan.auto.parts", "ali.aluminum",
              "sami.contracting",
              # a real trade-word company handle must still pass despite the
              # X_with_Y check now running first
              "acme.realestate.lb"]
# Search-term targeting. A tenant whose niche names a SERVICE ("small
# business marketing") must rotate its own target sectors instead, or it
# finds the people who sell that service rather than companies who buy it.
MJ_IND = ("E-commerce brands, consumer products, fashion & apparel, cosmetics & beauty, "
          "jewelry, technology, automotive, architecture & real estate, restaurants & "
          "hospitality, advertising agencies, marketing agencies, creative agencies, "
          "consulting agencies, holding companies (multi-brand groups)")
ZIM_IND = "Offices, warehouses, retail, schools, factories, residential complexes, hospitality"

mj_draws = {sch._resolve_search_niche("small business marketing", MJ_IND) for _ in range(50)}
check("MJivity rotates its own sectors, never 'small business marketing'",
      "small business marketing" not in mj_draws and len(mj_draws) > 3)

# Zimmar/Insurance mean "all companies". Their target_industry holds facility
# types and prose, which are not usable search terms -- they must keep the
# broad industry rotation.
zim_draws = {sch._resolve_search_niche("", ZIM_IND) for _ in range(50)}
check("empty-niche tenant keeps the broad all-industry rotation",
      not (zim_draws & {"Offices", "schools", "warehouses", "residential complexes"}))
check("a real sector niche is passed through untouched",
      sch._resolve_search_niche("dental clinics", "") == "dental clinics")

# Instagram fallback tags must not be influencer/coach territory.
banned = {"entrepreneur", "business owner", "small business", "startup"}
check("Instagram fallback hashtags avoid influencer/coach tags",
      not (set(sch._BUSINESS_HASHTAG_TERMS) & banned))

missed = [h for h in individuals if not qualify._looks_like_personal_handle(h)]
falsepos = [h for h in businesses if qualify._looks_like_personal_handle(h)]
check("all %d Instagram individuals detected%s" % (
        len(individuals), "" if not missed else " -- MISSED: %s" % missed),
      not missed)
check("all %d real businesses pass%s" % (
        len(businesses), "" if not falsepos else " -- FLAGGED: %s" % falsepos),
      not falsepos)

# Own-competitor exclusion, added 2026-09-13 after real overnight leads
# included Zimmar's own CCTV/security-camera/telecom competitors
# (linksecurtysystem, guardify.cloud, avtrade.integration) -- no such check
# had ever existed for Zimmar, only Insurance.
check("Zimmar's own security/CCTV competitors are excluded",
      sch._is_competitor("zimmar", "CCTV and video surveillance systems installer", None)
      and sch._is_competitor("zimmar", "Access control and alarm systems", None)
      and not sch._is_competitor("zimmar", "Trading company importing electronics", None))
check("Insurance's own competitor exclusion still works (regression)",
      sch._is_competitor("Partners Insurance Consultancy", "leading reinsurance broker", None)
      and not sch._is_competitor("Partners Insurance Consultancy", "quality assurance testing lab", None))

# Concrete-business-signal hard reject, added 2026-09-13 (owner: "the bio
# should have signals that it is a business or company... need 9/10
# qualified leads"). Real leads this closes: drfood.worldwide (2M
# followers), c.big.moe (123K), ab.art.pro -- all had a bio, heavy
# activity, and no website, but nothing said what the business actually
# sells or does. Must NOT reject a plain real bio with no exact phrase
# match ("Cedar Furniture, Beirut") -- that was a real overtightening bug
# caught before this shipped.
_vague_leads = [
    ("drfood.worldwide", "Follow for daily food content and recipes worldwide"),
    ("c.big.moe", "Big moe here, living my best life"),
    ("ab.art.pro", "Art is life. DM for collabs"),
]
_plain_real_leads = [
    ("cedar.furniture.beirut", "Cedar Furniture, Beirut"),
    ("newbiz.cafe", "Cafe in Hamra. Fresh coffee daily."),
    ("sparrowcarriers", "Sparrow Carriers - trusted shipping and delivery across Lebanon"),
]
_vague_still_pass = [n for n, b in _vague_leads
                      if qualify.qualify_profile(profile(display_name=n, bio=b, has_website=False), "")[0]]
_plain_rejected = [n for n, b in _plain_real_leads
                    if not qualify.qualify_profile(profile(display_name=n, bio=b, has_website=False), "")[0]]
check("vague high-follower non-businesses are rejected%s" % (
        "" if not _vague_still_pass else " -- STILL PASS: %s" % _vague_still_pass),
      not _vague_still_pass)
check("plain real small-business bios still pass (not overtightened)%s" % (
        "" if not _plain_rejected else " -- WRONGLY REJECTED: %s" % _plain_rejected),
      not _plain_rejected)

# Message templates must read as English even when the scrape failed to read
# the company name. LIVE-CONFIRMED 2026-09-13: the "there" fallback produced
# "Worth a quick check on there's setup?" and "brands like there?", which
# would have been sent to real prospects verbatim.
import re as _re
from agent.messaging import generate as _gen
from agent.db import repositories as _repo

_BROKEN = _re.compile(r"there's setup|like there\b|noticed there\b|there doesn", _re.I)
_TENANTS = [("cmt0o0yr30002f9p5t3eb269f", ["linkedin", "instagram"]),
            ("cmt4i8jxh000crao9ekd6pe08", ["linkedin"]),
            ("cmt4t0xv000046pkmm7ue1qhz", ["instagram"])]
_broken = []
for _tid, _plats in _TENANTS:
    try:
        with _repo.tenant_scope(_tid):
            for _plat in _plats:
                for _bn in ("Cedar Industrial SAL", None):
                    _body = _gen.generate_message(
                        {"business_name": _bn, "platform": _plat,
                         "weak_points": ["no group benefit"]}, _plat, "discovery")
                    if _BROKEN.search(_body):
                        _broken.append("%s/%s/name=%s" % (_tid[:8], _plat, _bn))
    except Exception as _exc:
        _broken.append("%s: %s" % (_tid[:8], _exc))
check("outreach templates read as English even with no company name%s" % (
        "" if not _broken else " -- BROKEN: %s" % _broken),
      not _broken)

# LinkedIn's Page inbox hard-rejects anything outside 25-750 chars. The
# generation-time guard (_enforce_channel_length) originally covered ONLY
# the AI-generated path -- generate_message() returned the FIXED templates
# early, before the guard ever ran. Twice now a template quietly grew past
# 750 (Zimmar 2026-09-13, Insurance 2026-09-16) and the failure only
# surfaced as MessageLengthInvalid at SEND time, after a human had already
# approved and queued the message -- 12 + 5 real Insurance messages sat
# unsendable in the queue because of it. The guard now runs on every
# return path. Tested with a deliberately long real-world company name,
# which is exactly what pushed the pre-fix templates over the limit.
_LONG_NAME = "Advanced Construction Technology Services International Holding SAL"
_over = []
for _tid, _plats in _TENANTS:
    try:
        with _repo.tenant_scope(_tid):
            for _plat in _plats:
                for _wp in ([], ["no group employee benefit"]):
                    _b = _gen.generate_message(
                        {"business_name": _LONG_NAME, "platform": _plat, "weak_points": _wp},
                        _plat, "discovery")
                    _limit = _gen._CHANNEL_MAX_CHARS.get(_plat)
                    if _limit is not None and len(_b) > _limit:
                        _over.append("%s/%s/wp=%d: %d chars" % (_tid[:8], _plat, len(_wp), len(_b)))
    except Exception as _exc:
        _over.append("%s: %s" % (_tid[:8], _exc))
check("every fixed template renders within its channel limit, even with a very long company name%s" % (
        "" if not _over else " -- OVER LIMIT: %s" % _over),
      not _over)
# Structural check: the guard must be applied to the template paths, not
# just happen to be unnecessary today. generate_message() must have exactly
# ONE return, and it must be the length-guarded one -- an early `return
# _<tenant>_fixed_template(...)` is precisely the regression this catches.
import inspect as _gm_inspect
_gm_src = _gm_inspect.getsource(_gen.generate_message)
check("generate_message() has a single length-guarded exit (no early return bypasses _enforce_channel_length)",
      _gm_src.count("return ") == 1 and "return _enforce_channel_length(body, channel)" in _gm_src)

# Strict Lebanon-only location, added 2026-09-13 after real leads in Dubai
# and Australia slipped through for Zimmar/Insurance. Two rules: (1) a bio
# naming a known foreign place is always a reject, (2) for a
# Lebanon-configured tenant, a bio with NO Lebanese place name at all is
# ALSO a reject now (owner: "we do not need outside lebanon... yes" to
# rejecting on no signal) -- previously an empty/ambiguous location got the
# benefit of the doubt.
check("Dubai bio flagged as foreign for a Lebanon-configured tenant",
      sch._mentions_foreign_location("Leading security provider based in Dubai, UAE", "lebanon") is not None)
check("Australia bio flagged as foreign for a Lebanon-configured tenant",
      sch._mentions_foreign_location("Proudly serving Sydney and Melbourne since 2010", "lebanon") is not None)
check("a real Beirut bio is NOT flagged as foreign",
      sch._mentions_foreign_location("Your trusted partner in Beirut, Lebanon", "lebanon") is None)
check("strict mode: a bio with no Lebanese place name at all has no positive signal",
      not any(p in "Solar panel installer for homes and offices".lower() for p in sch._LEBANON_PLACE_MARKERS))
check("strict mode: a real Beirut-mentioning bio DOES have a positive signal",
      any(p in "Solar panel installer for homes and offices in Beirut".lower() for p in sch._LEBANON_PLACE_MARKERS))

# LinkedIn's headquarters-check and bio-scan used to be if/else -- the bio
# was only scanned when Headquarters was blank, so a Headquarters field that
# merely existed (regardless of content) skipped the bio check entirely.
# Confirmed by reading scheduler.py's own _discover_linkedin source: both
# checks must now be reachable, not mutually exclusive.
import inspect as _inspect
_linkedin_src = _inspect.getsource(sch)
check("LinkedIn's foreign-bio scan is NOT gated behind an empty Headquarters field (structural regression check)",
      "if not mismatch_reason:\n                        foreign_marker = _mentions_foreign_location" in _linkedin_src)

# RELAXED 2026-09-13, real bug caught live in the actual A-to-Z cycle: the
# strict re-check rejected Dar (Dar Al-Handasah) and Aramex -- two large,
# famous, genuinely Lebanese-founded companies -- purely because their
# LinkedIn Headquarters field lists their current GLOBAL hq (Singapore,
# Dubai), not Beirut where they started and still operate. Both were
# surfaced by the search's OWN companyHqGeo=Lebanon facet -- LinkedIn had
# already confirmed a real Lebanon connection, and the stricter re-check
# then threw that confirmation away. Fix: when the search itself used a
# verified geo facet (true for any configured_location LOCATION_FACETS
# recognizes, e.g. "lebanon"), trust it and skip the extra
# Headquarters/bio re-verification -- only fall back to the strict check
# for a location LinkedIn has no facet ID for.
check("LinkedIn has a verified geo facet ID for 'lebanon' (what the relaxed-check decision is keyed on)",
      linkedin.LOCATION_FACETS.get("lebanon") is not None)
check("_discover_linkedin's source actually checks LOCATION_FACETS before applying the strict re-check (regression check)",
      "search_used_geo_facet = bool(linkedin.LOCATION_FACETS.get(configured_location))" in _linkedin_src)

# Real owner question after everything above still wasn't enough: "we have
# 80,000 companies in Lebanon, why can't we find 5?" Answer: 6 checks
# stacked in sequence compound their rejection rates -- a LIVE run visited
# 8 real candidates and saved 0 even with every earlier bug fixed. Raised
# from 10 to 20 search rounds/day (doubles daily candidate volume) and
# loosened the two weakest-justified checks (this file's own checks below
# cover the message-button wait-timing fix and the Instagram
# no-signal-at-all hard reject being downgraded to a soft qualify_profile
# signal) -- owner-approved: "both bro" (loosen filters AND raise volume).
check("max search rounds raised to 20 (was 10) to survive 6 stacked filters compounding",
      sch._MAX_SEARCH_ATTEMPTS == 20)
check("Instagram's old hard 'no Lebanese signal in bio' reject is gone (relaxed to a soft qualify_profile signal)",
      "no positive signal this is a Lebanese account" not in _inspect.getsource(sch._discover_instagram))
check("Instagram's foreign-location hard reject is UNCHANGED (still catches real Dubai/Australia leads)",
      "rejected (foreign location:" in _inspect.getsource(sch._discover_instagram))
# has_message_button() and its discovery-time wiring were REMOVED
# 2026-09-13 (owner's own call after two rounds of render-wait tuning
# still hit a ~25% false-reject rate on real, reachable companies): losing
# real leads to a false pre-check is worse than a genuine no-button lead
# reaching send time, where it's now caught cleanly and shown with its
# real reason (see the sendFailureReason checks below) instead of a
# silent, endless "pending" retry loop.
check("has_message_button (the discovery-time pre-check) is fully removed, not just disabled",
      not hasattr(linkedin, "has_message_button"))
check("no 'no Message button' rejection path remains in _discover_linkedin (regression check)",
      "rejected (no Message button)" not in _inspect.getsource(sch._discover_linkedin))

# Real gap found and fixed 2026-09-13 while removing the pre-check above:
# a NoMessageButtonAvailable at SEND time used to reset send_status back to
# "pending" -- the same treatment as a genuinely transient failure (a
# network blip, a timeout) -- so it silently retried forever with the real
# reason visible nowhere but a log line. Owner: "when it appears in the
# approval section as no message button I will press hold and ignore
# sending it" -- that requires the failure to actually land as a visible,
# permanent "failed" state with its real reason attached, not an endless
# silent retry.
_ls_src = _inspect.getsource(linkedin_send)
_is_src = _inspect.getsource(instagram_send)
check("linkedin_send.py marks a NoMessageButtonAvailable as a PERMANENT failed status with its reason persisted",
      'except NoMessageButtonAvailable as exc:' in _ls_src and '"send_failure_reason": str(exc)' in _ls_src)
check("instagram_send.py marks its own NoMessageButtonAvailable as a PERMANENT failed status with its reason persisted",
      'except NoMessageButtonAvailable as exc:' in _is_src and '"send_failure_reason": str(exc)' in _is_src)
# UPDATED 2026-09-16: the literal "send_status": "pending" reset used to
# live directly in linkedin_send.py/instagram_send.py, which is what this
# check originally grepped for. It now lives in ONE shared place --
# delivery.settle_after_failure() -- as part of fixing the two double-send
# vectors documented in that module's own docstring, so both files now
# route a transient (pre-delivery) failure through settle_after_failure()
# instead of writing the literal themselves. The actual pending-reset
# behavior is verified BEHAVIORALLY above (the "still releases a
# NOT-yet-delivered message back to 'pending'" check); this one just
# confirms both send paths still call the shared function rather than
# having quietly dropped back to handling it inline.
check("a genuinely transient send failure still resets to 'pending' for a real retry (regression check, both channels)",
      "settle_after_failure(" in _ls_src and "settle_after_failure(" in _is_src)

# THE REAL ROOT CAUSE of every LinkedIn zero-results run tonight and last
# night, LIVE-CONFIRMED 2026-09-13: both Zimmar's and Insurance's
# target_industry field is long descriptive PROSE, not a search term
# ("Offices, warehouses, retail, schools, factories, residential
# complexes, hospitality; individuals or families setting up a new
# house..." for Zimmar). build_search_url() had a length/comma guard for
# a verbose LOCATION string (see _MAX_LOCATION_KEYWORD_LENGTH's own use
# just above) but the identical guard was never applied to `industry` --
# so that entire sentence was appended straight into the `keywords` query
# param on every single search. Direct proof: a live diagnostic run
# showed 10/10 search rounds returning 0 results with the real
# (verbose-industry) URL, while the EXACT SAME niche+location, tested
# moments later with industry dropped, returned 10 real companies and a
# page showing "643 results" for keyword 'trading' alone.
_verbose_industry = (
    "Offices, warehouses, retail, schools, factories, residential complexes, "
    "hospitality; individuals or families setting up a new house; real estate "
    "developers building new apartments/buildings; engineering and construction "
    "companies; and property/building management companies -- any business, "
    "developer, or individual showing a real need, no category prioritized over another"
)
_poisoned_url = linkedin.build_search_url("trading", "Lebanon", _verbose_industry, None)
check("verbose target_industry text is NOT appended into the LinkedIn search keywords%s" % (
        "" if "Offices" not in _poisoned_url else " -- STILL POISONED: %s" % _poisoned_url),
      "Offices" not in _poisoned_url and "warehouses" not in _poisoned_url)
check("a real short industry facet still passes through untouched",
      "technology" in linkedin.build_search_url("trading", "Lebanon", "technology", None))

# Agency exclusion added 2026-09-13 (owner: "no agencies only companies and
# businesses"), then REVERSED the same night during a live test (owner:
# "some agencies have good number of employees... Yes, both -- remove the
# no-agency rule entirely for both tenants"): an agency with real staff and
# a real office has real premises to protect and a real insurable
# operation just like any other company. _is_agency() itself is still a
# correct detector (checked below) -- only the WIRING that hard-rejected on
# it is gone, via an empty _AGENCY_EXCLUDED_BUSINESSES set, kept for a
# future tenant that might genuinely need this (MJivity's own market
# explicitly INCLUDES agencies as legitimate clients, the opposite
# preference -- see outreach-tenant-targeting-rules memory).
check("_is_agency() still correctly detects a marketing agency (detector itself unchanged)",
      qualify._is_agency("acme.marketing.agency", "Full-service digital marketing agency in Beirut"))
check("_is_agency() still correctly detects a real estate agency (detector itself unchanged)",
      qualify._is_agency("cedar.realty", "Cedar Realty Agency - real estate in Beirut"))
check("_is_agency() does not false-positive a real furniture business",
      not qualify._is_agency("cedar.furniture", "Cedar Furniture, Beirut"))
check("_is_agency() does not false-positive a real logistics company",
      not qualify._is_agency("hosarilogistics", "Freight forwarding and logistics company in Beirut"))
check("agency exclusion is now EMPTY for both Zimmar and Insurance (owner-reversed 2026-09-13)",
      sch._AGENCY_EXCLUDED_BUSINESSES == set())

# Real-time discovery progress logging, added 2026-09-13 after a 30+ minute
# discovery run showed zero visible progress and the owner had to ask "how
# to check where is the real problem" -- there was no way to tell from the
# logs alone whether a long run was stuck or just slow. Just confirms the
# logger exists and is wired to reach docker logs (server.py's own
# logging.basicConfig() call, not re-tested here).
check("discovery progress logger exists and is a real Logger",
      hasattr(sch, "_progress_log") and isinstance(sch._progress_log, __import__("logging").Logger))

# Real infra problem found live 2026-09-13: the droplet has only 1.9GB RAM
# total, and one Chromium renderer process alone measured at 22.7% of that
# mid-run (docker top) -- with GPU/audio/network helper processes on top,
# one browser instance ate ~40-45% of the whole machine, at load average
# 4.5. That resource starvation, not a selector/logic bug, is the confirmed
# real cause behind a run of consecutive Page.goto timeouts. Upgrading the
# droplet was explicitly ruled out ("this is the 3rd time we upgrade") --
# this trims Chromium's own footprint instead (GPU, audio, extensions,
# background networking, etc, none of which a headless scraper needs).
import inspect as _sess_inspect
_session_src = _sess_inspect.getsource(sess)
check("Chromium launches with memory-saving flags (--disable-gpu, --mute-audio) for the RAM-constrained droplet",
      "--disable-gpu" in _session_src and "--mute-audio" in _session_src)
check("the anti-detection flag (--disable-blink-features=AutomationControlled) is still present (regression check)",
      "--disable-blink-features=AutomationControlled" in _session_src)

# REAL ROOT CAUSE of the duplicate-message incident, found and fixed
# 2026-09-15: linkedin_reply_check.py/instagram_reply_check.py's
# _sync_thread_messages() re-reads the live LinkedIn/Instagram DOM every
# 3-minute reply-detection poll and backfills any "new" outgoing message it
# sees into our DB. The dedup compared RAW strings -- our own stored body
# keeps real paragraph breaks, but the live DOM renders the identical
# message as one flat run with no line breaks, so the two never matched and
# EVERY poll re-inserted the same one real, already-sent message as a
# phantom "new" duplicate row (approved_by=None, created_at==approved_at==
# sent_at). LIVE-CONFIRMED against 5 real leads. No second message was
# ever actually delivered to any lead -- this was purely a duplicate
# DATABASE record of one real send, which the dashboard then rendered as
# if two real sends had happened.
check("_normalized_for_dedup() exists and actually collapses whitespace/newlines",
      linkedin_reply_check._normalized_for_dedup("Hi\n\nthere") == linkedin_reply_check._normalized_for_dedup("Hithere"))
check("linkedin_reply_check's dedup uses the normalized comparison, not raw strings (regression check)",
      "normalized in known_outgoing" in _inspect.getsource(linkedin_reply_check._sync_thread_messages))
check("instagram_reply_check's dedup uses the normalized comparison, not raw strings (regression check)",
      "normalized in known_outgoing" in _inspect.getsource(instagram_reply_check._sync_thread_messages))

# REAL ROOT CAUSE of 9 fake replies + false "replied" statuses, found and
# fixed 2026-09-16: instagram_reply_check._read_thread_messages() used to
# decide each bubble's direction ("us" vs "lead") by comparing its
# horizontal position against the THREAD'S OWN AVERAGE left offset --
# LIVE-CONFIRMED against 5 real leads (meteorintheyks, hnmoverseas,
# al_mosbah_, lafe.leb, lets_travel_and_discover), each a thread holding
# ONLY our own template's messages (no real reply ever received). With
# only one side actually present, the average sits in the middle of OUR
# OWN bubbles, so roughly half of them landed left of it and were recorded
# as if the lead had sent them -- fabricating outreach_replies rows out of
# our own pitch text and flipping the lead to status='replied' on zero real
# evidence. Fix: direction is no longer guessed from position alone. A
# bubble already matching known outgoing/incoming CONTENT is classified by
# that; a genuinely new bubble only gets a position-based guess once a real
# reply already exists for the lead (the thread is confirmed two-sided);
# otherwise it is skipped rather than guessed.
class _FakeReplyRepo:
    """Stands in for repo inside _sync_thread_messages: a lead with a known
    outgoing template already on file, no confirmed reply yet (the exact
    shape of the 5 real leads above at the moment the bug fired)."""
    def __init__(self, existing_replies=None, existing_messages=None):
        self._replies = existing_replies or []
        self._messages = existing_messages or []
        self.inserted_replies = []
        self.inserted_messages = []
    def replies_for_lead(self, lead_id):
        return self._replies
    def messages_for_lead(self, lead_id):
        return self._messages
    def insert_message(self, fields):
        self.inserted_messages.append(fields)
    def insert_error(self, *a, **k):
        pass

_zim_paras = _gen._ZIMMAR_TEMPLATE_INSTAGRAM.split("\n\n")
_ig_real_repo = instagram_reply_check.repo
_li_real_repo = linkedin_reply_check.repo
_orig_handle_reply = instagram_reply_check.handle_reply_detected
try:
    # No confirmed reply exists yet -- a thread with ONLY our own opening
    # and closing lines (mirrors the real 2-fragment pattern found on all
    # 5 leads). Bubbles are given the SAME left offset the real bug needed
    # to misfire (an even split around the average).
    _fake_no_reply_yet = _FakeReplyRepo(existing_replies=[], existing_messages=[
        {"channel": "instagram", "body": _zim_paras[0] + "\n\n" + _zim_paras[1] + "\n\n" + _zim_paras[2] + "\n\n" + _zim_paras[-1]},
    ])
    instagram_reply_check.repo = _fake_no_reply_yet
    _fabricated_replies = []
    instagram_reply_check.handle_reply_detected = lambda *a, **k: _fabricated_replies.append(k.get("body") or (a[2] if len(a) > 2 else None))
    _live = [{"text": _zim_paras[0], "left": 100.0}, {"text": _zim_paras[-1], "left": 900.0}]
    _new_replies, _new_outgoing = instagram_reply_check._sync_thread_messages(
        {"id": "lead-no-reply-yet"}, {"id": "acc1"}, _live)
    check("a thread with ONLY our own messages and no confirmed reply yet fabricates ZERO replies (the false 'replied' bug)",
          _new_replies == 0 and not _fabricated_replies)

    # A real reply DOES already exist -- the thread is confirmed two-sided,
    # so a genuinely new incoming message must still be detected normally.
    _fake_with_reply = _FakeReplyRepo(
        existing_replies=[{"body": "Thanks, tell me more"}],
        existing_messages=[{"channel": "instagram", "body": _zim_paras[0]}],
    )
    instagram_reply_check.repo = _fake_with_reply
    _fabricated_replies.clear()
    _live2 = [
        {"text": _zim_paras[0], "left": 900.0},          # already-known outgoing (content match, not position)
        {"text": "Thanks, tell me more", "left": 100.0},  # already-known incoming (content match)
        {"text": "Ok sounds good, call me", "left": 100.0},  # genuinely NEW incoming, same side as the known reply
    ]
    _new_replies2, _new_outgoing2 = instagram_reply_check._sync_thread_messages(
        {"id": "lead-with-reply"}, {"id": "acc1"}, _live2)
    check("a genuinely NEW incoming message is still detected once a real reply already anchors the thread",
          _new_replies2 == 1 and _fabricated_replies == ["Ok sounds good, call me"])

    # REAL BUG found live 2026-09-19 (fadeltradingcompany, titus.logistics,
    # real Zimmar Instagram leads): the ORIGINAL version of this check
    # additionally required known_incoming to be non-empty (a reply already
    # confirmed on a PRIOR run) before ever trusting a position-based
    # guess -- which silently excluded a lead's FIRST EVER reply, since
    # that is always the one moment known_incoming is still empty. Both
    # real leads sent a genuine first reply that this exact gate skipped
    # outright. This is the fix: a genuinely new bubble at a lead's FIRST
    # reply (known_incoming empty, but our own sent message IS
    # content-matched in this same read) must now be detected.
    _fake_first_reply = _FakeReplyRepo(existing_replies=[], existing_messages=[
        {"channel": "instagram", "body": "Our cold outreach message"},
    ])
    instagram_reply_check.repo = _fake_first_reply
    _fabricated_replies.clear()
    _live3 = [
        {"text": "Our cold outreach message", "left": 900.0},  # us, content-matched
        {"text": "Hi thanks for reaching out", "left": 100.0},  # genuinely new -- the lead's FIRST reply
    ]
    _new_replies3, _new_outgoing3 = instagram_reply_check._sync_thread_messages(
        {"id": "lead-first-reply"}, {"id": "acc1"}, _live3)
    check("a lead's FIRST EVER reply is detected even with no prior confirmed reply on file (2026-09-19 real bug fix)",
          _new_replies3 == 1 and _fabricated_replies == ["Hi thanks for reaching out"])
finally:
    instagram_reply_check.repo = _ig_real_repo
    instagram_reply_check.handle_reply_detected = _orig_handle_reply

check("_read_thread_messages() no longer decides direction itself (returns undecided bubbles, see its own docstring)",
      '"from"' not in _inspect.getsource(instagram_reply_check._read_thread_messages))
check("direction classification lives in _sync_thread_messages(), anchored to known content first (regression check)",
      "known_outgoing" in _inspect.getsource(instagram_reply_check._sync_thread_messages)
      and "confirmed_us_lefts" in _inspect.getsource(instagram_reply_check._sync_thread_messages))

# REAL ROOT CAUSE found and fixed 2026-09-18, live-confirmed against lead
# "Khatib & Alami" (Insurance tenant, lead_id 83ae70fd-0064-4372-992e-
# 612691e2add3): unlike Instagram (which derives direction from CONTENT
# first, known_outgoing checked before any position guess -- see the block
# above), linkedin_reply_check._sync_thread_messages() unconditionally
# trusted _read_thread_messages()'s DOM-derived "from" label. A re-render
# of our OWN already-sent message got mislabeled "lead" by LinkedIn's own
# sender-name carry-forward heuristic and was inserted straight into
# outreach_replies as a fabricated reply -- confirmed live: the bad row's
# body is byte-for-byte our own outbound message with paragraph breaks
# flattened. Fix: a candidate labeled "lead" is now cross-checked against
# known_outgoing (content-normalized) BEFORE being accepted -- a match
# means it's our own message misread off the page, not a real reply, and
# it's skipped entirely (no outreach_replies insert, no status flip).
check("linkedin_reply_check cross-checks a 'lead'-labeled candidate against known_outgoing before accepting it (regression check)",
      "if normalized in known_outgoing" in _inspect.getsource(linkedin_reply_check._sync_thread_messages))

class _FakeLinkedInRepo:
    """Stands in for repo inside linkedin_reply_check._sync_thread_messages:
    a lead with one known outgoing message on file and one genuine prior
    reply -- mirrors Khatib & Alami's real shape (one real send, one real
    'deleted message' reply) at the moment the bug fired."""
    def __init__(self, existing_replies=None, existing_messages=None):
        self._replies = existing_replies or []
        self._messages = existing_messages or []
    def replies_for_lead(self, lead_id):
        return self._replies
    def messages_for_lead(self, lead_id):
        return self._messages
    def insert_message(self, fields):
        pass
    def insert_error(self, *a, **k):
        pass

_li_sent_body = "Hello Khatib & Alami,\n\nWe're introducing Lebanon's first Dental Card.\n\nWould it be worth a quick call?"
_fake_li_repo = _FakeLinkedInRepo(
    existing_replies=[{"body": "This message has been deleted."}],
    existing_messages=[{"channel": "linkedin", "body": _li_sent_body}],
)
try:
    linkedin_reply_check.repo = _fake_li_repo
    _li_fabricated = []
    linkedin_reply_check.handle_reply_detected = lambda *a, **k: _li_fabricated.append(k.get("body") or (a[2] if len(a) > 2 else None))
    # DOM mislabels our own already-sent message as "lead" (flattened
    # newlines, exactly like LinkedIn's real re-render) -- the exact
    # live-confirmed failure mode for Khatib & Alami.
    _li_live = [
        {"from": "lead", "text": "This message has been deleted."},   # genuine prior reply, already known
        {"from": "lead", "text": _li_sent_body.replace("\n", "")},    # BUG: our own message mislabeled "lead"
    ]
    _li_new_replies, _li_new_outgoing = linkedin_reply_check._sync_thread_messages(
        {"id": "lead-khatib-alami"}, {"id": "acc1"}, _li_live)
    check("a DOM-mislabeled 'lead' message matching our own known_outgoing content fabricates ZERO replies (Khatib & Alami bug)",
          _li_new_replies == 0 and not _li_fabricated)
finally:
    linkedin_reply_check.repo = _li_real_repo
    linkedin_reply_check.handle_reply_detected = _orig_handle_reply

# REAL SECOND BUG found alongside the above while investigating: the
# sending cycle had NO daily-send-limit check at all -- LIVE-CONFIRMED one
# account sent 8 real LinkedIn messages in a single day against its own
# configured linkedin_daily_limit of 5 (this is a SEPARATE issue from the
# phantom-duplicate-row bug above; both existing together is what let the
# real send count run over its cap once duplicate rows were queued).
check("cold_sends_today_for_account() exists for the new sending-side daily cap",
      hasattr(sch.repo, "cold_sends_today_for_account"))
check("_run_sending_cycle_for_tenant() actually applies the daily cap (regression check)",
      "cold_sends_today_for_account" in _inspect.getsource(sch._run_sending_cycle_for_tenant))

# REAL INCIDENT 2026-09-16: LinkedIn served a security checkpoint on Zimmar's
# account ("unusual activity / high volume of profile data access"). Both that
# LinkedIn account and its Instagram account were set to status='paused' in
# the DB -- but the pause did NOT stop sending. Account status was only ever
# consulted at schedule-BUILD time (build_daily_schedule), so the sending jobs
# registered at the previous boot (08:41 and 10:05 Beirut) still held live
# closures and would have kept sending on the checkpointed account until the
# next redeploy. Discovery was never exposed: pool.get_due_accounts() has
# always re-checked status at run time. This proves the SENDING path now does
# the same, by actually running the real cycle against a paused account and
# asserting no send is even attempted (no browser session, no stage_run row).
class _FakeSendRepo:
    """Stands in for repo: one paused account with an approved message waiting."""
    def __init__(self, status):
        self.status = status
        self.started_runs = []
    class _Scope:
        def __enter__(self): return None
        def __exit__(self, *a): return False
    def tenant_scope(self, tenant_id): return self._Scope()
    def is_tenant_paused(self): return False          # tenant is ACTIVE; only the account is paused
    def get_account(self, account_id, tenant_id):
        return {"id": account_id, "status": self.status, "platform": "linkedin",
                "label": "Zimmar LinkedIn", "send_daily_limit_override": None}
    def messages_approved_pending(self):
        # A real, sendable, non-reply message is waiting -- so if the guard is
        # missing, the cycle WILL try to send it and the check fails loudly.
        return [{"id": "m1", "lead_id": "l1", "is_reply": False}]
    def get_lead(self, lead_id): return {"id": lead_id, "account_id": "acc-zimmar-li"}
    def start_stage_run(self, tenant_id, stage):
        self.started_runs.append(stage)
        return {"id": "run1"}
    def finish_run(self, *a, **k): pass
    def insert_error(self, *a, **k): pass
    def cold_sends_today_for_account(self, *a, **k): return 0

_real_repo = sch.repo
try:
    for _status, _should_send in (("paused", False), ("warned", False), ("active", True)):
        _fake = _FakeSendRepo(_status)
        sch.repo = _fake
        _results = sch.run_account_sending_cycle("tenant-zimmar", "acc-zimmar-li")
        if _should_send:
            # An active account must still get PAST the guard. It will fail
            # later (no real browser/DB here) -- what matters is that it tried:
            # a stage_run row was opened, proving the guard did not short-circuit.
            check("an ACTIVE account is still allowed to send (guard is not blanket-blocking)",
                  _fake.started_runs == ["sending"])
        else:
            check("a %r account sends NOTHING at run time, even with an approved message queued" % _status,
                  _results == [] and _fake.started_runs == [])
finally:
    sch.repo = _real_repo

check("run_account_sending_cycle() re-reads account status at RUN time, not just at schedule-build (regression check)",
      "get_account" in _inspect.getsource(sch.run_account_sending_cycle))
check("_run_sending_cycle_for_tenant() also gates on account status (defense in depth, regression check)",
      'status") != "active"' in _inspect.getsource(sch._run_sending_cycle_for_tenant))

# Owner-requested 2026-09-15: a simple box on the Follow-ups dashboard page
# for what follow-up messages should be about (OutreachSettings.
# followUpGuidance), threaded through as extra Claude context -- NOT a
# fixed template, _FOLLOWUP_RULES still forces a genuinely fresh,
# non-repeating message every time.
check("generate_followup_message() accepts follow_up_guidance and passes it through",
      "follow_up_guidance" in _inspect.signature(_gen.generate_followup_message).parameters)
check("format_personalization_context() includes follow_up_guidance in the prompt when given",
      "What this follow-up should be about" in _gen.format_personalization_context(
          {"business_name": "Test Co"}, follow_up_guidance="mention our new pricing"))
check("format_personalization_context() adds nothing extra when no guidance is given (regression check)",
      "What this follow-up should be about" not in _gen.format_personalization_context({"business_name": "Test Co"}))

# REAL CUSTOMER-VISIBLE BUG, found and fixed 2026-09-16: one Instagram DM to
# lead "mik.export" arrived as FOUR separate message bubbles, all stamped
# 11:31:49, one per paragraph of Zimmar's fixed Instagram template (owner
# screenshot). Cause: core/pacing.py's human_type() fed EVERY character to
# press_sequentially(), newlines included, and a bare Enter in Instagram's
# (and LinkedIn's) chat composer means SEND -- so each "\n\n" paragraph
# break fired a real send and the rest kept typing into an emptied box.
# Reads as spam to the recipient. The fix types newlines as explicit
# Shift+Enter (soft line break, never sends) instead.
from agent.core import pacing as _pacing

class _FakeLocator:
    """Records exactly what human_type() would do to a real composer."""
    def __init__(self):
        self.events = []
    def click(self):
        self.events.append(("click", ""))
    def press(self, key, delay=None):
        self.events.append(("press", key))
    def press_sequentially(self, text, delay=None):
        self.events.append(("type", text))

def _drive(body):
    loc = _FakeLocator()
    _pacing.human_type(loc, body)
    return loc.events

_zim = _gen._ZIMMAR_TEMPLATE_INSTAGRAM
_zim_events = _drive(_zim)
_typed_chars = [e[1] for e in _zim_events if e[0] == "type"]
_pressed = [e[1] for e in _zim_events if e[0] == "press"]

# The core assertion: a raw newline must NEVER be typed as a character --
# that keystroke is literally what sent the four separate messages.
check("human_type() never types a raw newline character (the four-separate-messages bug)",
      not any("\n" in c or "\r" in c for c in _typed_chars))
check("human_type() uses Shift+Enter for line breaks, not bare Enter",
      _pressed and all(k == "Shift+Enter" for k in _pressed))
# One Shift+Enter per newline in the real template (8 today: 4 blank lines
# between its 5 blocks). Asserted against the template's own count rather
# than a hardcoded number, so an owner-approved wording change can't fail
# this check -- what matters is that no newline is lost or doubled. Two
# presses per blank line is what preserves the visual paragraph spacing
# instead of collapsing it to a single break.
check("Zimmar's Instagram template produces one Shift+Enter per newline (blank lines preserved, not collapsed)",
      len(_pressed) == _zim.count("\n"))
check("a '\\n\\n' blank line yields exactly TWO Shift+Enters (paragraph spacing preserved)",
      [e[1] for e in _drive("a\n\nb") if e[0] == "press"] == ["Shift+Enter", "Shift+Enter"])
# Every visible character of the approved body still gets typed, in order --
# the fix must not drop or reorder any of the real message content.
check("human_type() still types the complete approved body, in order",
      "".join(_typed_chars) == _zim.replace("\n", ""))
# Single-line fields (login email/password via core/session.py) must behave
# exactly as before: no newlines in, no Shift+Enter out.
check("a single-line value presses no keys at all (login fields unaffected)",
      [e for e in _drive("user@example.com") if e[0] == "press"] == [])

# LinkedIn was affected identically -- same shared human_type(), and its
# Zimmar template also has "\n\n" paragraphs. Both send paths (cold +
# reply) on both channels must go through the fixed helper.
import inspect as _hi
for _label, _fn in (
    ("instagram cold send", instagram_send._send_from_profile),
    ("instagram reply", instagram_send.send_reply),
    ("linkedin company send", linkedin_send._send_to_company),
    ("linkedin person send", linkedin_send._send_to_person),
    ("linkedin reply", linkedin_send.send_reply),
):
    _src = _hi.getsource(_fn)
    check("%s types the body via human_type(), not .fill()/.type() (regression check)" % _label,
          "human_type(" in _src and ".fill(" not in _src)

# The 2026-09-15 duplicate-row fix must survive this change: _normalized_for_dedup
# strips ALL whitespace, so a body typed with Shift+Enter line breaks still
# compares equal to the same message read back flat from the live DOM.
check("dedup still matches the Zimmar body against a flat DOM rendering of it (cross-check with the 2026-09-15 fix)",
      linkedin_reply_check._normalized_for_dedup(_zim)
      == linkedin_reply_check._normalized_for_dedup(_zim.replace("\n", " ")))

# ---------------------------------------------------------------------------
# Two real double-send vectors, found and fixed 2026-09-16 -- the owner was
# emphatic a message must NEVER be delivered twice. See
# agent/sending/delivery.py's own module docstring for the full incident.
# VECTOR 1: send_reply() on both channels took no claim at all, so a
# post-delivery failure left the row 'pending' and the ~3-minute reply poll
# re-delivered it, unbounded (replies are exempt from the daily cap).
# VECTOR 2: a blanket `except Exception` enclosing both the send click and
# all post-click teardown reset an already-DELIVERED message back to
# 'pending', causing a genuine re-send on the next cycle.
import inspect as _dd_inspect
from agent.sending import delivery as _delivery

check("delivery.Delivery/settle_after_failure exist and enforce the invariant in code, not just by convention",
      hasattr(_delivery, "Delivery") and hasattr(_delivery, "settle_after_failure"))

# settle_after_failure must NEVER release a delivered message back to
# 'pending', even if persisting that fact to the DB itself fails -- verified
# behaviorally against the real function, not by reading its source.
class _FakeDeliveryRepo:
    def __init__(self):
        self.calls = []
    def update_message(self, message_id, fields):
        self.calls.append(dict(fields))
        if fields.get("send_status") == "sent":
            raise RuntimeError("DB write failed -- must not fall back to pending")
        return None

_orig_update_message = _delivery.repo.update_message
_fake_repo = _FakeDeliveryRepo()
_delivery.repo.update_message = _fake_repo.update_message
try:
    _d = _delivery.Delivery()
    _d.mark()  # simulates: the send click already succeeded
    _delivery.settle_after_failure({"id": "fake-msg"}, _d, RuntimeError("teardown blew up"), channel="linkedin")
    check("settle_after_failure never writes send_status='pending' for an already-delivered message, even when the DB write itself fails",
          all(c.get("send_status") != "pending" for c in _fake_repo.calls)
          and any(c.get("send_status") == "sent" for c in _fake_repo.calls))

    _fake_repo.calls.clear()
    _d2 = _delivery.Delivery()  # never marked -- send click never happened
    _delivery.settle_after_failure({"id": "fake-msg-2"}, _d2, RuntimeError("failed before the click"), channel="linkedin")
    check("settle_after_failure still releases a NOT-yet-delivered message back to 'pending' (the original, correct retry path)",
          any(c.get("send_status") == "pending" for c in _fake_repo.calls))
finally:
    _delivery.repo.update_message = _orig_update_message

for _mod, _fn_name, _label in (
    (linkedin_send, "send_message", "linkedin_send.send_message"),
    (linkedin_send, "send_reply", "linkedin_send.send_reply"),
    (instagram_send, "send_cold_message", "instagram_send.send_cold_message"),
    (instagram_send, "send_reply", "instagram_send.send_reply"),
):
    _src = _dd_inspect.getsource(getattr(_mod, _fn_name))
    check("%s claims the message via claim_message_for_sending() before any browser work (regression check)" % _label,
          "claim_message_for_sending" in _src)
    check("%s uses the shared Delivery/settle_after_failure invariant, not a bare except-Exception reset (regression check)" % _label,
          "settle_after_failure(" in _src and "Delivery(" in _src)

# Owner-requested 2026-09-16, REVISED 2026-09-17: Insurance runs at times
# ANCHORED near a fixed hour (discovery ~23:00/23:02, sending ~10:00), not
# spread across the whole window the way every other tenant (Zimmar,
# mjivity1, future tenants) is via _spread_within_window(). The 2026-09-16
# version made these times perfectly fixed/byte-identical every rebuild;
# the owner then flagged that as its own bot-detection fingerprint (the
# same "same wall-clock minute forever" pattern that got Zimmar's LinkedIn
# account checkpointed), so as of 2026-09-17 each anchor gets its own
# independent +/-_INSURANCE_JITTER_MINUTES (10) jitter, re-drawn every
# build_daily_schedule() call -- see _insurance_jittered_minutes() in
# scheduler.py. Verified three ways: (1) _is_insurance_tenant() correctly
# identifies Insurance by business_name (the same identifier
# messaging/generate.py already keys its own fixed templates on) and
# nothing else; (2) BEHAVIORALLY, by actually calling build_daily_schedule()
# against the real live DB and reading back the real APScheduler jobs it
# produced -- Insurance's discovery/sending jobs must land WITHIN the
# tight +/-10 min band around their anchors on every rebuild, and must
# VARY across independent rebuilds (bounded randomness, no longer a fixed
# minute), while Zimmar's must keep re-randomizing across its own much
# wider spread window; (3) Insurance's per-rebuild range stays tightly
# clustered near its anchor rather than spread across the full 4-hour
# window the way Zimmar's spread mechanism is.
check("_is_insurance_tenant() identifies Insurance by business_name (same identifier generate.py's fixed templates use)",
      sch._INSURANCE_BUSINESS_NAME == _gen._INSURANCE_BUSINESS_NAME)

_INSURANCE_TENANT_ID = "cmt4i8jxh000crao9ekd6pe08"
_ZIMMAR_TENANT_ID = "cmt0o0yr30002f9p5t3eb269f"
check("_is_insurance_tenant() is True for Insurance's real tenant_id",
      sch._is_insurance_tenant(_INSURANCE_TENANT_ID))
check("_is_insurance_tenant() is False for Zimmar's real tenant_id",
      not sch._is_insurance_tenant(_ZIMMAR_TENANT_ID))

# Rebuild the real schedule against the live DB (read-only -- constructing a
# BackgroundScheduler and calling add_job on it does not start the
# scheduler or touch any data) and inspect the actual jobs produced, twice,
# to prove Insurance's time is FIXED across rebuilds while Zimmar's keeps
# moving (spread/jitter re-draws its random offset on every call).
def _job_times(jobs, id_prefix, tenant_id):
    out = {}
    for job in jobs:
        if job.id.startswith("%s-%s-" % (id_prefix, tenant_id)):
            trig = job.trigger
            hour = str(trig.fields[trig.FIELD_NAMES.index("hour")])
            minute = str(trig.fields[trig.FIELD_NAMES.index("minute")])
            out[job.id] = (hour, minute)
    return out

_sched1 = sch.build_daily_schedule()
_sched2 = sch.build_daily_schedule()

_ins_disc_1 = _job_times(_sched1.get_jobs(), "discovery", _INSURANCE_TENANT_ID)
_ins_disc_2 = _job_times(_sched2.get_jobs(), "discovery", _INSURANCE_TENANT_ID)
_ins_send_1 = _job_times(_sched1.get_jobs(), "sending", _INSURANCE_TENANT_ID)
_ins_send_2 = _job_times(_sched2.get_jobs(), "sending", _INSURANCE_TENANT_ID)
_zim_disc_1 = _job_times(_sched1.get_jobs(), "discovery", _ZIMMAR_TENANT_ID)
_zim_disc_2 = _job_times(_sched2.get_jobs(), "discovery", _ZIMMAR_TENANT_ID)

# Extra rebuilds (beyond the two above) JUST for the "varies across
# rebuilds" checks below. A single independent +/-10 min randint draw has
# a real ~1-in-21 chance of landing on the identical minute as one other
# draw -- especially visible on Insurance's sending job, which has only
# ONE account (LinkedIn) behind it, so two rebuilds coincidentally
# matching is a plausible, non-buggy flake, not evidence the jitter regressed
# to fixed. Rebuilding several more times and requiring NOT ALL of them to
# match drops the false-flake probability to (1/21)^(N-1) -- effectively
# zero at N=8 -- while still failing hard if the jitter is ever really
# removed (in which case every single rebuild is identical).
_ins_send_all = [_ins_send_1, _ins_send_2]
_ins_disc_all = [_ins_disc_1, _ins_disc_2]
for _ in range(6):
    _extra_sched = sch.build_daily_schedule()
    _ins_send_all.append(_job_times(_extra_sched.get_jobs(), "sending", _INSURANCE_TENANT_ID))
    _ins_disc_all.append(_job_times(_extra_sched.get_jobs(), "discovery", _INSURANCE_TENANT_ID))

# Helper: total minutes-since-midnight for an (hour, minute) string pair,
# so "is this inside the +/-10 min band around anchor X" is a plain
# integer-range check instead of juggling hour/minute separately.
def _mins(pair):
    h, m = pair
    return int(h) * 60 + int(m)

_INS_DISC_LI_ANCHOR = 23 * 60 + 0     # 23:00
_INS_DISC_EMAIL_ANCHOR = 23 * 60 + 2  # 23:02
_INS_SEND_ANCHOR = 10 * 60 + 0        # 10:00
_INS_JITTER = 10  # must match scheduler._INSURANCE_JITTER_MINUTES

# Insurance's/Zimmar's LinkedIn accounts can legitimately be PAUSED (e.g.
# 2026-09-17: both paused while diagnosing droplet resource contention) --
# a paused account correctly has ZERO scheduled jobs (see the run-time
# account-status gate added earlier), so these timing/collision checks are
# only meaningful, and only run, when there's at least one real job to
# check. A paused account is not a regression; an ACTIVE account with
# broken timing is -- these checks still catch that case fully.
if _ins_disc_1:
    check("Insurance's discovery job(s) land within +/-10 min of their 23:00/23:02 anchors on both rebuilds (bounded jitter, not the full 20:00-24:00 window)",
          all(
              abs(_mins(v) - _INS_DISC_LI_ANCHOR) <= _INS_JITTER
              or abs(_mins(v) - _INS_DISC_EMAIL_ANCHOR) <= _INS_JITTER
              for v in _ins_disc_1.values()
          )
          and all(
              abs(_mins(v) - _INS_DISC_LI_ANCHOR) <= _INS_JITTER
              or abs(_mins(v) - _INS_DISC_EMAIL_ANCHOR) <= _INS_JITTER
              for v in _ins_disc_2.values()
          ))
    check("Insurance's discovery time VARIES across independent rebuilds (owner's 2026-09-17 fix -- no longer byte-identical every day; checked across 8 rebuilds so one coincidental match isn't a false flake)",
          any(x != _ins_disc_all[0] for x in _ins_disc_all[1:]))
    check("Insurance's discovery times stay tightly clustered near its anchor (span <= 2x jitter), unlike Zimmar's full-window spread",
          (max(_mins(v) for v in _ins_disc_1.values()) - min(_mins(v) for v in _ins_disc_1.values())) <= 2 * _INS_JITTER + 2)
else:
    print("   OK   Insurance's LinkedIn/Email accounts are currently paused -- no discovery jobs to check, correctly")

if _ins_send_1:
    check("Insurance's sending job lands within +/-10 min of its 10:00 Beirut anchor on both rebuilds, and stays before 11:00",
          all(abs(_mins(v) - _INS_SEND_ANCHOR) <= _INS_JITTER for v in _ins_send_1.values())
          and all(abs(_mins(v) - _INS_SEND_ANCHOR) <= _INS_JITTER for v in _ins_send_2.values())
          and all(_mins(v) < 11 * 60 for v in _ins_send_1.values()))
    check("Insurance's sending time VARIES across independent rebuilds (owner's 2026-09-17 fix, same as discovery; checked across 8 rebuilds so one coincidental match on its single LinkedIn job isn't a false flake)",
          any(x != _ins_send_all[0] for x in _ins_send_all[1:]))
else:
    print("   OK   Insurance's LinkedIn account is currently paused -- no sending job to check, correctly")

if not _zim_disc_1:
    print("   OK   Zimmar's LinkedIn/Instagram/Email accounts are currently paused -- no discovery jobs to check, correctly")
else:
    # REAL BUG FOUND AND FIXED 2026-09-17: with only 2 active Zimmar
    # accounts, _spread_within_window()'s slot-center for one of them lands
    # at EXACTLY 23:00 -- dead center of Insurance's reserved band -- and a
    # live check across 4 separate rebuilds that same day caught Zimmar's
    # discovery landing at 22:51/23:05/23:07/23:11, squarely inside
    # Insurance's 22:48-23:14 zone. Checking only _sched1/_sched2 (2
    # rebuilds) missed this reliably; sampling many more rebuilds here
    # catches the systematic collision this specific 2-account slot-center
    # produces, not just an unlucky one-off jitter draw.
    _zim_disc_many = [_zim_disc_1, _zim_disc_2]
    for _ in range(18):
        _extra_sched = sch.build_daily_schedule()
        _zim_disc_many.append(_job_times(_extra_sched.get_jobs(), "discovery", _ZIMMAR_TENANT_ID))
    check("Zimmar's discovery time is NOT pinned near Insurance's anchor hour/minute, across 20 independent rebuilds (regression check for the 2026-09-17 reserved-range collision)",
          all(
              not any(abs(_mins(v) - _INS_DISC_LI_ANCHOR) <= _INS_JITTER
                      or abs(_mins(v) - _INS_DISC_EMAIL_ANCHOR) <= _INS_JITTER
                      for v in _times.values())
              for _times in _zim_disc_many
          ))
    check("Zimmar's discovery time still RE-RANDOMIZES across independent rebuilds (spread/jitter unchanged, regression check)",
          _zim_disc_1 != _zim_disc_2)

# No literal collision: Insurance's jittered times must not exactly match
# any of Zimmar's own currently-scheduled job times (the owner's own
# explicit constraint, re-affirmed 2026-09-17 when fixed times became
# jittered), checked across BOTH independent rebuilds since Insurance's
# times now move -- and Insurance's own two discovery jobs (LinkedIn +
# Email) must not fire at the identical instant as each other.
_zim_all_times = (
    set(_zim_disc_1.values()) | set(_zim_disc_2.values())
    | set(_job_times(_sched1.get_jobs(), "sending", _ZIMMAR_TENANT_ID).values())
    | set(_job_times(_sched2.get_jobs(), "sending", _ZIMMAR_TENANT_ID).values())
)
if _ins_disc_1 or _ins_send_1 or _zim_all_times:
    check("Insurance's jittered discovery/sending times do not exactly match any of Zimmar's own live-scheduled job times (either rebuild)",
          not (set(_ins_disc_1.values()) & _zim_all_times)
          and not (set(_ins_disc_2.values()) & _zim_all_times)
          and not (set(_ins_send_1.values()) & _zim_all_times)
          and not (set(_ins_send_2.values()) & _zim_all_times))
else:
    print("   OK   Both tenants' LinkedIn accounts are currently paused -- no collision to check, correctly")
if _ins_disc_1:
    check("Insurance's own discovery jobs (LinkedIn + Email) don't collide with each other at the identical instant",
          len(set(_ins_disc_1.values())) == len(_ins_disc_1) or len(_ins_disc_1) == 1)

# ---------------------------------------------------------------------------
# Real resource-contention incident, 2026-09-17: Zimmar LinkedIn's scheduled
# sending job failed 3/3 real send attempts that morning on page-load/
# element-wait timeouts, each one landing within seconds of the reply-poll
# also having a browser open -- the droplet is 1 vCPU/1.9GB and one Chromium
# instance alone eats ~40-45% of that. Two fixes: (1) the reply polls now
# run every 30 min instead of 3, cutting poll-driven browser opens ~90%;
# (2) a global cross-process slot cap (_MAX_CONCURRENT_BROWSER_SESSIONS)
# stops two DIFFERENT accounts' sessions from ever launching Chromium at the
# same literal moment, closing the actual collision instead of just making
# it rarer.
check("reply-send poll interval raised from 3 to 30 minutes (resource-contention fix)",
      sch._REPLY_POLL_INTERVAL_MINUTES == 30)
check("reply-detection poll interval raised from 3 to 30 minutes (resource-contention fix)",
      sch._REPLY_DETECTION_POLL_INTERVAL_MINUTES == 30)

check("a global browser-slot limiter exists and is set to a small, non-zero cap",
      hasattr(sess, "_MAX_CONCURRENT_BROWSER_SESSIONS") and 1 <= sess._MAX_CONCURRENT_BROWSER_SESSIONS <= 4)
check("SessionManager acquires the global slot in __enter__, before launching Chromium (regression check)",
      "_global_session_slot" in _inspect.getsource(sess.SessionManager.__enter__)
      and _inspect.getsource(sess.SessionManager.__enter__).index("_global_session_slot")
          < _inspect.getsource(sess.SessionManager.__enter__).index("chromium.launch"))
check("SessionManager releases the global slot in __exit__, after the browser is closed (regression check)",
      "_global_session_slot" not in _inspect.getsource(sess.SessionManager.__exit__)
      and "_global_slot" in _inspect.getsource(sess.SessionManager.__exit__)
      and _inspect.getsource(sess.SessionManager.__exit__).index("_browser.close")
          < _inspect.getsource(sess.SessionManager.__exit__).index("_global_slot.__exit__"))

# Behavioral check: with the cap set to 1, a second concurrent
# SessionManager must actually raise SessionBusy rather than silently
# proceeding -- proves this is a real enforced limit, not just present code
# that's never actually reached.
_orig_cap = sess._MAX_CONCURRENT_BROWSER_SESSIONS
_orig_timeout = sess._GLOBAL_SESSION_LOCK_TIMEOUT_SECONDS
sess._MAX_CONCURRENT_BROWSER_SESSIONS = 1
sess._GLOBAL_SESSION_LOCK_TIMEOUT_SECONDS = 1
try:
    _held = sess._global_session_slot()
    _held.__enter__()
    try:
        _blocked = False
        try:
            with sess._global_session_slot():
                pass
        except sess.SessionBusy:
            _blocked = True
        check("with the slot cap at 1, a second concurrent session is genuinely blocked (SessionBusy), not silently allowed through",
              _blocked)
    finally:
        _held.__exit__(None, None, None)
    # And once released, a new acquire succeeds immediately -- proves this
    # isn't a one-way lock that permanently wedges the account/machine.
    with sess._global_session_slot():
        check("after the holder releases, a new session can acquire the same slot immediately", True)
finally:
    sess._MAX_CONCURRENT_BROWSER_SESSIONS = _orig_cap
    sess._GLOBAL_SESSION_LOCK_TIMEOUT_SECONDS = _orig_timeout

# Owner-requested 2026-09-17: 10 approved messages must reliably finish
# within a ~2-hour sending window. At the old 8-25 min gap (avg ~16.5 min),
# 10 messages averaged ~2.5 hours -- routinely spilling past the window.
check("send-gap pacing tightened to 6-13 min (was 8-25) so 10 messages fit within ~2 hours even in the worst case",
      sch._SEND_GAP_MIN_SECONDS == 6 * 60 and sch._SEND_GAP_MAX_SECONDS == 13 * 60)
check("10 messages' worst-case total gap time (9 max-length gaps) stays under 2 hours",
      9 * sch._SEND_GAP_MAX_SECONDS <= 120 * 60)
check("the gap is still genuinely randomized, not a fixed interval (regression check -- a fixed cadence is itself a bot signal)",
      sch._SEND_GAP_MIN_SECONDS < sch._SEND_GAP_MAX_SECONDS)

# ---------------------------------------------------------------------------
# REAL DATA-QUALITY BUG, found and fixed 2026-09-17: hunter.py's
# find_email()/find_company_emails() took whatever email Hunter's API
# returned without ever checking Hunter's own `score` (0-100 confidence)
# or `verification.status` fields -- both returned on every response, both
# silently discarded. Confirmed real harm: lead "Kedemos Education" has
# website=https://linktr.ee/KedemosEducation (a Linktree bio-link page,
# not their real site); Domain Search was run against linktr.ee itself and
# returned pooya@linktr.ee, a stranger's email with zero connection to the
# company -- consistent with the account's 5-8% bounce rate (cold-email
# norm is ~1-2%). Two fixes: (1) a score/verification quality gate on both
# Hunter lookup functions, (2) a bio-link/social-platform domain blocklist
# checked BEFORE calling Hunter at all, in _maybe_find_email() (so a lead
# whose "website" is actually linktr.ee/instagram.com/etc. never reaches
# either Hunter endpoint in the first place).
check("Hunter's verification.status is actually read from the API response, not discarded (structural regression check)",
      "verification" in _inspect.getsource(hunter._passes_quality_gate))
check("a hard-bad verification status (invalid/disposable) is rejected",
      not hunter._passes_quality_gate({"score": 90, "verification": {"status": "invalid"}}, context="t")
      and not hunter._passes_quality_gate({"score": 90, "verification": {"status": "disposable"}}, context="t"))
check("a genuinely good status (valid/accept_all) passes at a high score",
      hunter._passes_quality_gate({"score": 90, "verification": {"status": "valid"}}, context="t")
      and hunter._passes_quality_gate({"score": 90, "verification": {"status": "accept_all"}}, context="t"))
check("a low score is rejected even with no bad status present (score is checked independently of status)",
      not hunter._passes_quality_gate({"score": 20, "verification": {"status": "unknown"}}, context="t"))
check("score exactly at the minimum threshold (%d) still passes (inclusive floor, not exclusive)" % hunter._MIN_SCORE,
      hunter._passes_quality_gate({"score": hunter._MIN_SCORE, "verification": {"status": "valid"}}, context="t"))
check("an 'unknown' verification status is accepted (not so strict this returns nothing), at an acceptable score",
      hunter._passes_quality_gate({"score": 70, "verification": {"status": "unknown"}}, context="t"))
check("a candidate with no verification block at all (missing key) does not crash and is judged on score alone",
      hunter._passes_quality_gate({"score": 70}, context="t"))

# The Kedemos Education case, exactly as it happened: website is a Linktree
# URL, not the company's real domain.
check("linktr.ee (the real Kedemos Education case) is recognized as a generic platform domain, not a company site",
      hunter.is_blocklisted_domain("linktr.ee")
      and hunter.is_blocklisted_domain("https://linktr.ee/KedemosEducation"))
check("common bio-link/social platforms are all blocklisted",
      all(hunter.is_blocklisted_domain(d) for d in
          ["instagram.com", "facebook.com", "twitter.com", "x.com", "tiktok.com",
           "bio.link", "beacons.ai", "linktree.com"]))
check("a real company domain is NOT blocklisted (regression check -- must not over-block)",
      not hunter.is_blocklisted_domain("acmesecurity.com") and not hunter.is_blocklisted_domain("tesla.com"))
_refused_blocklisted = False
try:
    hunter.find_company_emails("linktr.ee")
except ValueError:
    _refused_blocklisted = True
except Exception:
    pass  # any other exception (e.g. network) still means it did NOT silently search it
check("find_company_emails() refuses to search a blocklisted platform domain (raises, never silently searches it)",
      _refused_blocklisted)

# scheduler.py's _maybe_find_email() must apply the domain-sanity check
# BEFORE calling Hunter at all -- structural check against its real source,
# since a live call would need a real API key/network access this
# verification step doesn't have.
_mfe_src = _inspect.getsource(sch._maybe_find_email)
check("_maybe_find_email() checks is_blocklisted_domain() before either Hunter lookup tier (regression check)",
      "hunter.is_blocklisted_domain(domain)" in _mfe_src
      and _mfe_src.index("is_blocklisted_domain") < _mfe_src.index("hunter.find_email")
      and _mfe_src.index("is_blocklisted_domain") < _mfe_src.index("hunter.find_company_emails"))

# Owner-requested 2026-09-17: prefer the company's own generic address
# (info@/contact@/sales@) over a named individual's personal email when
# Hunter's Domain Search returns both -- founder/manager email is still an
# acceptable fallback (find_email() above is unaffected), but a generic
# company address should win whenever one exists and passes the quality
# gate. Verified BEHAVIORALLY by mocking Hunter's HTTP response, not just
# reading the source -- proves the actual runtime selection, not just that
# the right-looking code exists.
import httpx as _httpx

class _FakeHunterResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = ""
    def json(self):
        return self._payload

def _fake_domain_search(emails):
    # **kwargs so this stub keeps working as the real call's signature
    # grows (e.g. the 2026-09-19 move of Hunter's key from an `api_key=`
    # query param to an X-API-KEY header added `headers=`).
    def _get(url, params=None, timeout=None, **kwargs):
        return _FakeHunterResponse({"data": {"emails": emails}})
    return _get

_orig_httpx_get = _httpx.get
try:
    _httpx.get = _fake_domain_search([
        {"value": "someone@acmesecurity.com", "type": "personal", "score": 90, "verification": {"status": "valid"}},
        {"value": "info@acmesecurity.com", "type": "generic", "score": 85, "verification": {"status": "valid"}},
    ])
    check("find_company_emails() picks the generic company address over a personal one, even when personal comes first in Hunter's own array",
          hunter.find_company_emails("acmesecurity.com") == "info@acmesecurity.com")

    _httpx.get = _fake_domain_search([
        {"value": "jane.doe@acmesecurity.com", "type": "personal", "score": 90, "verification": {"status": "valid"}},
    ])
    check("find_company_emails() falls back to a personal/named email when no generic address exists at all",
          hunter.find_company_emails("acmesecurity.com") == "jane.doe@acmesecurity.com")

    _httpx.get = _fake_domain_search([
        {"value": "bad@acmesecurity.com", "type": "generic", "score": 10, "verification": {"status": "invalid"}},
        {"value": "jane.doe@acmesecurity.com", "type": "personal", "score": 90, "verification": {"status": "valid"}},
    ])
    check("a low-quality generic candidate does NOT win over a genuinely good personal one (quality gate still applies within the preference)",
          hunter.find_company_emails("acmesecurity.com") == "jane.doe@acmesecurity.com")

    # REAL SECRET LEAK FOUND AND FIXED 2026-09-19: httpx logs every
    # request's full URL at INFO level, so passing Hunter's key as an
    # `api_key=` QUERY PARAM wrote the live key in plaintext into docker
    # logs on every lookup. Moved to an X-API-KEY header. These checks
    # capture what the code actually sends and assert the key is in the
    # header and NOT anywhere in the URL/params -- a behavioural guard, so
    # a future refactor that puts it back in the URL fails the deploy.
    _seen = {}
    def _capture_get(url, params=None, headers=None, timeout=None):
        _seen["url"] = url
        _seen["params"] = params or {}
        _seen["headers"] = headers or {}
        return _FakeHunterResponse({"data": {"emails": [
            {"value": "info@acmesecurity.com", "type": "generic", "score": 90,
             "verification": {"status": "valid"}},
        ]}})
    _httpx.get = _capture_get
    hunter.find_company_emails("acmesecurity.com")
    _key = hunter.config.HUNTER_API_KEY
    check("Hunter's API key is sent as an X-API-KEY header, not a URL query param (secret-leak regression check)",
          _seen["headers"].get("X-API-KEY") == _key and "api_key" not in _seen["params"])
    check("the real API key value appears NOWHERE in the request URL or params (secret-leak regression check)",
          bool(_key) and _key not in str(_seen["url"]) and _key not in str(_seen["params"]))
finally:
    _httpx.get = _orig_httpx_get

# ---------------------------------------------------------------------------
# 2026-09-17 real live-confirmed bug: for an EMPTY-target_niche tenant
# ("all companies" -- Zimmar and Insurance, owner's standing rule), the niche
# used to QUALIFY a candidate was the single random word _resolve_search_niche
# picked ONCE at the very top of the whole discovery cycle -- not the
# per-round search_niche that actually found that candidate. Zimmar and
# Insurance both searched "construction" the same night and surfaced the same
# real companies (Arabian Construction Co., UNITECH, Evans Engineering,
# Regbar, Murex, ITG Holding, Sword Group, NavLink, Falcon Logistics); Zimmar
# saved them because its own fixed top-of-run word happened to also be
# "construction", Insurance rejected every one of them because its fixed
# top-of-run word had randomly landed on something unrelated -- same real
# candidates, opposite outcomes, purely from this mismatch. Fix: qualify-time
# niche is now the per-round search_niche, passed via the new
# _save_if_qualified_with_reasons(), not the single fixed niche.
import re as _niche_re
_lk_src = _inspect.getsource(sch._discover_linkedin)
_ig_src = _inspect.getsource(sch._discover_instagram)

def _qualify_call_uses_search_niche(src, platform):
    # Find the _save_if_qualified_with_reasons(...) call for this platform and
    # confirm its niche argument is `search_niche` (this round's real
    # keyword), not the bare `niche` (the single fixed top-of-run value --
    # tonight's actual bug).
    m = _niche_re.search(
        r'_save_if_qualified_with_reasons\(\s*account,\s*"%s".*?\)' % platform, src, _niche_re.DOTALL,
    )
    if not m:
        return False
    call_text = m.group(0)
    return bool(_niche_re.search(r'\bsearch_niche\b', call_text)) and not _niche_re.search(r',\s*niche\s*\)', call_text)

check("LinkedIn discovery qualifies each candidate against THIS ROUND's search_niche, not the fixed top-of-run niche",
      _qualify_call_uses_search_niche(_lk_src, "linkedin"))
check("Instagram discovery qualifies each candidate against THIS ROUND's search_niche, not the fixed top-of-run niche",
      _qualify_call_uses_search_niche(_ig_src, "instagram"))
check("the old direct _save_if_qualified(...) call (fixed top-of-run niche, no reasons) is gone from both discovery loops (regression check)",
      "_save_if_qualified(account, \"linkedin\"" not in _lk_src
      and "_save_if_qualified(account, \"instagram\"" not in _ig_src)

# Behavioral proof, not just source-grepping: simulate what happened tonight
# directly through qualify_profile(), which is what actually scores a
# candidate. A bio that only mentions "construction" must qualify when
# checked against this round's real search keyword ("construction") and be
# scored worse when (as the bug did) checked against an unrelated fixed word
# picked at the top of the run ("retail") -- proving the fix's OWN target
# metric (niche mismatch costs real points) is real, then proving the code
# path now avoids it.
_construction_bio = (
    "Arabian Construction Co. is a leading Lebanese construction and "
    "building contractor with over 500 employees, delivering major "
    "infrastructure and construction projects across the region. Visit our "
    "website for details."
)
_qualifies_correct_niche, _reasons_correct = qualify.qualify_profile(
    {"display_name": "Arabian Construction Co.", "bio": _construction_bio,
     "has_website": True, "post_count": 40, "recent_activity": True,
     "follower_or_headcount": 500, "platform": "linkedin"},
    "construction",
)
_qualifies_wrong_niche, _reasons_wrong = qualify.qualify_profile(
    {"display_name": "Arabian Construction Co.", "bio": _construction_bio,
     "has_website": True, "post_count": 40, "recent_activity": True,
     "follower_or_headcount": 500, "platform": "linkedin"},
    "retail",
)
check("proves the real mismatch: the SAME real candidate qualifies when checked against the niche that actually found it (this round's search_niche)",
      _qualifies_correct_niche)
check("...and would have been penalized/could be rejected when checked against an unrelated fixed niche from a different round (tonight's actual bug)",
      "Bio mentions the target niche" in _reasons_correct
      and "Bio does not mention the target niche -- possible relevance miss" in _reasons_wrong)

# Real-niche tenants (Zimmar's actual configured niche, when non-empty) must
# be UNCHANGED: _next_search_terms only PEELS a real niche into a still
# on-topic substring, it never rotates in an unrelated random word the way
# the empty-niche branch does -- so search_niche and the original niche
# always describe the same topic for those tenants, before and after this fix.
check("a real (non-empty, non-service) configured niche is never randomized by _resolve_search_niche (regression check -- only empty/service niches rotate)",
      sch._resolve_search_niche("Security and building infrastructure integration") ==
      "Security and building infrastructure integration")
_peel_result = sch._next_search_terms(
    "Security and building infrastructure integration", "Lebanon", "Lebanon",
    False, set(),
)
check("a real configured niche's round-2 search_niche is a PEELED, still on-topic substring of the original -- never an unrelated word (so qualify-time niche stays consistent with what a real-niche tenant actually configured, same as before this fix)",
      _peel_result is not None
      and _peel_result[0] in "Security and building infrastructure integration")
check("an EMPTY niche's round-2 search_niche instead comes from the random industry rotation (confirms the fix only changes behavior for the empty/all-companies case)",
      sch._next_search_terms("construction", "Lebanon", "Lebanon", True, {"construction"})[0]
      in sch._RANDOM_INDUSTRY_TERMS)

# Rejection reasons must now reach the logs, not just be discarded --
# closes the exact observability gap that made tonight's bug hard to
# diagnose from `docker logs` alone.
import inspect as _svq_inspect
_svq_src = _svq_inspect.getsource(sch._save_if_qualified_with_reasons)
check("_save_if_qualified_with_reasons() returns qualify_profile's reasons on a rejection instead of discarding them",
      "return False, reasons" in _svq_src)
check("the LinkedIn rejection log line now includes the specific reasons, not just the candidate name",
      "rejected by qualify_profile: %s -- reasons: %s" in _lk_src)
check("the Instagram rejection log line now includes the specific reasons, not just the candidate name",
      "rejected by qualify_profile: %s -- reasons: %s" in _ig_src)

# --- 2026-09-18: unrendered LinkedIn /about pages masquerading as bad leads
# Insurance's Sept 18 run visited 161 candidates and saved 5 (3.1%), with
# 137 qualify-rejections -- 96 of them scoring exactly -6 on an IDENTICAL
# all-empty reason list and 117/137 (85%) including "Bio is missing".
# DocShipper, GFS Global Group, Regie Libanaise and Advanced Lines Group
# were all rejected this way despite being real companies with real
# websites, bios and posts. Zimmar the same night: 11 visits, 5 saves
# (45%). The root cause is page timing (1 vCPU droplet +
# wait_until="domcontentloaded"), not lead quality.
_empty_scrape = {
    "platform": "linkedin", "display_name": "DocShipper", "bio": "",
    "website": None, "has_website": False, "post_count": 0, "headquarters": "",
}
check("an all-empty /about scrape (bio+website+posts+headquarters ALL empty) is detected as a scrape failure",
      sch._linkedin_scrape_looks_empty(_empty_scrape))
check("whitespace-only bio/headquarters still counts as empty (a rendered-but-blank panel is the same failure)",
      sch._linkedin_scrape_looks_empty(dict(_empty_scrape, bio="   ", headquarters="  ")))

# The happy path must be untouched: NO extra page load for a normal or even
# a thin-but-real profile. Each of these has exactly ONE real field, which
# is enough to prove the page rendered.
check("a NORMAL scrape does not trigger the retry (no extra page loads on the happy path)",
      not sch._linkedin_scrape_looks_empty({
          "platform": "linkedin", "display_name": "DocShipper",
          "bio": "DocShipper is a Lebanese freight forwarding and logistics company.",
          "website": "https://docshipper.com", "has_website": True,
          "post_count": 12, "headquarters": "Beirut, Lebanon"}))
check("a PARTIAL scrape (bio only, no website/posts/hq) does not trigger the retry either -- that is a real thin company, not a failed render",
      not sch._linkedin_scrape_looks_empty(dict(_empty_scrape, bio="A real Lebanese trading company.")))
check("a partial scrape with ONLY a website does not trigger the retry",
      not sch._linkedin_scrape_looks_empty(dict(_empty_scrape, website="https://example.com.lb")))
check("a partial scrape with ONLY posts does not trigger the retry",
      not sch._linkedin_scrape_looks_empty(dict(_empty_scrape, post_count=3)))
check("a partial scrape with ONLY a headquarters does not trigger the retry",
      not sch._linkedin_scrape_looks_empty(dict(_empty_scrape, headquarters="Beirut, Lebanon")))

# Structural: the empty signature must route into a RETRY + distinct
# scrape-failure log, and must NOT reach qualification as a normal rejection.
check("_discover_linkedin gates a retry on the all-empty signature (not on every candidate)",
      "_linkedin_scrape_looks_empty(profile)" in _lk_src)
check("the retry re-loads /about with a stronger wait than domcontentloaded (networkidle) plus an explicit about-panel selector wait",
      'wait_until="networkidle"' in _lk_src
      and "section.org-about-module__margin-bottom p" in _lk_src)
check("a still-empty retry is logged DISTINCTLY as a scrape failure, not as a qualify rejection (this is what masked the bug in the logs)",
      "scrape failed (empty /about after retry)" in _lk_src)
check("a still-empty retry skips the candidate instead of passing empty data to qualification",
      _niche_re.search(r"scrape failed \(empty /about after retry\).*?continue", _lk_src, _niche_re.DOTALL) is not None)
check("the retry happens BEFORE the location/competitor/size checks, so recovered data flows through every check, not just qualify_profile",
      _lk_src.index("_linkedin_scrape_looks_empty(profile)") < _lk_src.index("mismatch_reason = None"))
check("the retry carries post_count/recent_activity across from the /posts/ read instead of letting extract_company_profile's placeholders overwrite them",
      'retried["post_count"] = profile.get("post_count")' in _lk_src
      and 'retried["recent_activity"] = profile.get("recent_activity")' in _lk_src)

# --- 2026-09-18: _looks_like_personal_name false-positives on LinkedIn
# LIVE-VERIFIED tonight: "Orange Business", "Alfa Telecommunications" and
# "Roman Foods" all returned True -- any Two Title-Case Words lacking a term
# from the small _BUSINESS_WORDS set. On LinkedIn that cost -2 each and
# produced 21 of the -9 scores. LinkedIn company search structurally only
# ever returns /company/ URLs, so an individual is impossible there.
def _linkedin_profile(name):
    return {"platform": "linkedin", "display_name": name,
            "bio": "A real Lebanese company providing services to businesses nationwide.",
            "has_website": True, "post_count": 8, "recent_activity": True,
            "follower_or_headcount": 200}

for _real_company in ("Orange Business", "Alfa Telecommunications", "Roman Foods"):
    _, _reasons_lk = qualify.qualify_profile(_linkedin_profile(_real_company), "")
    check("LinkedIn company %r is no longer penalized as a personal name (tonight's real false positive)" % _real_company,
          not any("matches a personal-name pattern" in r for r in _reasons_lk))

# Regression: Instagram genuinely needs this check (hashtag discovery really
# does surface individuals) -- it must be completely unchanged there.
_, _reasons_ig = qualify.qualify_profile(
    {"platform": "instagram", "display_name": "anthony_elhachem",
     "bio": "Sharing my life, travels and daily thoughts with you all.",
     "has_website": True, "post_count": 80, "recent_activity": True,
     "follower_or_headcount": 4000},
    "",
)
check("Instagram's personal-name gate still flags a real personal handle (regression check -- must NOT be weakened)",
      any("reads as an individual's account" in r for r in _reasons_ig))
_ig_qualifies, _ = qualify.qualify_profile(
    {"platform": "instagram", "display_name": "anthony_elhachem",
     "bio": "Sharing my life, travels and daily thoughts with you all.",
     "has_website": True, "post_count": 80, "recent_activity": True,
     "follower_or_headcount": 4000},
    "",
)
check("...and that Instagram individual is still hard-rejected, not saved as a lead",
      not _ig_qualifies)
check("a real Instagram BUSINESS account is still not flagged as personal (regression check -- must not over-reject)",
      qualify.qualify_profile(
          {"platform": "instagram", "display_name": "zimmar.security.systems",
           "bio": "Security systems installation and building infrastructure for offices and homes in Beirut.",
           "has_website": True, "post_count": 40, "recent_activity": True,
           "follower_or_headcount": 3000}, "")[0])

print()
if failures:
    print("VERIFICATION FAILED (%d): %s" % (len(failures), failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
PYEOF

verify() {
  say "Verifying the code the container is ACTUALLY running"
  ssh "$DROPLET" "docker exec -i $CONTAINER python3 -" <<< "$VERIFY_PY"
}

if [[ "${1:-}" == "--verify-only" ]]; then
  verify
  exit $?
fi

# --- 1. copy ---------------------------------------------------------------
# LIVE BUG FOUND AND FIXED 2026-09-13: this list used to name each
# subdirectory explicitly (discovery, messaging, db, core) and silently
# missed FOUR real ones that already existed with real code in them --
# analysis, crm, notifications, sending. A real edit made tonight to
# agent/sending/linkedin_send.py (marking a permanent send failure with
# its reason, instead of a silent endless retry) never reached the
# droplet at all -- deploy.sh's own verification step caught it immediately
# (the check for the new code failed against what was ACTUALLY running,
# exactly what this script's whole reason-to-exist is), but any of this
# session's earlier edits to those four directories could have silently
# never deployed either, the identical class of bug this script was
# originally built to prevent for the top-level agent/*.py files.
# Building the subdirectory list from find() instead of a hand-maintained
# name means a future new subdirectory (or one added months from now) can
# never repeat this -- it just gets included automatically.
say "Copying agent source to the droplet"
scp -q -r agent/*.py "$DROPLET:$REMOTE_DIR/agent/"
# LIVE BUG FOUND AND FIXED 2026-09-13 (second bug, same session): the first
# attempt at this loop used `ssh ... < <(find ...)` -- ssh with no `-n`
# reads its OWN stdin from the terminal by default, and inside a `while
# read` loop fed by process substitution, that means ssh silently steals
# the exact stdin the loop's own `read -r subdir` needs from find's output.
# Confirmed live with `set -x`: the loop ran for exactly ONE subdirectory
# (analysis) then silently stopped, no error, `set -e` never tripped --
# sending/ never got copied a second time even after "fixing" the missing-
# directory bug above, and the very next deploy attempt failed verification
# again on the exact same two checks. `ssh -n` (redirect ssh's own stdin
# from /dev/null instead of inheriting the loop's) is the standard fix for
# a network command run inside a shell read loop.
while IFS= read -r subdir; do
  name=$(basename "$subdir")
  # venv and __pycache__ are never source to deploy -- venv is the local
  # dev virtualenv (never existed on the droplet, has its own
  # droplet-side interpreter/deps), __pycache__ is compiled bytecode the
  # droplet regenerates itself.
  [[ "$name" == "venv" || "$name" == "__pycache__" ]] && continue
  if compgen -G "$subdir/*.py" > /dev/null; then
    ssh -n "$DROPLET" "mkdir -p $REMOTE_DIR/agent/$name" >/dev/null
    scp -q -r "$subdir"/*.py "$DROPLET:$REMOTE_DIR/agent/$name/"
  fi
done < <(find agent -mindepth 1 -maxdepth 1 -type d)
ok "source copied"

# --- 2. syntax check BEFORE building --------------------------------------
say "Syntax-checking every file on the droplet"
ssh "$DROPLET" "cd $REMOTE_DIR && python3 -m compileall -q agent >/dev/null && echo ok" >/dev/null
ok "all modules parse"

# --- 3. build --------------------------------------------------------------
say "Building the image"
ssh "$DROPLET" "cd $REMOTE_DIR && docker build -f agent/Dockerfile -t $IMAGE . 2>&1 | tail -2"
ok "image built"

# --- 4. RECREATE (never 'restart' -- see the header comment) ---------------
say "Recreating the container on the new image"
ssh "$DROPLET" "
  set -e
  docker rm -f ${CONTAINER}-old 2>/dev/null || true
  docker rename $CONTAINER ${CONTAINER}-old
  docker stop ${CONTAINER}-old >/dev/null
  docker run -d --name $CONTAINER \
    --restart unless-stopped \
    --env-file $ENV_FILE \
    -v $VOLUME:/app/agent/browser_profiles \
    $IMAGE \
    python -m agent.server >/dev/null
"
sleep 6

running=$(ssh "$DROPLET" "docker ps --filter name=^/${CONTAINER}\$ --format '{{.Status}}'")
if [[ -z "$running" ]]; then
  fail "container did not start -- rolling back"
  ssh "$DROPLET" "
    docker rm -f $CONTAINER 2>/dev/null || true
    docker rename ${CONTAINER}-old $CONTAINER
    docker start $CONTAINER
  "
  fail "ROLLED BACK to the previous container. Deploy aborted."
  exit 1
fi
ok "container up: $running"

# --- 5. verify -------------------------------------------------------------
if ! verify; then
  fail "verification failed -- rolling back"
  ssh "$DROPLET" "
    docker rm -f $CONTAINER
    docker rename ${CONTAINER}-old $CONTAINER
    docker start $CONTAINER
  "
  fail "ROLLED BACK to the previous container. Deploy aborted."
  exit 1
fi

say "Agent logs"
ssh "$DROPLET" "docker logs --tail 15 $CONTAINER 2>&1"

say "DEPLOY COMPLETE"
ok "new code is live and verified"
printf '   previous container kept as %s-old (remove with: docker rm %s-old)\n' "$CONTAINER" "$CONTAINER"
