import { AnimatePresence, motion } from 'framer-motion'
import { useToasts, dismissToast } from '../lib/toast'

/** Mounted once in Layout -- renders whatever pushToast() has queued. */
export default function ToastContainer() {
  const toasts = useToasts()

  return (
    <div className="pointer-events-none fixed bottom-20 right-4 z-50 flex flex-col gap-2 sm:bottom-6 sm:right-6">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            layout
            initial={{ opacity: 0, y: 24, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, x: 60, scale: 0.9, transition: { duration: 0.2 } }}
            transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            onClick={() => dismissToast(t.id)}
            className="glass pointer-events-auto w-72 max-w-[calc(100vw-2rem)] cursor-pointer overflow-hidden rounded-xl border-l-2 border-l-[var(--color-accent-from)] p-3 shadow-2xl"
          >
            <p className="text-sm font-semibold text-slate-100">{t.title}</p>
            {t.body && <p className="mt-0.5 text-xs text-slate-400">{t.body}</p>}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}
