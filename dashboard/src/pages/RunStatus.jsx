import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { supabase } from '../lib/supabase'

function duration(startedAt, finishedAt) {
  if (!finishedAt) return null
  const seconds = Math.round((new Date(finishedAt) - new Date(startedAt)) / 1000)
  return `${seconds}s`
}

const STATUS_STYLE = {
  running: 'ring-sky-400/30 bg-sky-500/10 text-sky-300',
  error: 'ring-rose-400/30 bg-rose-500/10 text-rose-300',
  completed: 'ring-emerald-400/30 bg-emerald-500/10 text-emerald-300',
}

const DOT_DELAYS = [0, 0.15, 0.3]

/**
 * A three-dot "typing" indicator for runs still in progress -- reads as
 * "the agent is actively working" the way a chat app's typing cursor does,
 * next to a live-ticking elapsed counter (tick(), below) since a run has
 * no text to actually type.
 */
function TypingDots() {
  return (
    <span className="inline-flex items-center gap-0.5">
      {DOT_DELAYS.map((delay) => (
        <motion.span
          key={delay}
          className="h-1 w-1 rounded-full bg-sky-400"
          animate={{ y: [0, -3, 0], opacity: [0.4, 1, 0.4] }}
          transition={{ duration: 0.9, delay, repeat: Infinity, ease: 'easeInOut' }}
        />
      ))}
    </span>
  )
}

function tick(startedAt) {
  return `${Math.max(0, Math.round((Date.now() - new Date(startedAt)) / 1000))}s`
}

/**
 * Run Status (spec §7.9) -- reads the `runs` table (agent/scheduler.py's
 * repo.start_run / repo.finish_run), showing each account's most recent
 * runs with real start/finish timestamps and duration.
 */
export default function RunStatus() {
  const [runs, setRuns] = useState([])
  const [accounts, setAccounts] = useState({})
  const [error, setError] = useState(null)
  const [now, setNow] = useState(Date.now())

  const hasRunning = runs.some((r) => r.status === 'running')
  useEffect(() => {
    if (!hasRunning) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [hasRunning])

  async function load() {
    const [{ data: runsData, error: err }, { data: accountsData }] = await Promise.all([
      supabase.from('runs').select('*').order('started_at', { ascending: false }).limit(20),
      supabase.from('accounts').select('id, label'),
    ])
    if (err) setError(err.message)
    else setRuns(runsData)
    setAccounts(Object.fromEntries((accountsData || []).map((a) => [a.id, a.label])))
  }

  useEffect(() => {
    load()
    const channel = supabase
      .channel('run-status')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'runs' }, load)
      .subscribe()
    return () => supabase.removeChannel(channel)
  }, [])

  return (
    <div className="mx-auto max-w-2xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-semibold">
          Run <span className="accent-text">Status</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">Last 20 runs across all accounts.</p>
      </motion.header>

      {error && <p className="mt-6 text-sm text-rose-400">{error}</p>}

      <div className="mt-6 space-y-2">
        <AnimatePresence mode="popLayout">
          {runs.map((run) => (
            <motion.div
              key={run.id}
              layout
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              className="glass glass-hover flex items-center justify-between gap-3 rounded-xl p-3"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-slate-100">{accounts[run.account_id] || 'Unknown account'}</p>
                <p className="mt-0.5 text-xs text-slate-500">
                  Started {new Date(run.started_at).toLocaleString()}
                  {run.finished_at && ` · finished ${new Date(run.finished_at).toLocaleTimeString()}`}
                  {duration(run.started_at, run.finished_at) && ` · ${duration(run.started_at, run.finished_at)}`}
                  {run.status === 'running' && now && ` · ${tick(run.started_at)} elapsed`}
                </p>
                {run.notes && <p className="mt-1 text-xs text-rose-400">{run.notes}</p>}
              </div>
              <span
                className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium capitalize ring-1 ${
                  STATUS_STYLE[run.status] || 'ring-slate-800 bg-slate-900 text-slate-400'
                }`}
              >
                {run.status === 'running' && <span className="h-1.5 w-1.5 rounded-full bg-sky-400 animate-pulse" />}
                {run.status}
                {run.status === 'running' && <TypingDots />}
              </span>
            </motion.div>
          ))}
        </AnimatePresence>
        {runs.length === 0 && !error && <p className="text-sm text-slate-500">No runs recorded yet.</p>}
      </div>
    </div>
  )
}
