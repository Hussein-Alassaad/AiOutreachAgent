import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { supabase } from '../lib/supabase'
import TemperatureBadge from '../components/TemperatureBadge'
import { Skeleton } from '../components/Skeleton'

/**
 * Expandable panel showing what a lead actually replied, and from which of
 * our accounts they got it (database/006_add_replies.sql). Lazy-loaded per
 * row on first expand rather than joined into the main query -- most leads
 * were never contacted at all, let alone replied, so eagerly fetching
 * replies for every row in the list would be a mostly-wasted query.
 */
function ReplyPanel({ leadId }) {
  const [replies, setReplies] = useState(null)
  const [accountLabels, setAccountLabels] = useState({})

  useEffect(() => {
    let cancelled = false
    async function load() {
      const { data } = await supabase
        .from('replies')
        .select('*')
        .eq('lead_id', leadId)
        .order('replied_at', { ascending: true })
      if (cancelled) return
      setReplies(data || [])

      const accountIds = [...new Set((data || []).map((r) => r.account_id).filter(Boolean))]
      if (accountIds.length) {
        const { data: accounts } = await supabase.from('accounts').select('id, label').in('id', accountIds)
        if (!cancelled && accounts) {
          setAccountLabels(Object.fromEntries(accounts.map((a) => [a.id, a.label])))
        }
      }
    }
    load()
    return () => { cancelled = true }
  }, [leadId])

  if (replies === null) {
    return <Skeleton className="mt-2 h-10 w-full" />
  }
  if (replies.length === 0) {
    return <p className="mt-2 text-xs text-slate-600">No reply recorded yet.</p>
  }

  return (
    <div className="mt-2 space-y-2">
      {replies.map((reply) => (
        <div key={reply.id} className="rounded-lg border border-slate-800 bg-slate-950/40 p-2.5">
          <div className="flex items-center justify-between gap-2 text-[11px] text-slate-500">
            <span className="uppercase tracking-wide">{reply.channel}</span>
            <span>{accountLabels[reply.account_id] || 'Unknown account'} · {new Date(reply.replied_at).toLocaleString()}</span>
          </div>
          <p className="mt-1 text-sm text-slate-200">{reply.body}</p>
        </div>
      ))}
    </div>
  )
}

// Columns pulled from row.snapshot -- the full leads-table row captured at
// analysis time (see scheduler.py's run_analysis_cycle: snapshot = {...lead,
// ...update_fields}) -- richer than the summary fields client_history keeps
// on its own columns, and exactly the fields a cold-outreach spreadsheet
// needs (profile url, follower count, website, WhatsApp number, founder).
const _EXPORT_COLUMNS = [
  ['business_name', 'Business Name'],
  ['platform', 'Platform'],
  ['industry', 'Industry'],
  ['profile_url', 'Profile URL'],
  ['website', 'Website'],
  ['follower_count', 'Followers'],
  ['whatsapp_number', 'WhatsApp Number'],
  ['founder_name', 'Founder Name'],
  ['score', 'Score'],
  ['temperature', 'Temperature'],
  ['contacted', 'Contacted'],
]

function _csvEscape(value) {
  if (value === null || value === undefined) return ''
  const str = Array.isArray(value) ? value.join('; ') : String(value)
  if (/[",\n]/.test(str)) return `"${str.replace(/"/g, '""')}"`
  return str
}

function rowsToCsv(rows) {
  const header = _EXPORT_COLUMNS.map(([, label]) => _csvEscape(label)).join(',')
  const lines = rows.map((row) => {
    const snapshot = row.snapshot || {}
    return _EXPORT_COLUMNS
      .map(([key]) => _csvEscape(snapshot[key] ?? row[key]))
      .join(',')
  })
  return [header, ...lines].join('\r\n')
}

function downloadCsv(rows) {
  const csv = rowsToCsv(rows)
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  const stamp = new Date().toISOString().slice(0, 10)
  link.href = url
  link.download = `nexaris-leads-${stamp}.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

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
  const [expandedId, setExpandedId] = useState(null)

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
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">
            Client <span className="accent-text">History</span>
          </h1>
          <p className="mt-1 text-sm text-slate-500">Every lead ever analyzed, permanently.</p>
        </div>
        <button
          type="button"
          disabled={rows.length === 0}
          onClick={() => downloadCsv(rows)}
          className="accent-ring shrink-0 rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-2 text-xs font-medium text-slate-200 transition hover:border-[var(--color-accent-from)]/50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Export CSV
        </button>
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
              onClick={() => row.lead_id && setExpandedId(expandedId === row.lead_id ? null : row.lead_id)}
              className={`glass glass-hover rounded-xl p-3 ${row.lead_id ? 'cursor-pointer' : ''}`}
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
              {row.lead_id && (
                <p className="mt-1.5 text-[11px] text-slate-600">
                  {expandedId === row.lead_id ? 'Hide replies ▲' : 'View replies ▼'}
                </p>
              )}
              <AnimatePresence>
                {expandedId === row.lead_id && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    onClick={(e) => e.stopPropagation()}
                    className="overflow-hidden"
                  >
                    <ReplyPanel leadId={row.lead_id} />
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
