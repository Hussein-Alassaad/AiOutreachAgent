import { useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import TemperatureBadge from './TemperatureBadge'

/**
 * One lead's summary card -- used on the Live Feed. Shows everything
 * needed to judge the lead at a glance (spec §7.1: full details including
 * weak points, founder flag, and a copyable message) without opening the
 * full Lead Detail page.
 */
export default function LeadCard({ lead }) {
  const [copied, setCopied] = useState(false)

  async function copyMessage() {
    if (!lead.generated_message) return
    await navigator.clipboard.writeText(lead.generated_message)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 16, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.96 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className="glass glass-hover rounded-2xl p-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to={`/leads/${lead.id}`} className="block truncate text-sm font-semibold text-slate-100 transition hover:text-[oklch(0.8_0.15_220)]">
            {lead.business_name || 'Unnamed business'}
          </Link>
          <p className="mt-0.5 text-xs text-slate-500">
            {lead.platform} {lead.industry ? `· ${lead.industry}` : ''}
          </p>
        </div>
        <div className="flex min-w-0 shrink items-center gap-2">
          {lead.founder_found && (
            <span className="inline-block max-w-[7rem] truncate rounded-full border border-violet-400/30 bg-violet-500/10 px-2 py-0.5 text-xs text-violet-300 sm:max-w-[10rem]">
              {lead.founder_name}
            </span>
          )}
          <TemperatureBadge temperature={lead.temperature} burst />
        </div>
      </div>

      {lead.score != null && (
        <div className="mt-3 flex items-center gap-2">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-800">
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: `${lead.score * 10}%` }}
              transition={{ duration: 0.6, ease: 'easeOut', delay: 0.1 }}
              className="h-full rounded-full bg-gradient-to-r from-[var(--color-accent-from)] to-[var(--color-accent-to)]"
            />
          </div>
          <span className="text-xs font-medium tabular-nums text-slate-400">{lead.score}/10</span>
        </div>
      )}

      {lead.weak_points?.length > 0 && (
        <ul className="mt-3 space-y-1">
          {lead.weak_points.slice(0, 3).map((point) => (
            <li key={point} className="text-xs text-slate-400">
              · {point}
            </li>
          ))}
          {lead.weak_points.length > 3 && (
            <li className="text-xs text-slate-600">+{lead.weak_points.length - 3} more</li>
          )}
        </ul>
      )}

      {lead.generated_message && (
        <div className="mt-3 rounded-xl border border-slate-800/70 bg-slate-950/50 p-3">
          <p className="line-clamp-3 text-xs text-slate-400">{lead.generated_message}</p>
          <button
            onClick={copyMessage}
            className="accent-ring mt-2 text-xs font-medium text-slate-300 underline decoration-slate-600 underline-offset-2 hover:text-slate-100"
          >
            {copied ? 'Copied ✓' : 'Copy message'}
          </button>
        </div>
      )}
    </motion.div>
  )
}
