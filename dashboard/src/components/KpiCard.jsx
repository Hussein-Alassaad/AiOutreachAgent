import { motion } from 'framer-motion'
import { Area, AreaChart, ResponsiveContainer } from 'recharts'
import AnimatedNumber from './AnimatedNumber'

// Matches SEQUENTIAL_BLUE in Analytics.jsx -- a sparkline is a single-series
// magnitude trend riding along with its KPI number, so per the dataviz
// skill's color-by-job rule it gets the same one sequential hue, not an
// identity color of its own.
const SPARK_COLOR = '#3987e5'

function Sparkline({ trend, id }) {
  if (!trend || trend.length < 2) return null
  const data = trend.map((v, i) => ({ i, v }))
  return (
    <div className="mt-2 h-8 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id={`spark-${id}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={SPARK_COLOR} stopOpacity={0.35} />
              <stop offset="100%" stopColor={SPARK_COLOR} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area
            type="monotone"
            dataKey="v"
            stroke={SPARK_COLOR}
            strokeWidth={1.5}
            fill={`url(#spark-${id})`}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

export default function KpiCard({ label, value, detail, accent = false, trend }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ y: -3 }}
      transition={{ duration: 0.25 }}
      className="glass glass-hover relative overflow-hidden rounded-2xl p-4"
    >
      {accent && (
        <div className="pointer-events-none absolute inset-x-0 top-0 h-0.5 bg-gradient-to-r from-[var(--color-accent-from)] to-[var(--color-accent-to)]" />
      )}
      <p className="text-xs font-medium uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`mt-1.5 text-3xl font-semibold tabular-nums ${accent ? 'accent-text' : 'text-slate-100'}`}>
        <AnimatedNumber value={typeof value === 'number' ? value : 0} />
        {typeof value !== 'number' && value}
      </p>
      {detail && <p className="mt-1 text-xs text-slate-500">{detail}</p>}
      <Sparkline trend={trend} id={label} />
    </motion.div>
  )
}
