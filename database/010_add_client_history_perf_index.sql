-- ============================================================
-- PERFORMANCE FIX — client_history had NO indexes at all, not even on
-- analyzed_at, which every single load of the Client History page orders
-- by. At 100k+ rows this was a full table scan on every page load and
-- every search keystroke.
--
-- Also adds leads(updated_at), used by Pipeline Board's now-paginated
-- per-stage queries (see PipelineBoard.jsx) to order each stage's cards
-- most-recently-updated first.
-- ============================================================

create index if not exists idx_client_history_analyzed_at on client_history(analyzed_at desc);
create index if not exists idx_client_history_lead_id on client_history(lead_id);
create index if not exists idx_leads_updated_at on leads(updated_at desc);
