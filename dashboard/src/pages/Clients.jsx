import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { supabase } from '../lib/supabase'
import TemperatureBadge from '../components/TemperatureBadge'
import { SkeletonCard } from '../components/Skeleton'

const FILTERS = [
  { key: 'all', label: 'All clients' },
  { key: 'numbers', label: 'Numbers found' },
]

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
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('all')

  useEffect(() => {
    async function load() {
      setLoading(true)
      const { data, error: err } = await supabase
        .from('leads')
        .select('*')
        .order('created_at', { ascending: false })
      if (err) setError(err.message)
      else setLeads(data || [])
      setLoading(false)
    }
    load()

    // Live updates -- a lead's score/status/whatsapp_found can change after
    // this page first loads (analysis, sending, reply-detection all run in
    // the background), so this mirrors LiveFeed's realtime pattern rather
    // than requiring a manual refresh to see current state.
    const channel = supabase
      .channel('clients-page-leads')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'leads' }, () => load())
      .subscribe()
    return () => supabase.removeChannel(channel)
  }, [])

  const rows = useMemo(() => {
    let filtered = leads
    if (filter === 'numbers') {
      filtered = filtered.filter((l) => l.whatsapp_found && l.whatsapp_number)
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      filtered = filtered.filter((l) => (l.business_name || '').toLowerCase().includes(q))
    }
    return filtered
  }, [leads, filter, search])

  const numbersCount = useMemo(() => leads.filter((l) => l.whatsapp_found && l.whatsapp_number).length, [leads])

  return (
    <div className="mx-auto max-w-4xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-semibold">
          <span className="accent-text">Clients</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">Every business the agent has ever reached details on, one place, always current.</p>
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
        {rows.map((lead, i) => (
          <motion.div
            key={lead.id}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: Math.min(i * 0.02, 0.3) }}
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
    </div>
  )
}
