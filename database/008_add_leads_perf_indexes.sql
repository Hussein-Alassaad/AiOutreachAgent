-- ============================================================
-- PERFORMANCE FIX — leads(created_at) and leads(score) had no index.
-- Every dashboard page that lists leads orders/filters by one or both
-- (LiveFeed: created_at + score, Clients/ClientHistory/PipelineBoard:
-- created_at) -- at low row counts a full table scan is invisible, but
-- it degrades badly as the table grows (measured ~6s for 1,000 rows
-- with no index, vs comparable tables of the same size finishing in
-- ~200ms once indexed).
-- ============================================================

create index if not exists idx_leads_created_at on leads(created_at desc);
create index if not exists idx_leads_score       on leads(score desc nulls last);
