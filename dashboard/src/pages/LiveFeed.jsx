import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { supabase } from '../lib/supabase'
import LeadCard from '../components/LeadCard'
import { SkeletonCard } from '../components/Skeleton'
import EmptyState from '../components/EmptyState'

/**
 * Today's leads (spec §7.1) -- everything discovered/analyzed since local
 * midnight, hottest first. Realtime-subscribed so new leads appear as the
 * agent works, without a manual refresh.
 */
export default function LiveFeed() {
  const [leads, setLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    const todayStart = new Date()
    todayStart.setHours(0, 0, 0, 0)

    async function load() {
      setLoading(true)
      const { data, error: err } = await supabase
        .from('leads')
        .select('*')
        .gte('created_at', todayStart.toISOString())
        .order('score', { ascending: false, nullsFirst: false })
        .order('created_at', { ascending: false })

      if (err) setError(err.message)
      else setLeads(data)
      setLoading(false)
    }

    load()

    const channel = supabase
      .channel('live-feed')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'leads' }, load)
      .subscribe()

    return () => supabase.removeChannel(channel)
  }, [])

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:px-8 sm:py-10">
      <motion.header initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-semibold">
          Live <span className="accent-text">Feed</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">Today's leads, hottest first.</p>
      </motion.header>

      {error && <p className="mt-6 text-sm text-rose-400">{error}</p>}

      {!error && loading && (
        <div className="mt-6 space-y-3">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      )}

      {!error && !loading && leads.length === 0 && (
        <EmptyState title="No leads yet today" subtitle="They'll appear here the moment the agent finds one." />
      )}

      <div className="mt-6 space-y-3">
        <AnimatePresence mode="popLayout">
          {leads.map((lead) => (
            <LeadCard key={lead.id} lead={lead} />
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
