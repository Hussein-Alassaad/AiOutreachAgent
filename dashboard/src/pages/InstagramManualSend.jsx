import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { supabase } from '../lib/supabase'
import { SkeletonCard } from '../components/Skeleton'

/**
 * Instagram never auto-sends (agent/sending/instagram_queue.py) -- approved
 * Instagram messages land here for a human to send by hand, then tap "Mark
 * as Sent". Mirrors instagram_queue.mark_sent()'s backend logic: bump
 * contact_count, set first_contacted_at once, move the lead to "contacted".
 */
export default function InstagramManualSend() {
  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  async function load() {
    setLoading(true)
    const { data, error: err } = await supabase
      .from('messages')
      .select('*, leads(id, business_name, profile_url, contact_count, first_contacted_at, status)')
      .eq('channel', 'instagram')
      .eq('send_status', 'manual_send_pending')
      .order('created_at', { ascending: true })

    if (err) setError(err.message)
    else setMessages(data)
    setLoading(false)
  }

  useEffect(() => {
    load()
    const channel = supabase
      .channel('instagram-manual-send')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'messages' }, load)
      .subscribe()
    return () => supabase.removeChannel(channel)
  }, [])

  async function markSent(message) {
    const lead = message.leads
    const now = new Date().toISOString()

    await supabase.from('messages').update({ send_status: 'sent', sent_at: now }).eq('id', message.id)

    const contactUpdates = { contact_count: (lead?.contact_count || 0) + 1 }
    if (!lead?.first_contacted_at) contactUpdates.first_contacted_at = now
    await supabase.from('leads').update({ ...contactUpdates, status: 'contacted' }).eq('id', lead.id)

    await supabase.from('pipeline_history').insert({
      lead_id: lead.id, from_stage: lead?.status, to_stage: 'contacted', changed_by: 'agent',
    })
    await supabase.from('client_history').update({ contacted: true }).eq('lead_id', lead.id)
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-semibold">
          Instagram — <span className="accent-text">Manual Send</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Instagram never auto-sends. Send these by hand, then mark them sent.
        </p>
      </motion.header>

      {error && <p className="mt-6 text-sm text-rose-400">{error}</p>}
      {!error && loading && (
        <div className="mt-6 space-y-4">
          <SkeletonCard />
        </div>
      )}
      {!error && !loading && messages.length === 0 && (
        <p className="mt-6 text-sm text-slate-500">Nothing queued for manual send right now.</p>
      )}

      <div className="mt-6 space-y-4">
        <AnimatePresence mode="popLayout">
          {messages.map((message) => (
            <motion.div
              key={message.id}
              layout
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="glass glass-hover rounded-2xl p-4"
            >
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold text-slate-100">
                  {message.leads?.business_name || 'Unknown business'}
                </p>
                {message.leads?.profile_url && (
                  <a
                    href={message.leads.profile_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs text-slate-400 underline hover:text-slate-200"
                  >
                    Open profile →
                  </a>
                )}
              </div>

              <p className="mt-3 whitespace-pre-wrap rounded-xl border border-slate-800/70 bg-slate-950/50 p-3 text-sm text-slate-300">
                {message.edited_body || message.body}
              </p>

              <motion.button
                whileTap={{ scale: 0.96 }}
                onClick={() => markSent(message)}
                className="mt-3 rounded-lg bg-emerald-500/15 px-3 py-1.5 text-xs font-semibold text-emerald-300 ring-1 ring-emerald-400/30 transition hover:bg-emerald-500/25"
              >
                Mark as Sent
              </motion.button>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
