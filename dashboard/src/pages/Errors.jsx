import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { supabase } from '../lib/supabase'
import { pushToast } from '../lib/toast'
import { Skeleton } from '../components/Skeleton'
import { debounce } from '../lib/debounce'
import { subscribeChannel } from '../lib/realtimeSubscribe'

const STAGE_LABEL = {
  discovery: 'Discovery',
  analysis: 'Analysis',
  message_generation: 'Message generation',
  sending: 'Sending',
  reply_check: 'Reply check',
  followup: 'Follow-up',
}

const FILTERS = [
  { key: 'real', label: 'Real problems' },
  { key: 'expected', label: 'Normal skips' },
  { key: 'all', label: 'All' },
]

/**
 * Every failure the pipeline's own try/except blocks catch (see
 * scheduler.py's log_error() and database/007_add_error_log.sql) --
 * previously these were only ever appended to an in-memory results list
 * that got discarded the moment a cycle function returned, so there was no
 * way to know a send/analysis/generation step failed without manually
 * re-running it and reading the terminal. Hussein asked for exactly this.
 *
 * "Real problems" (is_expected = false) are the default view -- things
 * genuinely worth Hussein/Mohamad's attention. "Normal skips"
 * (NoMessageButtonAvailable, WhatsAppNotConfigured, etc.) are routine,
 * already-understood outcomes kept visible but out of the way by default.
 */
export default function Errors() {
  const [errors, setErrors] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('real')
  const [leadNames, setLeadNames] = useState({})
  const [accountLabels, setAccountLabels] = useState({})

  async function load() {
    setLoading(true)
    const { data, error: err } = await supabase
      .from('error_log')
      .select('id, stage, channel, is_expected, resolved, error_message, lead_id, account_id, occurred_at')
      .order('occurred_at', { ascending: false })
      .limit(200)
    if (err) {
      setError(err.message)
      setLoading(false)
      return
    }
    setErrors(data || [])
    setLoading(false)

    const leadIds = [...new Set((data || []).map((e) => e.lead_id).filter(Boolean))]
    const accountIds = [...new Set((data || []).map((e) => e.account_id).filter(Boolean))]
    if (leadIds.length) {
      const { data: leads } = await supabase.from('leads').select('id, business_name').in('id', leadIds)
      if (leads) setLeadNames(Object.fromEntries(leads.map((l) => [l.id, l.business_name])))
    }
    if (accountIds.length) {
      const { data: accounts } = await supabase.from('accounts').select('id, label').in('id', accountIds)
      if (accounts) setAccountLabels(Object.fromEntries(accounts.map((a) => [a.id, a.label])))
    }
  }

  useEffect(() => {
    load()
    const debouncedLoad = debounce(load, 400)
    return subscribeChannel('errors-page', (ch) =>
      ch.on('postgres_changes', { event: '*', schema: 'public', table: 'error_log' }, debouncedLoad)
    )
  }, [])

  async function markResolved(id) {
    const { error: err } = await supabase.from('error_log').update({ resolved: true }).eq('id', id)
    if (err) pushToast({ title: 'Failed to update', body: err.message })
    else pushToast({ title: 'Marked resolved' })
  }

  const filtered = errors.filter((e) => {
    if (filter === 'real') return !e.is_expected && !e.resolved
    if (filter === 'expected') return e.is_expected
    return true
  })

  const realUnresolvedCount = errors.filter((e) => !e.is_expected && !e.resolved).length

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-semibold">
          <span className="accent-text">Errors</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Everything the agent failed to do, so nothing goes unnoticed.
        </p>
      </motion.header>

      <div className="mt-4 flex gap-2">
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
            {f.key === 'real' && realUnresolvedCount > 0 && (
              <span className="ml-1.5 rounded-full bg-rose-500/20 px-1.5 text-rose-300">{realUnresolvedCount}</span>
            )}
          </button>
        ))}
      </div>

      {error && <p className="mt-6 text-sm text-rose-400">{error}</p>}
      {!error && loading && (
        <div className="mt-6 space-y-2">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      )}
      {!error && !loading && filtered.length === 0 && (
        <p className="mt-6 text-sm text-slate-500">
          {filter === 'real' ? 'No unresolved problems. Everything ran clean.' : 'Nothing here.'}
        </p>
      )}

      <div className="mt-6 space-y-2">
        <AnimatePresence mode="popLayout">
          {filtered.map((err) => (
            <motion.div
              key={err.id}
              layout
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              className={`glass rounded-xl p-3.5 ${err.is_expected ? 'opacity-70' : ''}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="rounded-full border border-slate-700 bg-slate-900/60 px-2 py-0.5 text-[11px] text-slate-300">
                      {STAGE_LABEL[err.stage] || err.stage}
                    </span>
                    {err.channel && (
                      <span className="rounded-full border border-slate-700 bg-slate-900/60 px-2 py-0.5 text-[11px] uppercase tracking-wide text-slate-400">
                        {err.channel}
                      </span>
                    )}
                    {err.is_expected && (
                      <span className="rounded-full border border-sky-400/30 bg-sky-500/10 px-2 py-0.5 text-[11px] text-sky-300">
                        Normal skip
                      </span>
                    )}
                    {err.resolved && (
                      <span className="rounded-full border border-emerald-400/30 bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-300">
                        Resolved
                      </span>
                    )}
                  </div>
                  <p className="mt-1.5 text-sm text-slate-200">{err.error_message}</p>
                  <p className="mt-1 text-xs text-slate-500">
                    {err.lead_id && leadNames[err.lead_id] ? `${leadNames[err.lead_id]} · ` : ''}
                    {err.account_id && accountLabels[err.account_id] ? `${accountLabels[err.account_id]} · ` : ''}
                    {new Date(err.occurred_at).toLocaleString()}
                  </p>
                </div>
                {!err.is_expected && !err.resolved && (
                  <button
                    type="button"
                    onClick={() => markResolved(err.id)}
                    className="accent-ring shrink-0 rounded-lg border border-slate-800 bg-slate-900/50 px-2.5 py-1.5 text-xs font-medium text-slate-300 transition hover:border-emerald-400/40 hover:text-emerald-300"
                  >
                    Mark resolved
                  </button>
                )}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
