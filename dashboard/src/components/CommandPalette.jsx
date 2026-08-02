import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { supabase } from '../lib/supabase'

const PAGES = [
  { to: '/', label: 'Live Feed' },
  { to: '/approval', label: 'Approval Queue' },
  { to: '/instagram-manual', label: 'Instagram Manual Send' },
  { to: '/pipeline', label: 'Pipeline' },
  { to: '/client-history', label: 'Client History' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/accounts', label: 'Account Health' },
  { to: '/run-status', label: 'Run Status' },
  { to: '/settings', label: 'Settings' },
]

/** Cmd/Ctrl+K -- jump to any page or lead instantly. Mounted once in Layout. */
export default function CommandPalette() {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [leadResults, setLeadResults] = useState([])
  const inputRef = useRef(null)
  const navigate = useNavigate()

  const close = useCallback(() => {
    setOpen(false)
    setQuery('')
    setLeadResults([])
  }, [])

  useEffect(() => {
    function onKeyDown(e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((o) => !o)
      } else if (e.key === 'Escape') {
        close()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [close])

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 50)
  }, [open])

  useEffect(() => {
    if (!query.trim()) {
      setLeadResults([])
      return
    }
    const timeout = setTimeout(async () => {
      const { data } = await supabase
        .from('leads')
        .select('id, business_name, platform')
        .ilike('business_name', `%${query.trim()}%`)
        .limit(6)
      setLeadResults(data || [])
    }, 200)
    return () => clearTimeout(timeout)
  }, [query])

  const filteredPages = PAGES.filter((p) => p.label.toLowerCase().includes(query.toLowerCase()))

  function go(to) {
    navigate(to)
    close()
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={close}
          className="fixed inset-0 z-[60] flex items-start justify-center bg-black/50 px-4 pt-24 backdrop-blur-sm"
        >
          <motion.div
            initial={{ opacity: 0, y: -16, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -12, scale: 0.97 }}
            transition={{ duration: 0.15 }}
            onClick={(e) => e.stopPropagation()}
            className="glass w-full max-w-lg overflow-hidden rounded-2xl shadow-2xl"
          >
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Jump to a page or search leads…"
              className="w-full border-b border-slate-800/60 bg-transparent px-4 py-3.5 text-sm text-slate-100 outline-none placeholder:text-slate-600"
            />

            <div className="max-h-80 overflow-y-auto p-2">
              {filteredPages.length > 0 && (
                <div className="mb-1">
                  <p className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-600">Pages</p>
                  {filteredPages.map((p) => (
                    <button
                      key={p.to}
                      onClick={() => go(p.to)}
                      className="block w-full rounded-lg px-3 py-2 text-left text-sm text-slate-300 transition hover:bg-slate-800/50 hover:text-slate-100"
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              )}

              {leadResults.length > 0 && (
                <div>
                  <p className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-600">Leads</p>
                  {leadResults.map((lead) => (
                    <button
                      key={lead.id}
                      onClick={() => go(`/leads/${lead.id}`)}
                      className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm text-slate-300 transition hover:bg-slate-800/50 hover:text-slate-100"
                    >
                      <span>{lead.business_name}</span>
                      <span className="text-xs text-slate-600">{lead.platform}</span>
                    </button>
                  ))}
                </div>
              )}

              {query && filteredPages.length === 0 && leadResults.length === 0 && (
                <p className="px-3 py-4 text-center text-sm text-slate-600">No matches</p>
              )}
            </div>

            <div className="flex items-center justify-between border-t border-slate-800/60 px-4 py-2 text-[11px] text-slate-600">
              <span>↑↓ navigate · ↵ select</span>
              <span>esc to close</span>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
