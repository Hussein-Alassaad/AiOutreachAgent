import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { useSession, signOut } from '../lib/auth'
import { supabase } from '../lib/supabase'
import { pushToast } from '../lib/toast'
import { useActivityFeed } from '../lib/activity'
import Login from '../pages/Login'
import ThemeToggle from './ThemeToggle'
import ToastContainer from './ToastContainer'
import CommandPalette from './CommandPalette'
import ActivityFeed from './ActivityFeed'

// Every section from spec §7, in the order Mohamad/Hussein would actually
// use them day to day. Lead Detail isn't here -- it's reached by clicking a
// lead card, not a nav item.
const NAV = [
  { to: '/', label: 'Live Feed' },
  { to: '/approval', label: 'Approval' },
  { to: '/instagram-manual', label: 'Instagram' },
  { to: '/pipeline', label: 'Pipeline' },
  { to: '/clients', label: 'Clients' },
  { to: '/client-history', label: 'History' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/accounts', label: 'Accounts' },
  { to: '/run-status', label: 'Run Status' },
  { to: '/workflow', label: 'Workflow' },
  { to: '/settings', label: 'Settings' },
]

// One glyph per section -- lets the collapsed rail (see .sidebar-rail in
// index.css) read at a glance instead of showing clipped text, the same way
// an icon-only sidebar rail normally works.
function NavIcon({ to, className = 'h-5 w-5' }) {
  const common = { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.75, strokeLinecap: 'round', strokeLinejoin: 'round', className: `shrink-0 ${className}` }
  switch (to) {
    case '/':
      return <svg {...common}><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z" /></svg>
    case '/approval':
      return <svg {...common}><path d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" /></svg>
    case '/instagram-manual':
      return <svg {...common}><rect x="3.5" y="5" width="17" height="16" rx="4.5" /><circle cx="12" cy="13" r="4" /><circle cx="16.8" cy="8.2" r="0.4" fill="currentColor" /></svg>
    case '/pipeline':
      return <svg {...common}><rect x="3" y="4" width="5" height="16" rx="1.4" /><rect x="9.5" y="4" width="5" height="10.5" rx="1.4" /><rect x="16" y="4" width="5" height="13.5" rx="1.4" /></svg>
    case '/clients':
      return <svg {...common}><circle cx="9" cy="8" r="3.25" /><path d="M3.5 20c0-3.6 2.46-6.25 5.5-6.25S14.5 16.4 14.5 20" /><circle cx="17" cy="9" r="2.4" /><path d="M14.6 20c0-2.8 1.6-5 3.9-5.4" /></svg>
    case '/client-history':
      return <svg {...common}><rect x="3" y="7.5" width="18" height="12.5" rx="2" /><path d="M3 7.5 5.2 4h13.6L21 7.5M10 12.5h4" /></svg>
    case '/analytics':
      return <svg {...common}><rect x="4" y="12" width="4" height="8" rx="1" /><rect x="10" y="7" width="4" height="13" rx="1" /><rect x="16" y="3" width="4" height="17" rx="1" /></svg>
    case '/accounts':
      return <svg {...common}><circle cx="12" cy="8.5" r="3.5" /><path d="M4.5 20a7.5 7.5 0 0 1 15 0" /></svg>
    case '/run-status':
      return <svg {...common}><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15 15l5.5 5.5" /></svg>
    case '/workflow':
      return <svg {...common}><path d="M12 3.5V10M12 10 6 17M12 10l6 7" /><circle cx="12" cy="3.5" r="1.6" fill="currentColor" stroke="none" /><circle cx="6" cy="18" r="1.6" fill="currentColor" stroke="none" /><circle cx="18" cy="18" r="1.6" fill="currentColor" stroke="none" /></svg>
    case '/settings':
      return <svg {...common}><path d="M4 6h9M17 6h3M4 12h3M9 12h11M4 18h13M19 18h1" /><circle cx="13" cy="6" r="2" fill="currentColor" stroke="none" /><circle cx="7" cy="12" r="2" fill="currentColor" stroke="none" /><circle cx="17" cy="18" r="2" fill="currentColor" stroke="none" /></svg>
    default:
      return null
  }
}

function DesktopLink({ to, label, end }) {
  return (
    <NavLink to={to} end={end}>
      {({ isActive }) => (
        <motion.div
          whileHover={{ x: 2 }}
          className={`relative flex items-center gap-3 whitespace-nowrap rounded-xl px-3.5 py-2 text-sm font-medium transition-colors ${
            isActive ? 'text-slate-100' : 'text-slate-500 hover:text-slate-200'
          }`}
        >
          {isActive && (
            <motion.div
              layoutId="nav-active"
              transition={{ type: 'spring', stiffness: 400, damping: 32 }}
              className="absolute inset-0 rounded-xl bg-gradient-to-r from-[var(--color-accent-from)]/20 to-[var(--color-accent-to)]/10 ring-1 ring-[var(--color-accent-from)]/30"
            />
          )}
          <span className="relative flex shrink-0"><NavIcon to={to} /></span>
          <span className="relative hidden whitespace-nowrap md:group-hover:inline">{label}</span>
        </motion.div>
      )}
    </NavLink>
  )
}

function BellButton({ unseenCount, onClick }) {
  return (
    <button
      onClick={onClick}
      aria-label="Open MJivity feed"
      className="accent-ring relative rounded-xl p-2 text-slate-500 transition hover:bg-slate-900/60 hover:text-slate-300"
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" className="h-4 w-4">
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M14.857 17.082a23.85 23.85 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0"
        />
      </svg>
      <AnimatePresence>
        {unseenCount > 0 && (
          <motion.span
            key={unseenCount}
            initial={{ scale: 0.4, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.4, opacity: 0 }}
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-bold text-white shadow-[0_0_10px_-2px_rgba(244,63,94,0.8)]"
          >
            {unseenCount > 9 ? '9+' : unseenCount}
          </motion.span>
        )}
      </AnimatePresence>
    </button>
  )
}

export default function Layout() {
  const { session, loading } = useSession()
  const location = useLocation()
  const [activityOpen, setActivityOpen] = useState(false)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const { events, loading: activityLoading, unseenCount, setDrawerOpen } = useActivityFeed(40, !!session)

  const currentNav = NAV.find((item) => (item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to)))

  function toggleActivity() {
    const next = !activityOpen
    setActivityOpen(next)
    setDrawerOpen(next)
  }
  function closeActivity() {
    setActivityOpen(false)
    setDrawerOpen(false)
  }

  // App-wide: notify the instant a lead crosses into "hot" (score 8-10) OR
  // gets a phone/WhatsApp number extracted, not just when the Live Feed
  // happens to be open -- the spec is explicit that these alerts can't wait
  // for someone to be looking at the right page (mirrors
  // agent/notifications/whatsapp_notify.py's notify_hot_lead/
  // notify_number_found, which fire the same way on the backend).
  useEffect(() => {
    if (!session) return
    const channel = supabase
      .channel('global-hot-lead-watch')
      .on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'leads' }, (payload) => {
        if (payload.new.temperature === 'hot' && payload.old.temperature !== 'hot') {
          pushToast({ title: 'New hot lead', body: payload.new.business_name || 'Unnamed business' })
        }
        if (payload.new.whatsapp_found && !payload.old.whatsapp_found) {
          pushToast({
            title: 'Number found',
            body: `${payload.new.business_name || 'Unnamed business'} — ${payload.new.whatsapp_number}`,
          })
        }
      })
      .subscribe()
    return () => supabase.removeChannel(channel)
  }, [session])

  if (loading) {
    return <div className="min-h-full" />
  }

  if (!session) {
    return <Login />
  }

  return (
    <div className="min-h-full text-slate-100">
      {/* Faint, centered brand wordmark behind every page -- see
          .watermark-text/.watermark-glow/.watermark-rule in index.css.
          Fixed to the viewport so it's one element behind every section
          rather than repeated per page. */}
      <div aria-hidden="true" className="pointer-events-none fixed inset-0 z-0 flex select-none items-center justify-center overflow-hidden">
        <div className="relative flex translate-x-[6%] flex-col items-center gap-6 sm:gap-8">
          <span className="watermark-glow absolute left-1/2 top-1/2 h-[42vw] w-[42vw] -translate-x-1/2 -translate-y-1/2 rounded-full" />
          <span className="watermark-text relative">MJivity</span>
          <span className="relative flex items-center gap-3">
            <span className="watermark-rule h-px w-14 sm:w-24" />
            <span className="watermark-dot h-1.5 w-1.5 rotate-45 rounded-[2px]" />
            <span className="watermark-rule h-px w-14 sm:w-24" />
          </span>
        </div>
      </div>

      <ToastContainer />
      <CommandPalette />
      <ActivityFeed open={activityOpen} onClose={closeActivity} events={events} loading={activityLoading} />
      {/* Desktop sidebar -- collapsed to a slim rail by default, expands to
          full width on hover. It's `md:fixed` (positioned), so per normal
          CSS stacking rules it already paints above the static, non-fixed
          <main>, which is what lets it overlay the page instead of pushing
          content around when it expands. */}
      <div className="sidebar-rail group hidden md:fixed md:inset-y-0 md:z-20 md:flex md:w-16 md:flex-col md:overflow-hidden md:border-r md:border-slate-800/60 md:bg-slate-950/95 md:backdrop-blur-xl md:transition-[width] md:duration-300 md:ease-in-out md:hover:w-56 md:hover:shadow-2xl md:hover:shadow-black/50">
        <div className="flex items-start justify-between px-5 py-6">
          <div className="min-w-0">
            {/* Collapsed: just a monogram badge -- no text, so there's
                nothing that can ever render as a clipped fragment. Expanded
                (hover): the real wordmark + hint. Text is display:none, not
                just clipped by overflow, until the sidebar is actually
                hovered -- opacity/width tricks still let a half-rendered
                word peek through mid-transition, display:none can't. */}
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-[var(--color-accent-from)] to-[var(--color-accent-to)] text-xs font-black text-slate-950 md:group-hover:hidden">
              N
            </div>
            <div className="hidden md:group-hover:block">
              <p className="accent-text whitespace-nowrap text-sm font-bold uppercase tracking-widest">Nexaris</p>
              <p className="mt-0.5 whitespace-nowrap text-[11px] text-slate-600">AI Outreach Agent</p>
              <p className="mt-2 inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-slate-800 px-1.5 py-0.5 text-[10px] text-slate-600">
                <kbd className="font-sans">⌘</kbd>
                <kbd className="font-sans">K</kbd>
                <span className="ml-0.5">to search</span>
              </p>
            </div>
          </div>
          <span className="hidden shrink-0 md:group-hover:block">
            <BellButton unseenCount={unseenCount} onClick={toggleActivity} />
          </span>
        </div>
        <nav className="flex flex-1 flex-col gap-1 px-3">
          {NAV.map((item) => (
            <DesktopLink key={item.to} to={item.to} label={item.label} end={item.to === '/'} />
          ))}
        </nav>
        <div className="flex items-center justify-between gap-2 px-3 pb-6">
          <button
            onClick={signOut}
            className="flex flex-1 items-center gap-3 rounded-xl px-2.5 py-2 text-left text-sm text-slate-600 transition hover:bg-slate-900/60 hover:text-slate-300"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5 shrink-0">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
            </svg>
            <span className="hidden whitespace-nowrap md:group-hover:inline">Sign out</span>
          </button>
          <span className="hidden shrink-0 md:group-hover:block">
            <ThemeToggle />
          </span>
        </div>
      </div>

      {/* Mobile top bar */}
      <div className="flex items-center justify-between border-b border-slate-800/60 bg-slate-950/40 px-4 py-3 backdrop-blur-xl md:hidden">
        <p className="accent-text text-sm font-bold uppercase tracking-widest">Nexaris</p>
        <div className="flex items-center gap-3">
          <BellButton unseenCount={unseenCount} onClick={toggleActivity} />
          <ThemeToggle />
          <button onClick={signOut} className="text-xs text-slate-500">
            Sign out
          </button>
        </div>
      </div>

      <main className="pb-20 md:ml-16 md:pb-0">
        <AnimatePresence mode="wait">
          <motion.div
            key={location.pathname}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
          >
            <Outlet />
          </motion.div>
        </AnimatePresence>
      </main>

      {/* Mobile bottom bar -- a single trigger instead of cramming all ten
          sections into one scrollable strip, which was wrapping labels onto
          a second line and clipping the last item at the screen edge. */}
      <div className="fixed inset-x-0 bottom-0 z-30 flex items-center justify-between border-t border-slate-800/60 bg-slate-950/90 px-4 py-2.5 pb-[max(0.625rem,env(safe-area-inset-bottom))] backdrop-blur-xl md:hidden">
        <div className="min-w-0">
          <p className="text-[10px] uppercase tracking-widest text-slate-600">Section</p>
          <p className="accent-text truncate text-sm font-semibold">{currentNav?.label || 'Menu'}</p>
        </div>
        <button
          onClick={() => setMobileNavOpen(true)}
          aria-label="Open section menu"
          className="accent-ring flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-slate-800 bg-slate-900/60 text-slate-300"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-5 w-5">
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
        </button>
      </div>

      {/* Mobile section picker -- slides up from the bottom, tap a section
          to jump there and close it. */}
      <AnimatePresence>
        {mobileNavOpen && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setMobileNavOpen(false)}
              className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm md:hidden"
            />
            <motion.div
              initial={{ y: '100%' }}
              animate={{ y: 0 }}
              exit={{ y: '100%' }}
              transition={{ type: 'spring', stiffness: 340, damping: 34 }}
              className="glass fixed inset-x-0 bottom-0 z-40 max-h-[75vh] overflow-y-auto rounded-t-3xl border-t border-slate-800/60 p-4 pb-[max(1rem,env(safe-area-inset-bottom))] md:hidden"
            >
              <div className="mx-auto mb-4 h-1.5 w-12 rounded-full bg-slate-700" />
              <div className="grid grid-cols-2 gap-2">
                {NAV.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === '/'}
                    onClick={() => setMobileNavOpen(false)}
                    className={({ isActive }) =>
                      `flex items-center gap-2.5 rounded-xl px-4 py-3 text-sm font-medium transition-colors ${
                        isActive
                          ? 'bg-gradient-to-r from-[var(--color-accent-from)]/20 to-[var(--color-accent-to)]/10 text-slate-100 ring-1 ring-[var(--color-accent-from)]/30'
                          : 'bg-slate-900/50 text-slate-400'
                      }`
                    }
                  >
                    <NavIcon to={item.to} className="h-4 w-4" />
                    {item.label}
                  </NavLink>
                ))}
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  )
}
