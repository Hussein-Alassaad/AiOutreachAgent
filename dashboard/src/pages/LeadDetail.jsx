import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import { supabase } from '../lib/supabase'
import TemperatureBadge from '../components/TemperatureBadge'

const section = { initial: { opacity: 0, y: 12 }, animate: { opacity: 1, y: 0 } }

function Field({ label, children }) {
  if (children == null || children === '') return null
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <div className="mt-0.5 text-sm text-slate-200">{children}</div>
    </div>
  )
}

function ListField({ label, items }) {
  if (!items?.length) return null
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <ul className="mt-1 space-y-1">
        {items.map((item) => (
          <li key={item} className="text-sm text-slate-300">
            · {item}
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function LeadDetail() {
  const { id } = useParams()
  const [lead, setLead] = useState(null)
  const [history, setHistory] = useState([])
  const [notes, setNotes] = useState('')
  const [followupEnabled, setFollowupEnabled] = useState(false)
  const [followupAt, setFollowupAt] = useState('')
  const [error, setError] = useState(null)
  const [saveMsg, setSaveMsg] = useState(null)

  async function load() {
    const { data: leadData, error: err } = await supabase.from('leads').select('*').eq('id', id).single()
    if (err) {
      setError(err.message)
      return
    }
    setLead(leadData)
    setNotes(leadData.notes || '')

    const { data: historyData } = await supabase
      .from('pipeline_history')
      .select('*')
      .eq('lead_id', id)
      .order('changed_at', { ascending: true })
    setHistory(historyData || [])
  }

  useEffect(() => {
    load()
  }, [id])

  async function saveNotes() {
    await supabase.from('leads').update({ notes }).eq('id', id)
    setSaveMsg('Saved')
    setTimeout(() => setSaveMsg(null), 1500)
  }

  async function scheduleFollowup() {
    if (!followupAt) return
    const maxContacts = 2 // settings.max_contacts_per_lead default; Settings page can override
    if ((lead.contact_count || 0) >= maxContacts) {
      setSaveMsg(`Already at max contacts (${maxContacts})`)
      return
    }
    await supabase.from('follow_ups').insert({
      lead_id: id, enabled: followupEnabled, scheduled_for: followupAt, status: 'scheduled',
    })
    setSaveMsg('Follow-up scheduled')
    setTimeout(() => setSaveMsg(null), 1500)
  }

  if (error) return <p className="p-8 text-sm text-rose-400">{error}</p>
  if (!lead) return <p className="p-8 text-sm text-slate-500">Loading…</p>

  return (
    <div className="mx-auto max-w-2xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{lead.business_name || 'Unnamed business'}</h1>
          <p className="mt-1 text-sm text-slate-500">
            {lead.platform} · status: {lead.status}
          </p>
        </div>
        <TemperatureBadge temperature={lead.temperature} />
      </motion.header>

      <motion.div {...section} transition={{ delay: 0.05 }} className="glass mt-6 grid grid-cols-2 gap-4 rounded-2xl p-4">
        <Field label="Score">{lead.score != null ? `${lead.score}/10` : null}</Field>
        <Field label="Industry">{lead.industry}</Field>
        <Field label="Company size">{lead.company_size}</Field>
        <Field label="Revenue tier">{lead.revenue_tier}</Field>
        <Field label="Founder">
          {lead.founder_found ? `${lead.founder_name} ("${lead.founder_source_phrase}")` : 'Not found'}
        </Field>
        <Field label="WhatsApp">{lead.whatsapp_found ? lead.whatsapp_number : 'Not found'}</Field>
        <Field label="Website">
          {lead.website && (
            <a href={lead.website} target="_blank" rel="noreferrer" className="underline">
              {lead.website}
            </a>
          )}
        </Field>
        <Field label="Profile">
          {lead.profile_url && (
            <a href={lead.profile_url} target="_blank" rel="noreferrer" className="underline">
              Open profile →
            </a>
          )}
        </Field>
        <Field label="Contacts so far">{lead.contact_count}</Field>
      </motion.div>

      <motion.div {...section} transition={{ delay: 0.1 }} className="glass mt-4 space-y-4 rounded-2xl p-4">
        <ListField label="Weak points" items={lead.weak_points} />
        <ListField label="AI opportunities" items={lead.ai_opportunities} />
        {lead.score_reasoning && <Field label="Score reasoning">{lead.score_reasoning}</Field>}
      </motion.div>

      {lead.generated_message && (
        <motion.div {...section} transition={{ delay: 0.15 }} className="glass mt-4 rounded-2xl p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Message ({lead.message_style_used})
          </p>
          <p className="mt-1.5 whitespace-pre-wrap text-sm text-slate-300">{lead.generated_message}</p>
        </motion.div>
      )}

      <motion.div {...section} transition={{ delay: 0.2 }} className="glass mt-4 rounded-2xl p-4">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Notes</p>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
          className="accent-ring mt-2 w-full rounded-xl border border-slate-800 bg-slate-950/50 p-3 text-sm text-slate-200 outline-none transition focus:border-[var(--color-accent-from)]/50"
        />
        <motion.button
          whileTap={{ scale: 0.96 }}
          onClick={saveNotes}
          className="mt-2 rounded-lg bg-slate-800/80 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:bg-slate-700"
        >
          Save notes
        </motion.button>
      </motion.div>

      <motion.div {...section} transition={{ delay: 0.25 }} className="glass mt-4 rounded-2xl p-4">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Follow-up</p>
        <p className="mt-1 text-xs text-slate-500">On/off and timing are your call -- the agent never picks a delay.</p>
        <label className="mt-3 flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={followupEnabled} onChange={(e) => setFollowupEnabled(e.target.checked)} />
          Enable follow-up
        </label>
        <input
          type="datetime-local"
          value={followupAt}
          onChange={(e) => setFollowupAt(e.target.value)}
          className="accent-ring mt-2 w-full rounded-xl border border-slate-800 bg-slate-950/50 p-2 text-sm text-slate-200 outline-none transition focus:border-[var(--color-accent-from)]/50"
        />
        <motion.button
          whileTap={{ scale: 0.96 }}
          onClick={scheduleFollowup}
          className="mt-2 rounded-lg bg-slate-800/80 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:bg-slate-700"
        >
          Schedule
        </motion.button>
        {saveMsg && <p className="mt-2 text-xs text-emerald-400">{saveMsg}</p>}
      </motion.div>

      {history.length > 0 && (
        <motion.div {...section} transition={{ delay: 0.3 }} className="glass mt-4 rounded-2xl p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Stage history</p>
          <ul className="mt-2 space-y-1.5">
            {history.map((h) => (
              <li key={h.id} className="text-xs text-slate-400">
                {new Date(h.changed_at).toLocaleString()} — {h.from_stage || 'start'} → {h.to_stage} ({h.changed_by})
              </li>
            ))}
          </ul>
        </motion.div>
      )}
    </div>
  )
}
