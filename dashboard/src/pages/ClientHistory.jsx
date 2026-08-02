import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { supabase } from '../lib/supabase'
import TemperatureBadge from '../components/TemperatureBadge'
import { Skeleton } from '../components/Skeleton'

/**
 * Permanent record of every analyzed lead (spec §7.6) -- client_history
 * never resets, whether or not a lead was ever contacted (see
 * agent/scheduler.py's run_analysis_cycle, which writes here before any
 * message is ever generated).
 */
export default function ClientHistory() {
  const [rows, setRows] = useState([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    async function load() {
      setLoading(true)
      let query = supabase.from('client_history').select('*').order('created_at', { ascending: false })
      if (search.trim()) {
        query = query.ilike('business_name', `%${search.trim()}%`)
      }
      const { data, error: err } = await query
      if (err) setError(err.message)
      else setRows(data)
      setLoading(false)
    }

    const timeout = setTimeout(load, 250) // debounce while typing
    return () => clearTimeout(timeout)
  }, [search])

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-semibold">
          Client <span className="accent-text">History</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">Every lead ever analyzed, permanently.</p>
      </motion.header>

      <input
        type="search"
        placeholder="Search by business name…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="accent-ring mt-4 w-full rounded-xl border border-slate-800 bg-slate-900/50 px-3 py-2.5 text-sm text-slate-100 outline-none transition focus:border-[var(--color-accent-from)]/50"
      />

      {error && <p className="mt-6 text-sm text-rose-400">{error}</p>}
      {!error && loading && (
        <div className="mt-6 space-y-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      )}
      {!error && !loading && rows.length === 0 && <p className="mt-6 text-sm text-slate-500">No matches.</p>}

      <div className="mt-6 space-y-2">
        <AnimatePresence mode="popLayout">
          {rows.map((row) => (
            <motion.div
              key={row.id}
              layout
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              className="glass glass-hover rounded-xl p-3"
            >
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium text-slate-100">{row.business_name || 'Unnamed business'}</p>
                <div className="flex items-center gap-2">
                  {row.contacted && (
                    <span className="rounded-full border border-emerald-400/30 bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-300">
                      Contacted
                    </span>
                  )}
                  <TemperatureBadge temperature={row.temperature} />
                </div>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                {row.platform} {row.industry ? `· ${row.industry}` : ''}{' '}
                {row.score != null ? `· ${row.score}/10` : ''}
              </p>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
