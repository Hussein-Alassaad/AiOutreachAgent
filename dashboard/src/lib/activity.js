import { useCallback, useEffect, useRef, useState } from 'react'
import { supabase } from './supabase'

// One entry per event type this feed understands, sourced entirely from
// real timestamped columns that already exist (leads.created_at,
// pipeline_history.changed_at, messages.approved_at/sent_at,
// notifications_log.sent_at, runs.started_at/finished_at) -- no new table,
// no synthetic data.
export const TYPE_META = {
  lead_discovered: { label: 'New lead', color: 'sky' },
  lead_hot: { label: 'Hot lead', color: 'rose' },
  founder_found: { label: 'Founder found', color: 'violet' },
  stage_changed: { label: 'Pipeline move', color: 'amber' },
  message_approved: { label: 'Message approved', color: 'emerald' },
  message_sent: { label: 'Message sent', color: 'sky' },
  run_started: { label: 'Run started', color: 'slate' },
  run_finished: { label: 'Run finished', color: 'emerald' },
  run_error: { label: 'Run error', color: 'rose' },
}

const STAGE_LABEL = {
  contacted: 'Contacted',
  replied: 'Replied',
  interested: 'Interested',
  meeting_booked: 'Meeting Booked',
  deal_closed: 'Deal Closed',
  lost: 'Lost',
  approved: 'Approved',
}

export function timeAgo(iso) {
  if (!iso) return ''
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}

// A small in-memory cache so realtime events that don't carry a joined
// business_name (e.g. a bare pipeline_history INSERT payload) can still
// show one, as long as we've seen that lead somewhere else this session.
const leadNameCache = new Map()
function rememberLead(id, name) {
  if (id && name) leadNameCache.set(id, name)
}
function leadName(id, fallbackName) {
  return fallbackName || leadNameCache.get(id) || 'A lead'
}

async function fetchLeadDiscovered(limit) {
  const { data } = await supabase
    .from('leads')
    .select('id, business_name, platform, created_at')
    .order('created_at', { ascending: false })
    .limit(limit)
  return (data || []).map((l) => {
    rememberLead(l.id, l.business_name)
    return {
      id: `lead-${l.id}`,
      type: 'lead_discovered',
      ts: l.created_at,
      leadId: l.id,
      title: l.business_name || 'Unnamed business',
      subtitle: `Discovered on ${l.platform}`,
    }
  })
}

async function fetchStageChanges(limit) {
  const { data } = await supabase
    .from('pipeline_history')
    .select('id, lead_id, from_stage, to_stage, changed_by, changed_at, leads(business_name)')
    .order('changed_at', { ascending: false })
    .limit(limit)
  return (data || []).map((h) => {
    rememberLead(h.lead_id, h.leads?.business_name)
    return {
      id: `stage-${h.id}`,
      type: 'stage_changed',
      ts: h.changed_at,
      leadId: h.lead_id,
      title: leadName(h.lead_id, h.leads?.business_name),
      subtitle: `Moved to ${STAGE_LABEL[h.to_stage] || h.to_stage} by ${h.changed_by}`,
    }
  })
}

async function fetchApprovedMessages(limit) {
  const { data } = await supabase
    .from('messages')
    .select('id, lead_id, channel, approved_by, approved_at, leads(business_name)')
    .eq('approval_status', 'approved')
    .not('approved_at', 'is', null)
    .order('approved_at', { ascending: false })
    .limit(limit)
  return (data || []).map((m) => {
    rememberLead(m.lead_id, m.leads?.business_name)
    return {
      id: `approved-${m.id}`,
      type: 'message_approved',
      ts: m.approved_at,
      leadId: m.lead_id,
      title: leadName(m.lead_id, m.leads?.business_name),
      subtitle: `${m.channel} message approved by ${m.approved_by || 'Mohamad'}`,
    }
  })
}

async function fetchSentMessages(limit) {
  const { data } = await supabase
    .from('messages')
    .select('id, lead_id, channel, sent_at, leads(business_name)')
    .eq('send_status', 'sent')
    .not('sent_at', 'is', null)
    .order('sent_at', { ascending: false })
    .limit(limit)
  return (data || []).map((m) => {
    rememberLead(m.lead_id, m.leads?.business_name)
    return {
      id: `sent-${m.id}`,
      type: 'message_sent',
      ts: m.sent_at,
      leadId: m.lead_id,
      title: leadName(m.lead_id, m.leads?.business_name),
      subtitle: `Message sent via ${m.channel}`,
    }
  })
}

async function fetchNotifications(limit) {
  const { data } = await supabase
    .from('notifications_log')
    .select('id, type, related_lead_id, sent_at, leads(business_name)')
    .order('sent_at', { ascending: false })
    .limit(limit)
  return (data || [])
    .filter((n) => n.type === 'hot_lead' || n.type === 'founder')
    .map((n) => {
      rememberLead(n.related_lead_id, n.leads?.business_name)
      return {
        id: `notif-${n.id}`,
        type: n.type === 'hot_lead' ? 'lead_hot' : 'founder_found',
        ts: n.sent_at,
        leadId: n.related_lead_id,
        title: leadName(n.related_lead_id, n.leads?.business_name),
        subtitle: n.type === 'hot_lead' ? 'Flagged as a hot lead' : 'Founder contact found',
      }
    })
}

async function fetchRuns(limit) {
  const { data } = await supabase
    .from('runs')
    .select('id, started_at, finished_at, status, accounts(label)')
    .order('started_at', { ascending: false })
    .limit(limit)
  const events = []
  for (const r of data || []) {
    const account = r.accounts?.label || 'An account'
    events.push({ id: `run-start-${r.id}`, type: 'run_started', ts: r.started_at, leadId: null, title: account, subtitle: 'Run started' })
    if (r.finished_at) {
      events.push({
        id: `run-end-${r.id}`,
        type: r.status === 'error' ? 'run_error' : 'run_finished',
        ts: r.finished_at,
        leadId: null,
        title: account,
        subtitle: r.status === 'error' ? 'Run failed' : 'Run finished',
      })
    }
  }
  return events
}

export async function fetchActivity(limit = 15) {
  const results = await Promise.all([
    fetchLeadDiscovered(limit),
    fetchStageChanges(limit),
    fetchApprovedMessages(limit),
    fetchSentMessages(limit),
    fetchNotifications(limit),
    fetchRuns(limit),
  ])
  return results
    .flat()
    .filter((e) => e.ts)
    .sort((a, b) => new Date(b.ts) - new Date(a.ts))
    .slice(0, limit * 2)
}

/**
 * Merges the historical fetch with live realtime inserts/updates across
 * every source table, and tracks an "unseen" count for the bell badge --
 * mounted once in Layout so the count keeps accumulating even while the
 * drawer is closed.
 */
export function useActivityFeed(limit = 40, enabled = true) {
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [unseenCount, setUnseenCount] = useState(0)
  // false while the drawer is closed (new events count toward the badge),
  // true while it's open (events arrive already "seen", no badge bump).
  const drawerOpenRef = useRef(false)

  const addEvent = useCallback((event) => {
    if (!event?.ts) return
    setEvents((prev) => {
      if (prev.some((e) => e.id === event.id)) return prev
      return [event, ...prev].slice(0, limit)
    })
    if (!drawerOpenRef.current) setUnseenCount((c) => c + 1)
  }, [limit])

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    async function load() {
      setLoading(true)
      const data = await fetchActivity(Math.ceil(limit / 2))
      if (!cancelled) setEvents(data.slice(0, limit))
      setLoading(false)
    }
    load()
    return () => {
      cancelled = true
    }
  }, [limit, enabled])

  useEffect(() => {
    if (!enabled) return
    const channel = supabase
      .channel('activity-feed')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'leads' }, ({ new: l }) => {
        rememberLead(l.id, l.business_name)
        addEvent({ id: `lead-${l.id}`, type: 'lead_discovered', ts: l.created_at, leadId: l.id, title: l.business_name || 'Unnamed business', subtitle: `Discovered on ${l.platform}` })
      })
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'pipeline_history' }, ({ new: h }) => {
        addEvent({ id: `stage-${h.id}`, type: 'stage_changed', ts: h.changed_at, leadId: h.lead_id, title: leadName(h.lead_id), subtitle: `Moved to ${STAGE_LABEL[h.to_stage] || h.to_stage} by ${h.changed_by}` })
      })
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'notifications_log' }, ({ new: n }) => {
        if (n.type !== 'hot_lead' && n.type !== 'founder') return
        addEvent({
          id: `notif-${n.id}`,
          type: n.type === 'hot_lead' ? 'lead_hot' : 'founder_found',
          ts: n.sent_at,
          leadId: n.related_lead_id,
          title: leadName(n.related_lead_id),
          subtitle: n.type === 'hot_lead' ? 'Flagged as a hot lead' : 'Founder contact found',
        })
      })
      .on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'messages' }, ({ new: m, old: prev }) => {
        if (m.approval_status === 'approved' && prev.approval_status !== 'approved' && m.approved_at) {
          addEvent({ id: `approved-${m.id}`, type: 'message_approved', ts: m.approved_at, leadId: m.lead_id, title: leadName(m.lead_id), subtitle: `${m.channel} message approved by ${m.approved_by || 'Mohamad'}` })
        }
        if (m.send_status === 'sent' && prev.send_status !== 'sent' && m.sent_at) {
          addEvent({ id: `sent-${m.id}`, type: 'message_sent', ts: m.sent_at, leadId: m.lead_id, title: leadName(m.lead_id), subtitle: `Message sent via ${m.channel}` })
        }
      })
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'runs' }, ({ new: r }) => {
        addEvent({ id: `run-start-${r.id}`, type: 'run_started', ts: r.started_at, leadId: null, title: 'An account', subtitle: 'Run started' })
      })
      .on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'runs' }, ({ new: r, old: prev }) => {
        if (r.finished_at && !prev.finished_at) {
          addEvent({ id: `run-end-${r.id}`, type: r.status === 'error' ? 'run_error' : 'run_finished', ts: r.finished_at, leadId: null, title: 'An account', subtitle: r.status === 'error' ? 'Run failed' : 'Run finished' })
        }
      })
      .subscribe()
    return () => supabase.removeChannel(channel)
  }, [addEvent])

  // Called whenever the drawer's open state changes -- opening clears the
  // badge and pauses counting; closing resumes counting from zero.
  const setDrawerOpen = useCallback((open) => {
    drawerOpenRef.current = open
    if (open) setUnseenCount(0)
  }, [])

  return { events, loading, unseenCount, setDrawerOpen }
}
