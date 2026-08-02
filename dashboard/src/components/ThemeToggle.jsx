import { motion } from 'framer-motion'
import { useTheme } from '../lib/theme'

function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-3 w-3">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  )
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="h-3 w-3">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z" />
    </svg>
  )
}

/** Sun/moon toggle with a sliding-knob animation -- drop it anywhere. */
export default function ThemeToggle({ className = '' }) {
  const { theme, toggle } = useTheme()
  const isLight = theme === 'light'

  return (
    <button
      onClick={toggle}
      aria-label="Toggle light/dark mode"
      className={`relative flex h-7 w-14 items-center rounded-full border border-slate-700/50 bg-slate-800/60 px-1 transition-colors ${className}`}
    >
      <motion.div
        animate={{ x: isLight ? 26 : 0 }}
        transition={{ type: 'spring', stiffness: 500, damping: 30 }}
        className="flex h-5 w-5 items-center justify-center rounded-full bg-gradient-to-br from-[var(--color-accent-from)] to-[var(--color-accent-to)] text-slate-950"
      >
        {isLight ? <SunIcon /> : <MoonIcon />}
      </motion.div>
    </button>
  )
}
