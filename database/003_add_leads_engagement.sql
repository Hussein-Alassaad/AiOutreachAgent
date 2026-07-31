-- ============================================================
-- MIGRATION 003 — add engagement sample storage to leads
-- Run this in the Supabase SQL editor, after 002_add_leads_bio.sql.
-- ============================================================
--
-- WHY THIS IS NEEDED
-- Phase 3's spec asks Instagram discovery to read "bio, followers, posts,
-- engagement" -- engagement was never actually captured. Verified 2026-07-31
-- that a post/reel page's `og:description` meta tag carries its like and
-- comment counts, so discovery now samples that off the one post it found
-- via the hashtag search (see agent/discovery/instagram.py's
-- extract_post_engagement()) and stores it here.
--
-- This is a SAMPLE from one post, not a full-profile average -- computing a
-- true average would mean visiting several of the account's posts, an extra
-- page load per lead this deliberately avoids. LinkedIn leads leave this
-- null; there is no equivalent public per-post engagement signal there.
-- ============================================================

alter table leads add column if not exists engagement_sample jsonb;

comment on column leads.engagement_sample is
  'Instagram only: {likes, comments} sampled from the one post discovery found via the hashtag search. Null for LinkedIn leads.';
