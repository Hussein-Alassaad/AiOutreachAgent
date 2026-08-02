import { useEffect, useState } from 'react'
import { AnimatePresence, motion, useMotionValue, useTransform } from 'framer-motion'
import { supabase } from '../lib/supabase'
import { SkeletonCard } from '../components/Skeleton'
import EmptyState from '../components/EmptyState'

const SWIPE_THRESHOLD = 110

/**
 * A message card that can be swiped right to approve or left to hold
 * (mobile-first gesture; desktop keeps the explicit buttons below as the
 * primary path). Framer Motion's drag x-offset drives the background tint
 * and the "Approve"/"Hold" hint labels so the gesture gives feedback before
 * the user commits to it.
 */
function DraggableCard({ message, onApprove, onHold, children }) {
  const x = useMotionValue(0)
  const background = useTransform(
    x,
    [-SWIPE_THRESHOLD * 1.5, 0, SWIPE_THRESHOLD * 1.5],
    ['rgba(248, 113, 113, 0.16)', 'rgba(0, 0, 0, 0)', 'rgba(52, 211, 153, 0.16)']
  )
  const approveOpacity = useTransform(x, [10, SWIPE_THRESHOLD], [0, 1])
  const holdOpacity = useTransform(x, [-SWIPE_THRESHOLD, -10], [1, 0])

  function handleDragEnd(_, info) {
    if (info.offset.x > SWIPE_THRESHOLD) onApprove(message)
    else if (info.offset.x < -SWIPE_THRESHOLD) onHold(message)
  }

  return (
    <motion.div layout initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95 }} className="relative">
      <motion.div style={{ opacity: approveOpacity }} className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-5 text-sm font-semibold text-emerald-300 md:hidden">
        Approve →
      </motion.div>
      <motion.div style={{ opacity: holdOpacity }} className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-5 text-sm font-semibold text-rose-300 md:hidden">
        ← Hold
      </motion.div>
      <motion.div
        style={{ x, background }}
        drag="x"
        dragConstraints={{ left: 0, right: 0 }}
        dragElastic={0.6}
        onDragEnd={handleDragEnd}
        whileDrag={{ cursor: 'grabbing' }}
        className="glass glass-hover touch-pan-y rounded-2xl p-4"
      >
        {children}
      </motion.div>
    </motion.div>
  )
}

/**
 * Mohamad's approval queue (spec §7.3) -- mirrors the backend logic in
 * agent/messaging/approval.py exactly: editing alone never approves a
 * message, and a lead only advances to "approved" once every one of its
 * messages clears. Duplicated here (not called from Python) because this
 * is the browser -- the two stay in sync by being tested against the same
 * rules, not by sharing code across languages.
 */
export default function ApprovalQueue() {
  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [drafts, setDrafts] = useState({})

  async function load() {
    setLoading(true)
    const { data, error: err } = await supabase
      .from('messages')
      .select('*, leads(id, business_name, platform, score, temperature)')
      .eq('approval_status', 'awaiting')
      .order('created_at', { ascending: true })

    if (err) setError(err.message)
    else setMessages(data)
    setLoading(false)
  }

  useEffect(() => {
    load()
    const channel = supabase
      .channel('approval-queue')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'messages' }, load)
      .subscribe()
    return () => supabase.removeChannel(channel)
  }, [])

  async function maybeAdvanceLead(leadId) {
    const { data: leadMessages } = await supabase.from('messages').select('approval_status').eq('lead_id', leadId)
    if (leadMessages?.length && leadMessages.every((m) => m.approval_status === 'approved')) {
      await supabase.from('leads').update({ status: 'approved' }).eq('id', leadId)
      await supabase.from('pipeline_history').insert({
        lead_id: leadId, from_stage: 'awaiting_approval', to_stage: 'approved', changed_by: 'mohamad',
      })
    }
  }

  async function approve(message) {
    await supabase
      .from('messages')
      .update({ approval_status: 'approved', approved_by: 'Mohamad', approved_at: new Date().toISOString() })
      .eq('id', message.id)
    await maybeAdvanceLead(message.lead_id)
  }

  async function hold(message) {
    await supabase.from('messages').update({ approval_status: 'held' }).eq('id', message.id)
  }

  async function saveEdit(message) {
    const newBody = drafts[message.id]
    if (newBody == null) return
    await supabase.from('messages').update({ edited_body: newBody, approval_status: 'edited' }).eq('id', message.id)
  }

  async function approveAll() {
    for (const message of messages) {
      if (message.approval_status === 'held') continue // holds are deliberate -- Approve All must not override them
      await approve(message)
    }
  }

  const pendingCount = messages.filter((m) => m.approval_status !== 'held').length

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">
            Approval <span className="accent-text">Queue</span>
          </h1>
          <p className="mt-1 text-sm text-slate-500">Nothing sends until you approve it here.</p>
        </div>
        <AnimatePresence>
          {pendingCount > 0 && (
            <motion.span
              key={pendingCount}
              initial={{ scale: 0.5, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.5, opacity: 0 }}
              className="shrink-0 rounded-full bg-rose-500/15 px-3 py-1 text-sm font-bold text-rose-300 shadow-[0_0_16px_-4px_rgba(244,63,94,0.6)] ring-1 ring-rose-400/30"
            >
              {pendingCount}
            </motion.span>
          )}
        </AnimatePresence>
      </motion.header>

      {messages.length > 1 && (
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={approveAll}
          className="mt-4 rounded-xl bg-gradient-to-r from-[var(--color-accent-from)] to-[var(--color-accent-to)] px-4 py-2 text-sm font-semibold text-slate-950 shadow-lg shadow-[var(--color-accent-from)]/20"
        >
          Approve All ({pendingCount})
        </motion.button>
      )}

      {error && <p className="mt-6 text-sm text-rose-400">{error}</p>}
      {!error && loading && (
        <div className="mt-6 space-y-4">
          <SkeletonCard />
          <SkeletonCard />
        </div>
      )}
      {!error && !loading && messages.length === 0 && (
        <EmptyState icon="check" title="All caught up" subtitle="Nothing is waiting on your approval right now." />
      )}

      <div className="mt-6 space-y-4">
        <AnimatePresence mode="popLayout">
          {messages.map((message) => (
            <DraggableCard key={message.id} message={message} onApprove={approve} onHold={hold}>
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold text-slate-100">
                  {message.leads?.business_name || 'Unknown business'}
                </p>
                <span className="rounded-full border border-slate-700 px-2 py-0.5 text-xs uppercase tracking-wide text-slate-400">
                  {message.channel}
                </span>
              </div>

              <textarea
                defaultValue={message.edited_body || message.body}
                onChange={(e) => setDrafts((d) => ({ ...d, [message.id]: e.target.value }))}
                onPointerDown={(e) => e.stopPropagation()}
                rows={4}
                className="accent-ring mt-3 w-full rounded-xl border border-slate-800 bg-slate-950/50 p-3 text-sm text-slate-200 outline-none transition focus:border-[var(--color-accent-from)]/50"
              />

              <div className="mt-3 flex flex-wrap gap-2">
                <motion.button
                  whileTap={{ scale: 0.96 }}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => approve(message)}
                  className="rounded-lg bg-emerald-500/15 px-3 py-1.5 text-xs font-semibold text-emerald-300 ring-1 ring-emerald-400/30 transition hover:bg-emerald-500/25"
                >
                  Approve
                </motion.button>
                <motion.button
                  whileTap={{ scale: 0.96 }}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => saveEdit(message)}
                  className="rounded-lg bg-slate-800/80 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:bg-slate-700"
                >
                  Save edit
                </motion.button>
                <motion.button
                  whileTap={{ scale: 0.96 }}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => hold(message)}
                  className="rounded-lg bg-slate-900/60 px-3 py-1.5 text-xs font-semibold text-slate-400 transition hover:bg-slate-800"
                >
                  Hold
                </motion.button>
              </div>
              <p className="mt-2 text-[11px] text-slate-600 md:hidden">Swipe right to approve, left to hold.</p>
            </DraggableCard>
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
