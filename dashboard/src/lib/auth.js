import { useEffect, useState } from 'react'
import { supabase, isConfigured } from './supabase'

/**
 * Auth state hook -- the dashboard's RLS policies (database/policies.sql)
 * grant the `authenticated` role full access and everyone else nothing, so
 * every page needs a real logged-in session before any query returns data.
 *
 * NOTE: Supabase Auth accounts for Hussein and Mohamad don't exist yet --
 * login is postponed (see project todo list). Every page still renders
 * correctly against an empty/unauthenticated session; it just won't show
 * real rows until accounts are created and someone signs in.
 */
export function useSession() {
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!isConfigured) {
      setLoading(false)
      return
    }

    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session)
      setLoading(false)
    })

    const { data: listener } = supabase.auth.onAuthStateChange((_event, newSession) => {
      setSession(newSession)
    })

    return () => listener.subscription.unsubscribe()
  }, [])

  return { session, loading }
}

export async function signIn(email, password) {
  if (!isConfigured) throw new Error('Supabase is not configured (dashboard/.env)')
  const { error } = await supabase.auth.signInWithPassword({ email, password })
  if (error) throw error
}

export async function signOut() {
  if (!isConfigured) return
  await supabase.auth.signOut()
}
