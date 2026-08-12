import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { supabase } from '../lib/supabase'

const section = { initial: { opacity: 0, y: 12 }, animate: { opacity: 1, y: 0 } }

/**
 * Every editable control backed by the `settings` table (database/schema.sql).
 * This is the ground truth for "every editable control" -- built against the
 * real columns that exist, not a separate spec doc this repo doesn't have.
 */
export default function Settings() {
  const [settings, setSettings] = useState(null)
  const [error, setError] = useState(null)
  const [saveMsg, setSaveMsg] = useState(null)

  useEffect(() => {
    async function load() {
      const { data, error: err } = await supabase.from('settings').select('*').limit(1).single()
      if (err) setError(err.message)
      else setSettings(data)
    }
    load()
  }, [])

  function set(field, value) {
    setSettings((s) => ({ ...s, [field]: value }))
  }

  async function save() {
    const { id, ...fields } = settings
    const { error: err } = await supabase.from('settings').update(fields).eq('id', id)
    if (err) setError(err.message)
    else {
      setSaveMsg('Saved')
      setTimeout(() => setSaveMsg(null), 1500)
    }
  }

  if (error) return <p className="p-8 text-sm text-rose-400">{error}</p>
  if (!settings) return <p className="p-8 text-sm text-slate-500">Loading…</p>

  const inputClass =
    'accent-ring mt-1 w-full rounded-xl border border-slate-800 bg-slate-950/50 px-3 py-2.5 text-sm text-slate-100 outline-none transition focus:border-[var(--color-accent-from)]/50'
  const labelClass = 'text-xs font-medium uppercase tracking-wide text-slate-500'

  return (
    <div className="mx-auto max-w-2xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-semibold">
          <span className="accent-text">Settings</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">Every control here is live -- changes apply on the agent's next run.</p>
      </motion.header>

      <motion.section {...section} transition={{ delay: 0.05 }} className="glass mt-6 space-y-4 rounded-2xl p-4">
        <p className="text-sm font-semibold text-slate-200">Targeting</p>
        <label className="block">
          <span className={labelClass}>Target niche</span>
          <input className={inputClass} value={settings.target_niche || ''} onChange={(e) => set('target_niche', e.target.value)} />
        </label>
        <label className="block">
          <span className={labelClass}>Target industry</span>
          <input className={inputClass} value={settings.target_industry || ''} onChange={(e) => set('target_industry', e.target.value)} />
        </label>
        <label className="block">
          <span className={labelClass}>Target location</span>
          <input className={inputClass} value={settings.target_location || ''} onChange={(e) => set('target_location', e.target.value)} />
        </label>
        <label className="block">
          <span className={labelClass}>Target business type</span>
          <input
            className={inputClass}
            value={settings.target_business_type || ''}
            onChange={(e) => set('target_business_type', e.target.value)}
          />
        </label>
        <label className="block">
          <span className={labelClass}>Outreach languages (comma-separated)</span>
          <input
            className={inputClass}
            value={(settings.outreach_languages || []).join(', ')}
            onChange={(e) => set('outreach_languages', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))}
          />
        </label>
      </motion.section>

      <motion.section {...section} transition={{ delay: 0.1 }} className="glass mt-4 space-y-4 rounded-2xl p-4">
        <p className="text-sm font-semibold text-slate-200">Message style</p>
        <label className="block">
          <span className={labelClass}>Style</span>
          <select
            className={inputClass}
            value={settings.message_style || 'discovery'}
            onChange={(e) => set('message_style', e.target.value)}
          >
            <option value="discovery">Discovery (softer, curiosity-driven)</option>
            <option value="direct">Direct (names the weak point outright)</option>
          </select>
        </label>
        <label className="block">
          <span className={labelClass}>Rotate style after (days)</span>
          <input
            type="number"
            className={inputClass}
            value={settings.style_duration_days ?? ''}
            onChange={(e) => set('style_duration_days', Number(e.target.value))}
          />
        </label>
      </motion.section>

      <motion.section {...section} transition={{ delay: 0.15 }} className="glass mt-4 space-y-4 rounded-2xl p-4">
        <p className="text-sm font-semibold text-slate-200">Contact rules</p>
        <label className="block">
          <span className={labelClass}>Default re-contact gap (days)</span>
          <input
            type="number"
            className={inputClass}
            value={settings.default_recontact_gap_days ?? ''}
            onChange={(e) => set('default_recontact_gap_days', Number(e.target.value))}
          />
        </label>
        <label className="block">
          <span className={labelClass}>Max contacts per lead</span>
          <input
            type="number"
            className={inputClass}
            value={settings.max_contacts_per_lead ?? ''}
            onChange={(e) => set('max_contacts_per_lead', Number(e.target.value))}
          />
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input
            type="checkbox"
            checked={settings.approval_required ?? true}
            onChange={(e) => set('approval_required', e.target.checked)}
          />
          Require approval before sending (hard gate)
        </label>
        <label className="block">
          <span className={labelClass}>Remind me about pending approvals after (hours)</span>
          <input
            type="number"
            className={inputClass}
            value={settings.approval_reminder_hours ?? ''}
            onChange={(e) => set('approval_reminder_hours', Number(e.target.value))}
          />
        </label>
      </motion.section>

      <motion.section {...section} transition={{ delay: 0.2 }} className="glass mt-4 space-y-4 rounded-2xl p-4">
        <p className="text-sm font-semibold text-slate-200">WhatsApp notification recipients</p>
        <label className="block">
          <span className={labelClass}>Recipient 1 (e.g. Hussein)</span>
          <input
            className={inputClass}
            placeholder="+961..."
            value={settings.whatsapp_recipient_1 || ''}
            onChange={(e) => set('whatsapp_recipient_1', e.target.value)}
          />
        </label>
        <label className="block">
          <span className={labelClass}>Recipient 2 (e.g. Mohamad)</span>
          <input
            className={inputClass}
            placeholder="+961..."
            value={settings.whatsapp_recipient_2 || ''}
            onChange={(e) => set('whatsapp_recipient_2', e.target.value)}
          />
        </label>
      </motion.section>

      <div className="mt-4 flex items-center gap-3">
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.97 }}
          onClick={save}
          className="rounded-xl bg-gradient-to-r from-[var(--color-accent-from)] to-[var(--color-accent-to)] px-4 py-2.5 text-sm font-semibold text-slate-950 shadow-lg shadow-[var(--color-accent-from)]/20"
        >
          Save settings
        </motion.button>
        {saveMsg && <p className="text-sm text-emerald-400">{saveMsg}</p>}
      </div>
    </div>
  )
}
