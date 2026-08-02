import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import { supabase } from '../lib/supabase'
import KpiCard from '../components/KpiCard'

// Single-series magnitude -> one hue is correct (dataviz skill: multi-color
// categorical rules only apply at 2+ series). Temperature reuses the exact
// hex values behind TemperatureBadge/index.css so the donut's identity
// matches every other screen in the app, with the legend + slice labels
// as the required secondary encoding for the amber/red pair.
const SEQUENTIAL_BLUE = '#3987e5'
const TEMP_COLORS = { hot: '#fb7185', warm: '#fbbf24', cold: '#38bdf8' }

const RANGES = [
  { key: 'all', label: 'All time' },
  { key: 'today', label: 'Today' },
  { key: '7d', label: 'Last 7 days' },
  { key: '30d', label: 'Last 30 days' },
  { key: 'custom', label: 'Custom' },
]

function toDateInputValue(date) {
  return date.toISOString().slice(0, 10)
}

// Resolves a range key to [start, end] instants. "all" returns null bounds
// (no filtering). Custom trusts whatever Mohamad picked in the two date
// inputs, defaulting the end to now if he only set a start.
function resolveBounds(rangeKey, customStart, customEnd) {
  const end = new Date()
  if (rangeKey === 'all') return { start: null, end: null }
  if (rangeKey === 'today') {
    const start = new Date()
    start.setHours(0, 0, 0, 0)
    return { start, end }
  }
  if (rangeKey === '7d') {
    const start = new Date()
    start.setDate(start.getDate() - 6)
    start.setHours(0, 0, 0, 0)
    return { start, end }
  }
  if (rangeKey === '30d') {
    const start = new Date()
    start.setDate(start.getDate() - 29)
    start.setHours(0, 0, 0, 0)
    return { start, end }
  }
  // custom
  const start = customStart ? new Date(`${customStart}T00:00:00`) : null
  const customEndDate = customEnd ? new Date(`${customEnd}T23:59:59`) : end
  return { start, end: customEndDate }
}

// Bucket keys for the trend chart + sparklines. "today" buckets by hour for
// useful granularity on a single day; every other range buckets by day.
function buildBuckets(rangeKey, start, end) {
  if (rangeKey === 'today') {
    return Array.from({ length: 24 }, (_, h) => ({ key: h, label: `${String(h).padStart(2, '0')}:00` }))
  }
  const rangeStart = start ? new Date(start) : (() => {
    const d = new Date()
    d.setDate(d.getDate() - 13)
    d.setHours(0, 0, 0, 0)
    return d
  })()
  const rangeEnd = end ? new Date(end) : new Date()
  rangeStart.setHours(0, 0, 0, 0)
  rangeEnd.setHours(0, 0, 0, 0)

  const buckets = []
  const cur = new Date(rangeStart)
  while (cur <= rangeEnd && buckets.length < 90) {
    const iso = cur.toISOString().slice(0, 10)
    buckets.push({ key: iso, label: iso.slice(5) })
    cur.setDate(cur.getDate() + 1)
  }
  return buckets
}

function countInBucket(rangeKey, bucketKey, leadsList, predicate = () => true) {
  if (rangeKey === 'today') {
    return leadsList.filter((l) => l.created_at && new Date(l.created_at).getHours() === bucketKey && predicate(l)).length
  }
  return leadsList.filter((l) => l.created_at?.slice(0, 10) === bucketKey && predicate(l)).length
}

function ChartCard({ title, children, delay = 0 }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay }}
      className="glass rounded-2xl p-4"
    >
      <p className="text-xs font-medium uppercase tracking-wider text-slate-500">{title}</p>
      <div className="mt-3 h-64">{children}</div>
    </motion.div>
  )
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="glass rounded-lg px-3 py-2 text-xs shadow-xl">
      <p className="font-medium text-slate-200">{label}</p>
      {payload.map((p) => (
        <p key={p.dataKey} style={{ color: p.color || p.fill }}>
          {p.name}: <span className="font-semibold">{p.value}</span>
        </p>
      ))}
    </div>
  )
}

function RangeFilter({ rangeKey, onChange, customStart, customEnd, onCustomChange }) {
  return (
    <div className="mt-4">
      <div className="inline-flex flex-wrap gap-1 rounded-xl border border-slate-800/70 bg-slate-950/40 p-1 backdrop-blur-sm">
        {RANGES.map((r) => (
          <button
            key={r.key}
            onClick={() => onChange(r.key)}
            className={`relative rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              rangeKey === r.key ? 'text-slate-100' : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            {rangeKey === r.key && (
              <motion.span
                layoutId="analytics-range-pill"
                transition={{ type: 'spring', stiffness: 400, damping: 32 }}
                className="absolute inset-0 rounded-lg bg-gradient-to-r from-[var(--color-accent-from)]/25 to-[var(--color-accent-to)]/15 ring-1 ring-[var(--color-accent-from)]/30"
              />
            )}
            <span className="relative">{r.label}</span>
          </button>
        ))}
      </div>

      {rangeKey === 'custom' && (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-400"
        >
          <label className="flex flex-wrap items-center gap-1.5">
            From
            <input
              type="date"
              value={customStart}
              onChange={(e) => onCustomChange({ start: e.target.value, end: customEnd })}
              className="accent-ring min-w-0 max-w-full rounded-lg border border-slate-800 bg-slate-950/50 px-2 py-1 text-slate-200 outline-none"
            />
          </label>
          <label className="flex flex-wrap items-center gap-1.5">
            To
            <input
              type="date"
              value={customEnd}
              onChange={(e) => onCustomChange({ start: customStart, end: e.target.value })}
              className="accent-ring min-w-0 max-w-full rounded-lg border border-slate-800 bg-slate-950/50 px-2 py-1 text-slate-200 outline-none"
            />
          </label>
        </motion.div>
      )}
    </div>
  )
}

export default function Analytics() {
  const [leads, setLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [rangeKey, setRangeKey] = useState('all')
  const [customStart, setCustomStart] = useState(toDateInputValue(new Date(Date.now() - 6 * 86400000)))
  const [customEnd, setCustomEnd] = useState(toDateInputValue(new Date()))

  useEffect(() => {
    async function load() {
      setLoading(true)
      const { data, error: err } = await supabase
        .from('leads')
        .select('platform, temperature, status, contact_count, created_at')
      if (err) setError(err.message)
      else setLeads(data)
      setLoading(false)
    }
    load()
  }, [])

  if (error) return <p className="p-8 text-sm text-rose-400">{error}</p>
  if (loading) return <p className="p-8 text-sm text-slate-500">Loading…</p>

  const { start, end } = resolveBounds(rangeKey, customStart, customEnd)
  const scoped = start
    ? leads.filter((l) => l.created_at && new Date(l.created_at) >= start && new Date(l.created_at) <= end)
    : leads

  const total = scoped.length
  const hot = scoped.filter((l) => l.temperature === 'hot').length
  const warm = scoped.filter((l) => l.temperature === 'warm').length
  const cold = scoped.filter((l) => l.temperature === 'cold').length
  const contacted = scoped.filter((l) => l.contact_count > 0 || l.status === 'contacted').length
  const replied = scoped.filter((l) => l.status === 'replied').length
  const linkedinCount = scoped.filter((l) => l.platform === 'linkedin').length
  const instagramCount = scoped.filter((l) => l.platform === 'instagram').length

  const buckets = buildBuckets(rangeKey, start, end)
  const dailyCounts = buckets.map((b) => ({ date: b.label, leads: countInBucket(rangeKey, b.key, scoped) }))

  // Sparklines get at most the trailing 7 buckets -- a glance, not the full trend.
  const sparkBuckets = buckets.slice(-7)
  const trendFor = (predicate) => sparkBuckets.map((b) => countInBucket(rangeKey, b.key, scoped, predicate))
  const totalTrend = trendFor(() => true)
  const hotTrend = trendFor((l) => l.temperature === 'hot')
  const warmTrend = trendFor((l) => l.temperature === 'warm')
  const coldTrend = trendFor((l) => l.temperature === 'cold')

  const temperatureData = [
    { name: 'Hot', value: hot, key: 'hot' },
    { name: 'Warm', value: warm, key: 'warm' },
    { name: 'Cold', value: cold, key: 'cold' },
  ].filter((d) => d.value > 0)

  const platformData = [
    { name: 'LinkedIn', leads: linkedinCount },
    { name: 'Instagram', leads: instagramCount },
  ]

  const rangeLabel = RANGES.find((r) => r.key === rangeKey)?.label || 'All time'
  const subtitle =
    rangeKey === 'all'
      ? 'All-time totals across every lead ever discovered.'
      : rangeKey === 'custom'
        ? `Totals from ${customStart || '…'} to ${customEnd || '…'}.`
        : `Totals for ${rangeLabel.toLowerCase()}.`
  const chartTitle = rangeKey === 'all' ? 'Leads discovered — last 14 days' : `Leads discovered — ${rangeLabel.toLowerCase()}`

  return (
    <div className="mx-auto max-w-4xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-semibold">
          <span className="accent-text">Analytics</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">{subtitle}</p>
      </motion.header>

      <RangeFilter
        rangeKey={rangeKey}
        onChange={setRangeKey}
        customStart={customStart}
        customEnd={customEnd}
        onCustomChange={({ start: s, end: e }) => {
          setCustomStart(s)
          setCustomEnd(e)
        }}
      />

      <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <KpiCard label="Total leads" value={total} accent trend={totalTrend} />
        <KpiCard label="Hot" value={hot} trend={hotTrend} />
        <KpiCard label="Warm" value={warm} trend={warmTrend} />
        <KpiCard label="Cold" value={cold} trend={coldTrend} />
        <KpiCard label="Contacted" value={contacted} />
        <KpiCard label="Replied" value={replied} />
        <KpiCard label="LinkedIn leads" value={linkedinCount} />
        <KpiCard label="Instagram leads" value={instagramCount} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <ChartCard title={chartTitle} delay={0.05}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={dailyCounts} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="leadsFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={SEQUENTIAL_BLUE} stopOpacity={0.5} />
                    <stop offset="100%" stopColor={SEQUENTIAL_BLUE} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.1)" vertical={false} />
                <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: '#64748b', fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} width={28} />
                <Tooltip content={<ChartTooltip />} />
                <Area
                  type="monotone"
                  dataKey="leads"
                  stroke={SEQUENTIAL_BLUE}
                  strokeWidth={2}
                  fill="url(#leadsFill)"
                  animationDuration={900}
                  activeDot={{ r: 5, strokeWidth: 0 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>

        <ChartCard title="Temperature breakdown" delay={0.1}>
          {temperatureData.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-slate-600">No scored leads yet</div>
          ) : (
            <div className="relative h-full">
              {/* Total sits in the donut's own hole instead of a label drawn
                  outside the ring -- the old on-slice label positioned itself
                  by slice angle and had nowhere to go but off the card for a
                  single 100%-share slice, which is exactly what a one-category
                  board (e.g. only "Hot" leads so far) produces. */}
              <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-2xl font-semibold tabular-nums text-slate-100">{hot + warm + cold}</span>
                <span className="text-[11px] text-slate-500">scored</span>
              </div>
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={temperatureData}
                    dataKey="value"
                    nameKey="name"
                    innerRadius={50}
                    outerRadius={75}
                    paddingAngle={3}
                    animationDuration={900}
                  >
                    {temperatureData.map((entry) => (
                      <Cell key={entry.key} fill={TEMP_COLORS[entry.key]} stroke="none" />
                    ))}
                  </Pie>
                  <Tooltip content={<ChartTooltip />} />
                  <Legend
                    verticalAlign="bottom"
                    height={24}
                    iconType="circle"
                    iconSize={8}
                    formatter={(value) => <span className="text-xs text-slate-400">{value}</span>}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}
        </ChartCard>
      </div>

      <div className="mt-4">
        <ChartCard title="Leads by platform" delay={0.15}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={platformData} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.1)" vertical={false} />
              <XAxis dataKey="name" tick={{ fill: '#64748b', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: '#64748b', fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} width={28} />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(148,163,184,0.06)' }} />
              <Bar dataKey="leads" fill={SEQUENTIAL_BLUE} radius={[6, 6, 0, 0]} maxBarSize={64} animationDuration={900} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>
    </div>
  )
}
