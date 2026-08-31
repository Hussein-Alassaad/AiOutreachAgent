import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Link } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { debounce } from '../lib/debounce'
import { subscribeChannel } from '../lib/realtimeSubscribe'

const STATUS_STYLE = {
  active: 'ring-emerald-400/30 bg-emerald-500/10 text-emerald-300',
  warned: 'ring-amber-400/30 bg-amber-500/10 text-amber-300',
  paused: 'ring-rose-400/30 bg-rose-500/10 text-rose-300',
}

/**
 * Read-only LinkedIn activity view (Hussein's request) -- LinkedIn already
 * auto-sends (agent/sending/linkedin_send.py), unlike Instagram which needs
 * a human click per message (see InstagramManualSend.jsx), so this page has
 * nothing to action -- it's purely "what has LinkedIn actually done,"
 * pulling sent messages, real replies, and account health into one place
 * instead of Hussein having to piece it together from Approval/Pipeline/
 * Client History separately.
 */
export default function LinkedIn() {
  const [messages, setMessages] = useState([])
  const [replies, setReplies] = useState([])
  const [accounts, setAccounts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  async function load() {
    setLoading(true)
    const [msgRes, replyRes, accountRes] = await Promise.all([
      supabase
        .from('messages')
        .select('id, lead_id, body, edited_body, send_status, sent_at, created_at, leads(id, business_name, profile_url, status)')
        .eq('channel', 'linkedin')
        .order('created_at', { ascending: false })
        .limit(50),
      supabase
        .from('replies')
        .select('id, lead_id, body, replied_at, account_id, leads(id, business_name)')
        .eq('channel', 'linkedin')
        .order('replied_at', { ascending: false })
        .limit(20),
      supabase
        .from('accounts')
        .select('id, label, status, warning_type, warning_reason, linkedin_daily_limit, warmup_current_limit'),
    ])
    if (msgRes.error) setError(msgRes.error.message)
    else setMessages(msgRes.data || [])
    setReplies(replyRes.data || [])
    setAccounts(accountRes.data || [])
    setLoading(false)
  }

  useEffect(() => {
    load()
    const debouncedReload = debounce(load, 400)
    return subscribeChannel('linkedin-page', (ch) =>
      ch
        .on('postgres_changes', { event: '*', schema: 'public', table: 'messages' }, debouncedReload)
        .on('postgres_changes', { event: '*', schema: 'public', table: 'replies' }, debouncedReload)
        .on('postgres_changes', { event: '*', schema: 'public', table: 'accounts' }, debouncedReload)
    )
  }, [])

  const sentCount = messages.filter((m) => m.send_status === 'sent').length

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-semibold">
          <span className="accent-text">LinkedIn</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          LinkedIn sends automatically once approved -- nothing to action here, just what's happened.
        </p>
      </motion.header>

      {error && <p className="mt-6 text-sm text-rose-400">{error}</p>}

      {/* Account health strip -- the accounts actually doing the sending */}
      <div className="mt-6 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {accounts.map((a) => (
          <div key={a.id} className="glass rounded-xl p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-medium text-slate-100">{a.label}</p>
              <span
                className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium capitalize ring-1 ${
                  STATUS_STYLE[a.status] || 'ring-slate-800 bg-slate-900 text-slate-400'
                }`}
              >
                {a.status}
              </span>
            </div>
            <p className="mt-1 text-xs text-slate-500">
              {a.warmup_current_limit ?? '—'}/{a.linkedin_daily_limit ?? '—'} daily limit
            </p>
            {a.warning_reason && <p className="mt-1 text-xs text-amber-400">{a.warning_reason}</p>}
          </div>
        ))}
      </div>

      {/* Sent messages */}
      <div className="mt-8 flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
          Sent messages <span className="text-slate-600">({sentCount})</span>
        </h2>
      </div>
      {!error && loading && <p className="mt-4 text-sm text-slate-500">Loading…</p>}
      {!error && !loading && messages.length === 0 && (
        <p className="mt-4 text-sm text-slate-500">No LinkedIn messages yet.</p>
      )}
      <div className="mt-3 space-y-2">
        <AnimatePresence mode="popLayout">
          {messages.map((m) => (
            <motion.div
              key={m.id}
              layout
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              className="glass glass-hover rounded-xl p-3"
            >
              <div className="flex items-center justify-between gap-3">
                {m.leads?.id ? (
                  <Link to={`/leads/${m.leads.id}`} className="truncate text-sm font-medium text-slate-100 hover:text-[oklch(0.8_0.15_220)]">
                    {m.leads?.business_name || 'Unnamed business'}
                  </Link>
                ) : (
                  <p className="truncate text-sm font-medium text-slate-100">{m.leads?.business_name || 'Unnamed business'}</p>
                )}
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium capitalize ${
                    m.send_status === 'sent'
                      ? 'bg-emerald-500/10 text-emerald-300'
                      : m.send_status === 'failed'
                        ? 'bg-rose-500/10 text-rose-300'
                        : 'bg-slate-800 text-slate-400'
                  }`}
                >
                  {m.send_status}
                </span>
              </div>
              <p className="mt-1.5 line-clamp-2 text-xs text-slate-400">{m.edited_body || m.body}</p>
              <p className="mt-1.5 text-[11px] text-slate-600">
                {m.sent_at ? `Sent ${new Date(m.sent_at).toLocaleString()}` : `Created ${new Date(m.created_at).toLocaleString()}`}
              </p>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {/* Real replies */}
      <div className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
          Replies <span className="text-slate-600">({replies.length})</span>
        </h2>
      </div>
      {!error && !loading && replies.length === 0 && (
        <p className="mt-4 text-sm text-slate-500">No replies detected yet.</p>
      )}
      <div className="mt-3 space-y-2">
        <AnimatePresence mode="popLayout">
          {replies.map((r) => (
            <motion.div
              key={r.id}
              layout
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              className="rounded-lg border border-slate-800 bg-slate-950/40 p-2.5"
            >
              <div className="flex items-center justify-between gap-2 text-[11px] text-slate-500">
                <span>{r.leads?.business_name || 'Unknown lead'}</span>
                <span>{new Date(r.replied_at).toLocaleString()}</span>
              </div>
              <p className="mt-1 text-sm text-slate-200">{r.body}</p>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
