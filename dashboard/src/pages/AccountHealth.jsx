import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { supabase } from '../lib/supabase'

const STATUS_STYLE = {
  active: 'bg-emerald-500/10 text-emerald-300 ring-emerald-400/30',
  warned: 'bg-amber-500/10 text-amber-300 ring-amber-400/30',
  paused: 'bg-rose-500/10 text-rose-300 ring-rose-400/30',
}
const STATUS_DOT = { active: 'bg-emerald-400', warned: 'bg-amber-400', paused: 'bg-rose-400' }

/**
 * Account Health (spec §7.8) -- redistribution is deliberately a human
 * decision (core rule R9, see agent/db/repositories.py's set_account_warning):
 * the agent only raises redistribute_flag as a suggestion, it never acts on
 * it. This toggle is that decision point.
 */
const inputClass =
  'accent-ring mt-1 w-full rounded-lg border border-slate-800 bg-slate-950/50 px-2.5 py-1.5 text-xs text-slate-200 outline-none transition focus:border-[var(--color-accent-from)]/50'
const labelClass = 'text-[11px] font-medium uppercase tracking-wide text-slate-500'

// S4-S6/S12 (spec §8): run time, per-platform daily limits, and proxy
// credentials must be editable from the dashboard, no code change -- these
// were previously just displayed as plain text here.
function draftFrom(account) {
  return {
    run_time: (account.run_time || '').slice(0, 5), // HH:MM:SS -> HH:MM for a plain <input type=time>
    ig_daily_limit: account.ig_daily_limit ?? '',
    linkedin_daily_limit: account.linkedin_daily_limit ?? '',
    proxy_host: account.proxy_host || '',
    proxy_port: account.proxy_port || '',
    proxy_username: account.proxy_username || '',
    proxy_password: account.proxy_password || '',
  }
}

export default function AccountHealth() {
  const [accounts, setAccounts] = useState([])
  const [error, setError] = useState(null)
  const [drafts, setDrafts] = useState({})
  const [savedId, setSavedId] = useState(null)

  async function load() {
    const { data, error: err } = await supabase.from('accounts').select('*').order('label')
    if (err) setError(err.message)
    else {
      setAccounts(data)
      // Only seed a draft for accounts that don't have one yet -- reloading
      // (the realtime subscription below) must not stomp an in-progress edit.
      setDrafts((prev) => {
        const next = { ...prev }
        for (const account of data) {
          if (!next[account.id]) next[account.id] = draftFrom(account)
        }
        return next
      })
    }
  }

  useEffect(() => {
    load()
    const channel = supabase
      .channel('account-health')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'accounts' }, load)
      .subscribe()
    return () => supabase.removeChannel(channel)
  }, [])

  function setDraftField(accountId, field, value) {
    setDrafts((d) => ({ ...d, [accountId]: { ...d[accountId], [field]: value } }))
  }

  async function saveDraft(account) {
    const draft = drafts[account.id]
    const { error: err } = await supabase
      .from('accounts')
      .update({
        run_time: draft.run_time ? `${draft.run_time}:00` : account.run_time,
        ig_daily_limit: Number(draft.ig_daily_limit),
        linkedin_daily_limit: Number(draft.linkedin_daily_limit),
        proxy_host: draft.proxy_host || null,
        proxy_port: draft.proxy_port || null,
        proxy_username: draft.proxy_username || null,
        proxy_password: draft.proxy_password || null,
      })
      .eq('id', account.id)
    if (err) setError(err.message)
    else {
      setSavedId(account.id)
      setTimeout(() => setSavedId(null), 1500)
    }
  }

  async function toggleRedistribute(account) {
    await supabase.from('accounts').update({ redistribute_flag: !account.redistribute_flag }).eq('id', account.id)
  }

  async function resumeAccount(account) {
    await supabase
      .from('accounts')
      .update({ status: 'active', warning_type: null, warning_reason: null, redistribute_flag: false })
      .eq('id', account.id)
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-semibold">
          Account <span className="accent-text">Health</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">Nothing redistributes automatically -- that's always your call.</p>
      </motion.header>

      {error && <p className="mt-6 text-sm text-rose-400">{error}</p>}

      <div className="mt-6 space-y-3">
        {accounts.map((account, i) => (
          <motion.div
            key={account.id}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05 }}
            className="glass glass-hover rounded-2xl p-4"
          >
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-semibold text-slate-100">{account.label}</p>
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium capitalize ring-1 ${
                  STATUS_STYLE[account.status] || 'bg-slate-900 text-slate-400 ring-slate-800'
                }`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[account.status] || 'bg-slate-500'} ${account.status === 'active' ? 'animate-pulse' : ''}`} />
                {account.status}
              </span>
            </div>

            <p className="mt-3 text-xs text-slate-500">
              Warm-up cap: {account.warmup_current_limit} (ramps automatically, not editable)
            </p>

            <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
              <label className="block">
                <span className={labelClass}>Run time</span>
                <input
                  type="time"
                  className={inputClass}
                  value={drafts[account.id]?.run_time || ''}
                  onChange={(e) => setDraftField(account.id, 'run_time', e.target.value)}
                />
              </label>
              <label className="block">
                <span className={labelClass}>IG limit/day</span>
                <input
                  type="number"
                  className={inputClass}
                  value={drafts[account.id]?.ig_daily_limit ?? ''}
                  onChange={(e) => setDraftField(account.id, 'ig_daily_limit', e.target.value)}
                />
              </label>
              <label className="block">
                <span className={labelClass}>LinkedIn limit/day</span>
                <input
                  type="number"
                  className={inputClass}
                  value={drafts[account.id]?.linkedin_daily_limit ?? ''}
                  onChange={(e) => setDraftField(account.id, 'linkedin_daily_limit', e.target.value)}
                />
              </label>
              <label className="block">
                <span className={labelClass}>Proxy host</span>
                <input
                  className={inputClass}
                  placeholder="none yet"
                  value={drafts[account.id]?.proxy_host || ''}
                  onChange={(e) => setDraftField(account.id, 'proxy_host', e.target.value)}
                />
              </label>
              <label className="block">
                <span className={labelClass}>Proxy port</span>
                <input
                  className={inputClass}
                  value={drafts[account.id]?.proxy_port || ''}
                  onChange={(e) => setDraftField(account.id, 'proxy_port', e.target.value)}
                />
              </label>
              <label className="block">
                <span className={labelClass}>Proxy username</span>
                <input
                  className={inputClass}
                  value={drafts[account.id]?.proxy_username || ''}
                  onChange={(e) => setDraftField(account.id, 'proxy_username', e.target.value)}
                />
              </label>
              <label className="col-span-2 block sm:col-span-3">
                <span className={labelClass}>Proxy password</span>
                <input
                  type="password"
                  className={inputClass}
                  value={drafts[account.id]?.proxy_password || ''}
                  onChange={(e) => setDraftField(account.id, 'proxy_password', e.target.value)}
                />
              </label>
            </div>

            <div className="mt-3 flex items-center gap-3">
              <motion.button
                whileTap={{ scale: 0.96 }}
                onClick={() => saveDraft(account)}
                className="rounded-lg bg-slate-800/80 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:bg-slate-700"
              >
                Save
              </motion.button>
              {savedId === account.id && <p className="text-xs text-emerald-400">Saved</p>}
            </div>

            {account.warning_type && (
              <div className="mt-3 rounded-xl border border-amber-400/20 bg-amber-500/5 p-3 text-xs text-amber-300">
                <p className="font-semibold">{account.warning_type}</p>
                <p className="mt-1 text-amber-400/70">{account.warning_reason}</p>
              </div>
            )}

            {account.status === 'paused' && (
              <div className="mt-3 flex flex-wrap gap-2">
                <motion.button
                  whileTap={{ scale: 0.96 }}
                  onClick={() => toggleRedistribute(account)}
                  className="rounded-lg bg-slate-800/80 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:bg-slate-700"
                >
                  {account.redistribute_flag ? 'Cancel redistribution' : 'Redistribute its quota'}
                </motion.button>
                <motion.button
                  whileTap={{ scale: 0.96 }}
                  onClick={() => resumeAccount(account)}
                  className="rounded-lg bg-emerald-500/15 px-3 py-1.5 text-xs font-semibold text-emerald-300 ring-1 ring-emerald-400/30 transition hover:bg-emerald-500/25"
                >
                  Resume account
                </motion.button>
              </div>
            )}
          </motion.div>
        ))}
      </div>
    </div>
  )
}
