import { isConfigured } from './lib/supabase'

/**
 * Phase 0 placeholder.
 *
 * Confirms the dashboard builds, Tailwind works, and Supabase credentials are wired
 * up. The real sections (live feed, approval queue, pipeline board, etc.) arrive in
 * Phase 9 — they're listed below so the build order is visible.
 */

// The 10 sections from spec §7, each mapped to the file that will hold it.
const SECTIONS = [
  ['Daily Live Feed', "Today's leads with full weak points and copyable message"],
  ['Instagram Manual Send', 'Approved IG messages a human sends by hand'],
  ["Mahmoud's Approval", 'Edit + approve — nothing sends until this is cleared'],
  ['CRM Pipeline Board', 'Kanban across the 7 stages, drag and drop'],
  ['Lead Detail', 'Complete record, editable follow-up and notes'],
  ['Client History', 'Permanent searchable database of every lead analysed'],
  ['Analytics & KPIs', 'Every metric, filterable by date, platform, account'],
  ['Account Health', '3 accounts — status, warning type and reason, proxy state'],
  ['Run Status', 'Running / idle, current task, start and finish time, duration'],
  ['Settings', 'Every control editable without touching code'],
]

function StatusRow({ ok, label, detail }) {
  return (
    <div className="flex items-start gap-3 py-2">
      <span
        className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
          ok ? 'bg-emerald-500' : 'bg-slate-600'
        }`}
        aria-hidden="true"
      />
      <div className="min-w-0">
        <p className="text-sm font-medium text-slate-200">{label}</p>
        <p className="text-sm text-slate-400">{detail}</p>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <div className="min-h-full bg-slate-950 text-slate-100">
      <div className="mx-auto max-w-3xl px-5 py-10 sm:px-8 sm:py-14">
        <header>
          <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">
            Nexaris
          </p>
          <h1 className="mt-2 text-2xl font-semibold sm:text-3xl">
            AI Outreach Agent
          </h1>
          <p className="mt-3 text-slate-400">
            Phase 0 — setup complete. The dashboard builds and runs; no real data yet.
          </p>
        </header>

        <section className="mt-10 rounded-xl border border-slate-800 bg-slate-900/50 p-5">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
            Setup status
          </h2>
          <div className="mt-3 divide-y divide-slate-800">
            <StatusRow ok label="Dashboard" detail="React + Tailwind building correctly" />
            <StatusRow
              ok={isConfigured}
              label="Supabase connection"
              detail={
                isConfigured
                  ? 'Credentials found in dashboard/.env'
                  : 'Not configured — add VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY to dashboard/.env (Phase 1)'
              }
            />
          </div>
        </section>

        <section className="mt-8">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
            Coming in Phase 9
          </h2>
          <ol className="mt-3 space-y-2.5">
            {SECTIONS.map(([name, detail], i) => (
              <li
                key={name}
                className="flex gap-3 rounded-lg border border-slate-800/70 bg-slate-900/30 px-4 py-3"
              >
                <span className="w-5 shrink-0 text-sm tabular-nums text-slate-600">
                  {i + 1}
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-200">{name}</p>
                  <p className="text-sm text-slate-500">{detail}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <footer className="mt-10 border-t border-slate-800 pt-5 text-sm text-slate-500">
          Next up: Phase 1 — create the database schema in Supabase.
        </footer>
      </div>
    </div>
  )
}
