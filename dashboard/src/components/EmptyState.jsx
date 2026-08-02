import { motion } from 'framer-motion'

export default function EmptyState({ title, subtitle, icon = 'inbox' }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay: 0.1 }}
      className="mt-12 flex flex-col items-center px-4 text-center"
    >
      <motion.div
        animate={{ y: [0, -8, 0] }}
        transition={{ duration: 2.6, repeat: Infinity, ease: 'easeInOut' }}
        className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-[var(--color-accent-from)]/15 to-[var(--color-accent-to)]/10 ring-1 ring-[var(--color-accent-from)]/20"
      >
        {icon === 'inbox' ? (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-7 w-7 text-[var(--color-accent-from)]">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 12h4l2 3h4l2-3h4M4 12l1.5-6.5A2 2 0 0 1 7.44 4h9.12a2 2 0 0 1 1.94 1.5L20 12M4 12v6a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-7 w-7 text-[var(--color-accent-from)]">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
          </svg>
        )}
      </motion.div>
      <p className="mt-4 text-sm font-medium text-slate-300">{title}</p>
      {subtitle && <p className="mt-1 max-w-xs text-xs text-slate-500">{subtitle}</p>}
    </motion.div>
  )
}
