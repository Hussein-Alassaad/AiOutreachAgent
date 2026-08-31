import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { supabase } from '../lib/supabase'
import LeadCard from '../components/LeadCard'
import { SkeletonCard } from '../components/Skeleton'
import EmptyState from '../components/EmptyState'
import { debounce } from '../lib/debounce'
import { subscribeChannel } from '../lib/realtimeSubscribe'

const PAGE_SIZE = 40

/**
 * Today's leads (spec §7.1) -- everything discovered/analyzed since local
 * midnight, hottest first. Realtime-subscribed so new leads appear as the
 * agent works, without a manual refresh. Paginated -- rendering every lead
 * as a fully animated card (score bar, layout transitions) gets genuinely
 * heavy in the DOM on a busy day with hundreds of leads, independent of
 * how fast the underlying query runs.
 */
export default function LiveFeed() {
  const [leads, setLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState(null)
  const [page, setPage] = useState(0)
  const [hasMore, setHasMore] = useState(true)

  async function load(pageNum, { append } = {}) {
    if (append) setLoadingMore(true)
    else setLoading(true)

    const todayStart = new Date()
    todayStart.setHours(0, 0, 0, 0)

    const { data, error: err } = await supabase
      .from('leads')
      .select('*')
      .gte('created_at', todayStart.toISOString())
      .order('score', { ascending: false, nullsFirst: false })
      .order('created_at', { ascending: false })
      .range(pageNum * PAGE_SIZE, pageNum * PAGE_SIZE + PAGE_SIZE - 1)

    if (err) {
      setError(err.message)
    } else {
      setHasMore((data || []).length === PAGE_SIZE)
      setLeads((prev) => (append ? [...prev, ...(data || [])] : data || []))
    }
    setLoading(false)
    setLoadingMore(false)
  }

  useEffect(() => {
    load(0)

    // Debounced so a burst of changes (a full pipeline cycle touching many
    // leads at once, a bulk import) collapses into one reload instead of
    // one full refetch per row changed. Reloads only the currently-loaded
    // pages, not the whole day's leads.
    const reloadVisible = () => {
      for (let p = 0; p <= page; p++) load(p, { append: p > 0 })
    }
    const debouncedReload = debounce(reloadVisible, 400)
    return subscribeChannel('live-feed', (ch) =>
      ch.on('postgres_changes', { event: '*', schema: 'public', table: 'leads' }, debouncedReload)
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page])

  function loadMore() {
    const next = page + 1
    setPage(next)
    load(next, { append: true })
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-semibold">
          Live <span className="accent-text">Feed</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">Today's leads, hottest first.</p>
      </motion.header>

      {error && <p className="mt-6 text-sm text-rose-400">{error}</p>}

      {!error && loading && (
        <div className="mt-6 space-y-3">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      )}

      {!error && !loading && leads.length === 0 && (
        <EmptyState title="No leads yet today" subtitle="They'll appear here the moment the agent finds one." />
      )}

      <div className="mt-6 space-y-3">
        <AnimatePresence mode="popLayout">
          {leads.map((lead) => (
            <LeadCard key={lead.id} lead={lead} />
          ))}
        </AnimatePresence>
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
