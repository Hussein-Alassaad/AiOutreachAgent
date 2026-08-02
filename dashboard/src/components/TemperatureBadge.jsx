import HotBurst from './HotBurst'

const STYLES = {
  hot: 'bg-rose-500/10 text-rose-300 border-rose-400/30 shadow-[0_0_16px_-4px_rgba(244,63,94,0.5)]',
  warm: 'bg-amber-500/10 text-amber-300 border-amber-400/30 shadow-[0_0_16px_-4px_rgba(245,158,11,0.4)]',
  cold: 'bg-sky-500/10 text-sky-300 border-sky-400/30 shadow-[0_0_16px_-4px_rgba(56,189,248,0.4)]',
}

const DOT = { hot: 'bg-rose-400', warm: 'bg-amber-400', cold: 'bg-sky-400' }

export default function TemperatureBadge({ temperature, burst = false }) {
  if (!temperature) return null
  const style = STYLES[temperature] || 'bg-slate-900 text-slate-400 border-slate-800'

  return (
    <span className="relative inline-flex">
      {burst && temperature === 'hot' && <HotBurst />}
      <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium capitalize backdrop-blur-sm ${style}`}>
        <span className={`h-1.5 w-1.5 rounded-full ${DOT[temperature] || 'bg-slate-500'} animate-pulse`} />
        {temperature}
      </span>
    </span>
  )
}
