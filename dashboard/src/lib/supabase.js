import { createClient } from '@supabase/supabase-js'

/**
 * Supabase client for the dashboard.
 *
 * Uses the ANON key, never the service role key. The anon key is safe to ship in
 * frontend code because Row Level Security (set up in Phase 1) is what actually
 * restricts access — only Hussein and Mahmoud can read or write.
 *
 * The agent uses the service role key instead. Never mix the two up.
 */

const url = import.meta.env.VITE_SUPABASE_URL
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

/** True when the dashboard has credentials. Lets the UI show a helpful setup message
 *  instead of crashing on a blank .env, which is the expected state in Phase 0. */
export const isConfigured = Boolean(url && anonKey)

export const supabase = isConfigured
  ? createClient(url, anonKey, {
      // Realtime keeps the live feed, run status, and analytics updating as the
      // agent works, without the dashboard polling.
      realtime: { params: { eventsPerSecond: 5 } },
    })
  : null
