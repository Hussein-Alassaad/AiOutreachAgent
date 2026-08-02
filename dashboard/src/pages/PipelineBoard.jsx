import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { supabase } from '../lib/supabase'

// Matches agent/crm/pipeline.py's STAGES exactly.
const STAGES = [
  ['contacted', 'Contacted'],
  ['replied', 'Replied'],
  ['interested', 'Interested'],
  ['meeting_booked', 'Meeting Booked'],
  ['deal_closed', 'Deal Closed'],
  ['lost', 'Lost'],
]

/**
 * CRM pipeline (spec §7.4) -- native HTML5 drag-and-drop, no extra library.
 * Stages stack vertically (one full-width section per stage) rather than a
 * side-by-side kanban -- easier to scan top-to-bottom on any screen size,
 * and each stage gets its own responsive card grid instead of competing for
 * horizontal space with five other columns.
 * A manual drag move is logged with changed_by="mohamad" so the audit
 * trail can tell it apart from an automatic move (send, reply detection).
 */
export default function PipelineBoard() {
  const [leads, setLeads] = useState([])
  const [error, setError] = useState(null)
  const [dragLeadId, setDragLeadId] = useState(null)
  const [dragOverStage, setDragOverStage] = useState(null)

  async function load() {
    const { data, error: err } = await supabase
      .from('leads')
      .select('id, business_name, platform, score, temperature, status')
      .in('status', STAGES.map(([value]) => value))
    if (err) setError(err.message)
    else setLeads(data)
  }

  useEffect(() => {
    load()
    const channel = supabase
      .channel('pipeline-board')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'leads' }, load)
      .subscribe()
    return () => supabase.removeChannel(channel)
  }, [])

  async function moveLead(leadId, toStage) {
    const lead = leads.find((l) => l.id === leadId)
    if (!lead || lead.status === toStage) return

    setLeads((prev) => prev.map((l) => (l.id === leadId ? { ...l, status: toStage } : l)))
    await supabase.from('leads').update({ status: toStage }).eq('id', leadId)
    await supabase.from('pipeline_history').insert({
      lead_id: leadId, from_stage: lead.status, to_stage: toStage, changed_by: 'mohamad',
    })
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
          const stageLeads = leads.filter((l) => l.status === value)
          const isDragOver = dragOverStage === value
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
                {label} <span className="text-slate-600">({stageLeads.length})</span>
              </p>

              {stageLeads.length === 0 ? (
                <p className="rounded-xl border border-dashed border-slate-800/70 px-3 py-4 text-center text-xs text-slate-600">
                  Drop a lead here
                </p>
              ) : (
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
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
