import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { supabase } from '../lib/supabase'
import TemperatureBadge from '../components/TemperatureBadge'
import { SkeletonCard } from '../components/Skeleton'
import { debounce } from '../lib/debounce'
import { subscribeChannel } from '../lib/realtimeSubscribe'

// Only the columns this page's cards actually render -- select('*') was
// pulling every jsonb column (weak_points, ai_opportunities, etc.) for
// every row on every load, which is most of why this page got slow as the
// leads table grew.
const _LIST_COLUMNS =
  'id, business_name, platform, industry, score, temperature, status, founder_found, founder_name, whatsapp_found, whatsapp_number, created_at'

const FILTERS = [
  { key: 'all', label: 'All clients' },
  { key: 'numbers', label: 'Numbers found' },
]

const PAGE_SIZE = 40

/**
 * One card per lead the agent has ever reached details on -- reads straight
 * from `leads` (not client_history's frozen-at-analysis-time snapshot),
 * so this always reflects the lead's current status/score/contact info,
 * not what it looked like the moment it was first analyzed. Every card
 * links to LeadDetail (/leads/:id), which already has the full record
 * (weak points, AI opportunities, generated message, notes, follow-up) --
 * this page's job is the organized overview, not duplicating that page.
 */
export default function Clients() {
  const [leads, setLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('all')
  const [page, setPage] = useState(0)
  const [hasMore, setHasMore] = useState(true)

  // Only render one page's worth of cards at a time -- rendering every
  // lead as an animated card (Framer Motion layout + entrance transitions)
  // gets genuinely heavy in the DOM once the table has hundreds/thousands
  // of rows, independent of how fast the underlying query is. Search and
  // the "numbers found" filter both run server-side (not a client-side
  // .filter() over whatever happens to be loaded) so they always search
  // the full table, not just the currently-loaded page.
  async function load(pageNum, { append } = {}) {
    if (append) setLoadingMore(true)
    else setLoading(true)

    let query = supabase
      .from('leads')
      .select(_LIST_COLUMNS)
      .order('created_at', { ascending: false })
      .range(pageNum * PAGE_SIZE, pageNum * PAGE_SIZE + PAGE_SIZE - 1)

    if (filter === 'numbers') query = query.eq('whatsapp_found', true).not('whatsapp_number', 'is', null)
    if (search.trim()) query = query.ilike('business_name', `%${search.trim()}%`)

    const { data, error: err } = await query
    if (err) {
      setError(err.message)
    } else {
      setHasMore((data || []).length === PAGE_SIZE)
      setLeads((prev) => (append ? [...prev, ...(data || [])] : data || []))
    }
    setLoading(false)
    setLoadingMore(false)
  }

  // Search is debounced (typing "acme" shouldn't fire 4 queries, one per
  // keystroke); filter button clicks fire immediately -- a single
  // deliberate click has nothing to debounce against, so applying the same
  // 250ms delay to it was pure dead time before the query even started, on
  // top of the query/render cost itself. Split into two effects so each
  // input gets the right treatment, guarded so the very first mount (both
  // effects would otherwise fire together) only loads once.
  const mounted = useRef(false)
  useEffect(() => {
    if (!mounted.current) return
    const timeout = setTimeout(() => {
      setPage(0)
      load(0)
    }, 250)
    return () => clearTimeout(timeout)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search])

  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true
      load(0) // initial load, no debounce
      return
    }
    setPage(0)
    load(0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter])

  // Both counts are independent of pagination -- `leads.length` only
  // reflects however many pages have been loaded so far, so each needs its
  // own head-only count query, refreshed on the same realtime signal as
  // the visible pages (see below) so a new/changed lead updates the number
  // without a manual refresh.
  const [numbersCount, setNumbersCount] = useState(0)
  const [totalCount, setTotalCount] = useState(null)

  function loadCounts() {
    supabase
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .eq('whatsapp_found', true)
      .not('whatsapp_number', 'is', null)
      .then(({ count }) => setNumbersCount(count || 0))
    supabase
      .from('leads')
      .select('id', { count: 'exact', head: true })
      .then(({ count }) => setTotalCount(count ?? 0))
  }

  useEffect(() => {
    loadCounts()
  }, [])

  useEffect(() => {
    // Live updates -- a lead's score/status/whatsapp_found can change after
    // this page first loads (analysis, sending, reply-detection all run in
    // the background), so this mirrors LiveFeed's realtime pattern rather
    // than requiring a manual refresh to see current state. Debounced so a
    // burst of changes (a full pipeline cycle, a bulk import) collapses
    // into one reload instead of one per row changed. Reloads only the
    // currently-loaded pages (0..page), not the whole table.
    const reloadVisible = () => {
      loadCounts()
      for (let p = 0; p <= page; p++) load(p, { append: p > 0 })
    }
    const debouncedReload = debounce(reloadVisible, 400)
    return subscribeChannel('clients-page-leads', (ch) =>
      ch.on('postgres_changes', { event: '*', schema: 'public', table: 'leads' }, debouncedReload)
    )
  }, [page])

  function loadMore() {
    const next = page + 1
    setPage(next)
    load(next, { append: true })
  }

  const rows = leads

  return (
    <div className="mx-auto max-w-4xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">
            <span className="accent-text">Clients</span>
          </h1>
          <p className="mt-1 text-sm text-slate-500">Every business the agent has ever reached details on, one place, always current.</p>
        </div>
        <div className="shrink-0 rounded-xl border border-slate-800 bg-slate-900/50 px-3.5 py-2 text-right">
          <p className="text-lg font-semibold tabular-nums text-slate-100">{totalCount ?? '—'}</p>
          <p className="text-[11px] uppercase tracking-wide text-slate-500">Total clients</p>
        </div>
      </motion.header>

      <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
        <input
          type="search"
          placeholder="Search by business name…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="accent-ring w-full rounded-xl border border-slate-800 bg-slate-900/50 px-3 py-2.5 text-sm text-slate-100 outline-none transition focus:border-[var(--color-accent-from)]/50 sm:max-w-xs"
        />
        <div className="flex gap-2">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                filter === f.key
                  ? 'border-[var(--color-accent-from)]/60 bg-[var(--color-accent-from)]/10 text-slate-100'
                  : 'border-slate-800 bg-slate-900/50 text-slate-400 hover:text-slate-200'
              }`}
            >
              {f.label}
              {f.key === 'numbers' && numbersCount > 0 && (
                <span className="ml-1.5 rounded-full bg-emerald-500/20 px-1.5 text-emerald-300">{numbersCount}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {filter === 'numbers' && (
        <p className="mt-3 text-xs text-slate-500">
          Leads with a real phone/WhatsApp number the agent extracted from their profile — ready for direct cold-calling.
        </p>
      )}

      {error && <p className="mt-6 text-sm text-rose-400">{error}</p>}
      {!error && loading && (
        <div className="mt-6 space-y-2">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      )}
      {!error && !loading && rows.length === 0 && (
        <p className="mt-6 text-sm text-slate-500">
          {filter === 'numbers' ? 'No numbers found yet.' : 'No clients yet.'}
        </p>
      )}

      <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {/* No per-card stagger delay -- fine for a one-time page load, but
            switching the "All clients"/"Numbers found" filter re-mounts
            up to 40 cards at once, and a cascading i*0.02s delay across all
            of them (up to 300ms) made every filter click feel sluggish on
            top of the query itself. A flat, immediate fade keeps the visual
            polish without the compounding delay. */}
        {rows.map((lead) => (
          <motion.div
            key={lead.id}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.15 }}
          >
            <Link
              to={`/leads/${lead.id}`}
              className="glass glass-hover block h-full rounded-xl p-3.5 transition"
            >
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-medium text-slate-100">{lead.business_name || 'Unnamed business'}</p>
                <TemperatureBadge temperature={lead.temperature} />
              </div>
              <p className="mt-1 text-xs text-slate-500">
                {lead.platform} {lead.industry ? `· ${lead.industry}` : ''} {lead.score != null ? `· ${lead.score}/10` : ''}
              </p>
              <p className="mt-1 text-xs capitalize text-slate-500">Status: {(lead.status || '').replace(/_/g, ' ')}</p>

              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {lead.founder_found && (
                  <span className="rounded-full border border-violet-400/30 bg-violet-500/10 px-2 py-0.5 text-[11px] text-violet-300">
                    Founder: {lead.founder_name}
                  </span>
                )}
                {lead.whatsapp_found && lead.whatsapp_number && (
                  <span className="rounded-full border border-emerald-400/30 bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-300">
                    {lead.whatsapp_number}
                  </span>
                )}
                {lead.contact_count > 0 && (
                  <span className="rounded-full border border-sky-400/30 bg-sky-500/10 px-2 py-0.5 text-[11px] text-sky-300">
                    Contacted ×{lead.contact_count}
                  </span>
                )}
              </div>
            </Link>
          </motion.div>
        ))}
      </div>

      {!loading && hasMore && (
        <div className="mt-6 flex justify-center">
          <button
            type="button"
            onClick={loadMore}
            disabled={loadingMore}
            className="accent-ring rounded-lg border border-slate-800 bg-slate-900/50 px-4 py-2 text-xs font-medium text-slate-300 transition hover:border-[var(--color-accent-from)]/50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {loadingMore ? 'Loading…' : 'Load more'}
          </button>
        </div>
      )}
    </div>
  )
}
