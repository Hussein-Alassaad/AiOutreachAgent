import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { TYPE_META, timeAgo } from '../lib/activity'
import { SkeletonCard } from './Skeleton'
import EmptyState from './EmptyState'

const COLOR_CLASSES = {
  sky: 'bg-sky-500/15 text-sky-300 ring-sky-400/30',
  rose: 'bg-rose-500/15 text-rose-300 ring-rose-400/30',
  violet: 'bg-violet-500/15 text-violet-300 ring-violet-400/30',
  amber: 'bg-amber-500/15 text-amber-300 ring-amber-400/30',
  emerald: 'bg-emerald-500/15 text-emerald-300 ring-emerald-400/30',
  slate: 'bg-slate-500/15 text-slate-300 ring-slate-400/30',
}

const ICON_PATHS = {
  lead_discovered: 'M12 4.5v15m7.5-7.5h-15',
  lead_hot: 'M12 2.25c1.5 3 .5 4.5-1 6-1.8 1.8-2.5 3.5-2.5 5.25a3.5 3.5 0 0 0 7 0c0-1.1-.4-1.9-1-2.6.9.3 2 1.4 2 3.1a5.5 5.5 0 1 1-11 0c0-3.5 2.2-5 3.5-6.5C10.8 5.8 11.5 4 12 2.25Z',
  founder_found: 'M12 2.25 14.6 8l6.15.5-4.7 4.05L17.5 18.5 12 15.2 6.5 18.5l1.45-5.95L3.25 8.5 9.4 8 12 2.25Z',
  stage_changed: 'M4.5 12h15m0 0-5.25-5.25M19.5 12l-5.25 5.25',
  message_approved: 'M4.5 12.75l6 6 9-13.5',
  message_sent: 'M6 12 3.27 3.94a.75.75 0 0 1 .94-.94l17.25 6.9a.75.75 0 0 1 0 1.4L4.2 18.2a.75.75 0 0 1-.94-.94L6 12Zm0 0h11.25',
  run_started: 'M5.25 5.25 18 12 5.25 18.75V5.25Z',
  run_finished: 'M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
  run_error: 'M12 9v3.75m0 3.75h.008M10.29 3.86 1.82 18a1.5 1.5 0 0 0 1.29 2.25h17.78A1.5 1.5 0 0 0 22.18 18L13.71 3.86a1.5 1.5 0 0 0-2.42 0Z',
}

function ActivityIcon({ type }) {
  const meta = TYPE_META[type] || TYPE_META.lead_discovered
  const path = ICON_PATHS[type] || ICON_PATHS.lead_discovered
  return (
    <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ring-1 ${COLOR_CLASSES[meta.color]}`}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d={path} />
      </svg>
    </span>
  )
}

/**
 * Slide-in drawer showing a merged, real-time log of everything the agent
 * and Mohamad/Hussein have done -- discoveries, pipeline moves, approvals,
 * sends, and runs -- pulled from the actual timestamped columns behind
 * each of those actions (see lib/activity.js), not a synthetic feed.
 */
export default function ActivityFeed({ open, onClose, events, loading }) {
  const navigate = useNavigate()
  const [, forceTick] = useState(0)

  // Relative timestamps ("2m ago") drift stale without a re-render -- tick
  // once a minute while the drawer is open, cheap enough to not matter.
  useEffect(() => {
    if (!open) return
    const id = setInterval(() => forceTick((t) => t + 1), 60000)
    return () => clearInterval(id)
  }, [open])

  function go(leadId) {
    if (!leadId) return
    navigate(`/leads/${leadId}`)
    onClose()
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 z-[70] bg-black/50 backdrop-blur-sm"
          />
          <motion.div
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 340, damping: 36 }}
            className="glass fixed inset-y-0 right-0 z-[71] flex w-full max-w-sm flex-col border-l border-slate-800/60"
          >
            <div className="flex items-center justify-between border-b border-slate-800/60 px-4 py-3.5">
              <p className="accent-text text-sm font-bold uppercase tracking-widest">MJivity</p>
              <button
                onClick={onClose}
                aria-label="Close MJivity feed"
                className="accent-ring rounded-lg p-1 text-slate-500 transition hover:bg-slate-800/50 hover:text-slate-200"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" className="h-4 w-4">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-3">
              {loading && (
                <div className="space-y-3">
                  <SkeletonCard />
                  <SkeletonCard />
                  <SkeletonCard />
                </div>
              )}

              {!loading && events.length === 0 && (
                <EmptyState title="No activity yet" subtitle="Discoveries, approvals, and sends will show up here as they happen." />
              )}

              {!loading && events.length > 0 && (
                <ul className="space-y-1">
                  <AnimatePresence initial={false}>
                    {events.map((event) => (
                      <motion.li
                        key={event.id}
                        layout
                        initial={{ opacity: 0, x: 12 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.2 }}
                      >
                        <button
                          onClick={() => go(event.leadId)}
                          disabled={!event.leadId}
                          className={`flex w-full items-start gap-3 rounded-xl px-2 py-2.5 text-left transition ${
                            event.leadId ? 'hover:bg-slate-800/40' : 'cursor-default'
                          }`}
                        >
                          <ActivityIcon type={event.type} />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium text-slate-200">{event.title}</span>
                            <span className="block truncate text-xs text-slate-500">{event.subtitle}</span>
                          </span>
                          <span className="shrink-0 whitespace-nowrap pt-0.5 text-[11px] text-slate-600">{timeAgo(event.ts)}</span>
                        </button>
                      </motion.li>
                    ))}
                  </AnimatePresence>
                </ul>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
