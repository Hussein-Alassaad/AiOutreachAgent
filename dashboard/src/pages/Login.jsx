import { useState } from 'react'
import { motion } from 'framer-motion'
import { signIn } from '../lib/auth'
import { isConfigured } from '../lib/supabase'
import ThemeToggle from '../components/ThemeToggle'

export default function Login() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await signIn(email, password)
      // onAuthStateChange (see lib/auth.js) picks up the new session and
      // Layout re-renders the real app -- no manual redirect needed here.
    } catch (err) {
      setError(err.message || 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center px-4 py-16 text-slate-100">
      <motion.div
        initial={{ opacity: 0, y: 16, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.4, ease: 'easeOut' }}
        className="glass w-full max-w-sm rounded-2xl p-8 shadow-2xl"
      >
        <div className="flex items-center justify-between">
          <p className="accent-text text-xs font-bold uppercase tracking-widest">Nexaris</p>
          <ThemeToggle />
        </div>
        <h1 className="mt-2 text-2xl font-semibold">Sign in</h1>
        <p className="mt-1 text-sm text-slate-500">AI outreach agent dashboard</p>

        {!isConfigured && (
          <p className="mt-4 rounded-xl border border-amber-400/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
            Supabase isn't configured (dashboard/.env is missing VITE_SUPABASE_URL /
            VITE_SUPABASE_ANON_KEY).
          </p>
        )}

        <form onSubmit={handleSubmit} className="mt-6 space-y-4">
          <div>
            <label className="block text-sm font-medium text-slate-300" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="accent-ring mt-1 w-full rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-2.5 text-sm text-slate-100 outline-none transition focus:border-[var(--color-accent-from)]/60"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-300" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="accent-ring mt-1 w-full rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-2.5 text-sm text-slate-100 outline-none transition focus:border-[var(--color-accent-from)]/60"
            />
          </div>

          {error && (
            <motion.p
              initial={{ opacity: 0, x: -4 }}
              animate={{ opacity: 1, x: 0 }}
              className="text-sm text-rose-400"
            >
              {error}
            </motion.p>
          )}

          <motion.button
            whileHover={{ scale: 1.01 }}
            whileTap={{ scale: 0.98 }}
            type="submit"
            disabled={busy || !isConfigured}
            className="w-full rounded-xl bg-gradient-to-r from-[var(--color-accent-from)] to-[var(--color-accent-to)] px-3 py-2.5 text-sm font-semibold text-slate-950 shadow-lg shadow-[var(--color-accent-from)]/20 transition disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? 'Signing in…' : 'Sign in'}
          </motion.button>
        </form>
      </motion.div>
    </div>
  )
}
