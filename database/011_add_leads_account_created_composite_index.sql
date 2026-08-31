-- ============================================================
-- PERFORMANCE FIX — leads had separate single-column indexes on
-- account_id and created_at, but the Clients/Live Feed pages filter by
-- account_id AND sort by created_at together. Postgres can only pick one
-- index for this query shape, so it was scanning by created_at and then
-- manually filtering out non-matching accounts row by row afterward
-- (confirmed live: "Rows Removed by Filter: 195" on a 50-row LIMIT).
--
-- A composite index on (account_id, created_at) lets Postgres jump
-- straight to one account's rows, already in the right sort order --
-- no post-filtering needed.
-- ============================================================

create index if not exists idx_leads_account_created on leads(account_id, created_at desc);
