-- ============================================================
-- PERFORMANCE FIX — leads(whatsapp_found) had no index.
-- The Clients page's "Numbers found" filter does
-- .eq('whatsapp_found', true).not('whatsapp_number', 'is', null) --
-- every other column that page filters/orders by (created_at, score) is
-- already indexed (see 008_add_leads_perf_indexes.sql); this one was
-- missed, so switching to "Numbers found" does a full table scan.
-- ============================================================

create index if not exists idx_leads_whatsapp_found on leads(whatsapp_found) where whatsapp_found = true;
