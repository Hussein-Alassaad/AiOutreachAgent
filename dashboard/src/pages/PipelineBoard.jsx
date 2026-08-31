import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { supabase } from '../lib/supabase'
import { debounce } from '../lib/debounce'
import { subscribeChannel } from '../lib/realtimeSubscribe'

// Matches agent/crm/pipeline.py's STAGES exactly.
const STAGES = [
  ['contacted', 'Contacted'],
  ['replied', 'Replied'],
  ['interested', 'Interested'],
  ['meeting_booked', 'Meeting Booked'],
  ['deal_closed', 'Deal Closed'],
  ['lost', 'Lost'],
]

const STAGE_PAGE_SIZE = 20

/**
 * CRM pipeline (spec §7.4) -- native HTML5 drag-and-drop, no extra library.
 * Stages stack vertically (one full-width section per stage) rather than a
 * side-by-side kanban -- easier to scan top-to-bottom on any screen size,
 * and each stage gets its own responsive card grid instead of competing for
 * horizontal space with five other columns.
 * A manual drag move is logged with changed_by="mohamad" so the audit
 * trail can tell it apart from an automatic move (send, reply detection).
 *
 * Each stage is paginated independently (20 cards, "Show more" per column)
 * rather than one flat cap across the whole board -- a flat limit could
 * silently push an entire stage out of view if another stage happened to
 * have more leads. Counts per stage come from a separate head-only query
 * so "Replied (4,213)" is always the true total, not just however many
 * cards are currently rendered.
 */
export default function PipelineBoard() {
  const [leadsByStage, setLeadsByStage] = useState({})
  const [countsByStage, setCountsByStage] = useState({})
  const [visibleByStage, setVisibleByStage] = useState(() => Object.fromEntries(STAGES.map(([s]) => [s, STAGE_PAGE_SIZE])))
  const [loadingMore, setLoadingMore] = useState({})
  const [error, setError] = useState(null)
  const [dragLeadId, setDragLeadId] = useState(null)
  const [dragOverStage, setDragOverStage] = useState(null)

  async function loadStage(stage, limit) {
    const { data, error: err } = await supabase
      .from('leads')
      .select('id, business_name, platform, score, temperature, status')
      .eq('status', stage)
      .order('updated_at', { ascending: false })
      .limit(limit)
    if (err) {
      setError(err.message)
      return
    }
    setLeadsByStage((prev) => ({ ...prev, [stage]: data || [] }))
  }

  async function loadCounts() {
    const results = await Promise.all(
      STAGES.map(([stage]) => supabase.from('leads').select('id', { count: 'exact', head: true }).eq('status', stage))
    )
    setCountsByStage(Object.fromEntries(STAGES.map(([stage], i) => [stage, results[i].count || 0])))
  }

  function loadAll() {
    loadCounts()
    for (const [stage] of STAGES) loadStage(stage, visibleByStage[stage])
  }

  useEffect(() => {
    loadAll()
    const debouncedReload = debounce(loadAll, 400)
    return subscribeChannel('pipeline-board', (ch) =>
      ch.on('postgres_changes', { event: '*', schema: 'public', table: 'leads' }, debouncedReload)
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function showMore(stage) {
    const nextLimit = visibleByStage[stage] + STAGE_PAGE_SIZE
    setLoadingMore((prev) => ({ ...prev, [stage]: true }))
    await loadStage(stage, nextLimit)
    setVisibleByStage((prev) => ({ ...prev, [stage]: nextLimit }))
    setLoadingMore((prev) => ({ ...prev, [stage]: false }))
  }

  async function moveLead(leadId, toStage) {
    const fromStage = Object.keys(leadsByStage).find((s) => leadsByStage[s]?.some((l) => l.id === leadId))
    const lead = fromStage ? leadsByStage[fromStage].find((l) => l.id === leadId) : null
    if (!lead || lead.status === toStage) return

    setLeadsByStage((prev) => ({
      ...prev,
      [fromStage]: prev[fromStage].filter((l) => l.id !== leadId),
      [toStage]: [{ ...lead, status: toStage }, ...(prev[toStage] || [])],
    }))
    // Neither write depends on the other's result -- run them together
    // instead of one after the other.
    await Promise.all([
      supabase.from('leads').update({ status: toStage }).eq('id', leadId),
      supabase.from('pipeline_history').insert({
        lead_id: leadId, from_stage: lead.status, to_stage: toStage, changed_by: 'mohamad',
      }),
    ])
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-semibold">
          <span className="accent-text">Pipeline</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">Drag a card to move it. Every move is logged.</p>
      </motion.header>

      {error && <p className="mt-6 text-sm text-rose-400">{error}</p>}

      <div className="mt-6 space-y-4">
        {STAGES.map(([value, label]) => {
          const stageLeads = leadsByStage[value] || []
          const totalCount = countsByStage[value] ?? stageLeads.length
          const isDragOver = dragOverStage === value
          const hasMore = stageLeads.length < totalCount
          return (
            <div
              key={value}
              onDragOver={(e) => {
                e.preventDefault()
                setDragOverStage(value)
              }}
              onDragLeave={() => setDragOverStage((s) => (s === value ? null : s))}
              onDrop={() => {
                if (dragLeadId) moveLead(dragLeadId, value)
                setDragOverStage(null)
              }}
              className={`glass select-none rounded-2xl p-4 transition-all duration-150 ${
                isDragOver ? 'scale-[1.01] ring-2 ring-[var(--color-accent-from)]/60' : ''
              }`}
            >
              <p className="mb-3 cursor-default text-xs font-semibold uppercase tracking-wider text-slate-400">
                {label} <span className="text-slate-600">({totalCount})</span>
              </p>

              {stageLeads.length === 0 ? (
                <p className="rounded-xl border border-dashed border-slate-800/70 px-3 py-4 text-center text-xs text-slate-600">
                  Drop a lead here
                </p>
              ) : (
                <>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <AnimatePresence>
                      {stageLeads.map((lead) => (
                        <motion.div
                          key={lead.id}
                          layout
                          layoutId={lead.id}
                          initial={{ opacity: 0, scale: 0.9 }}
                          animate={{ opacity: 1, scale: 1 }}
                          exit={{ opacity: 0, scale: 0.9 }}
                          whileDrag={{ scale: 1.05, zIndex: 10 }}
                        >
                          <Link
                            to={`/leads/${lead.id}`}
                            draggable
                            onDragStart={() => setDragLeadId(lead.id)}
                            className="block cursor-grab rounded-xl border border-slate-800/70 bg-slate-950/50 p-3 transition hover:border-[var(--color-accent-from)]/40 active:cursor-grabbing"
                          >
                            <p className="truncate text-sm font-medium text-slate-100">{lead.business_name}</p>
                            <p className="mt-1 text-xs text-slate-500">
                              {lead.platform} {lead.score != null ? `· ${lead.score}/10` : ''}
                            </p>
                          </Link>
                        </motion.div>
                      ))}
                    </AnimatePresence>
                  </div>
                  {hasMore && (
                    <div className="mt-3 flex justify-center">
                      <button
                        type="button"
                        onClick={() => showMore(value)}
                        disabled={loadingMore[value]}
                        className="accent-ring rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:border-[var(--color-accent-from)]/50 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        {loadingMore[value] ? 'Loading…' : `Show more (${totalCount - stageLeads.length} left)`}
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
