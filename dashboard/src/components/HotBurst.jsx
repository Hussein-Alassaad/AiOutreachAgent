import { motion } from 'framer-motion'

const PARTICLES = Array.from({ length: 8 }, (_, i) => {
  const angle = (i / 8) * Math.PI * 2
  return { x: Math.cos(angle) * 26, y: Math.sin(angle) * 26 }
})

/**
 * A brief particle burst -- fires once on mount, meant to sit behind a
 * TemperatureBadge when a lead is "hot". Mounting == "just appeared as hot"
 * closely enough (a fresh card from realtime, or the first paint of an
 * already-hot lead), without needing to track a previous-value diff.
 */
export default function HotBurst() {
  return (
    <span className="pointer-events-none absolute inset-0 flex items-center justify-center">
      {PARTICLES.map((p, i) => (
        <motion.span
          key={i}
          initial={{ x: 0, y: 0, opacity: 1, scale: 1 }}
          animate={{ x: p.x, y: p.y, opacity: 0, scale: 0 }}
          transition={{ duration: 0.7, delay: 0.05 * i, ease: 'easeOut' }}
          className="absolute h-1.5 w-1.5 rounded-full bg-rose-400"
        />
      ))}
    </span>
  )
}
