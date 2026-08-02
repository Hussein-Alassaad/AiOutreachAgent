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
export default function AccountHealth() {
  const [accounts, setAccounts] = useState([])
  const [error, setError] = useState(null)

  async function load() {
    const { data, error: err } = await supabase.from('accounts').select('*').order('label')
    if (err) setError(err.message)
    else setAccounts(data)
  }

  useEffect(() => {
    load()
    const channel = supabase
      .channel('account-health')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'accounts' }, load)
      .subscribe()
    return () => supabase.removeChannel(channel)
  }, [])

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

            <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-500">
              <p>Run time: {account.run_time}</p>
              <p>Warm-up cap: {account.warmup_current_limit}</p>
              <p>IG limit: {account.ig_daily_limit}</p>
              <p>LinkedIn limit: {account.linkedin_daily_limit}</p>
              <p>Proxy: {account.proxy_host ? 'configured' : 'none yet'}</p>
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
