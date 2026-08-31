import { supabase } from './supabase'

/**
 * Subscribes to a Supabase realtime channel, deferred by one animation
 * frame. Every page on this dashboard opens its own channel on mount and
 * tears it down on unmount -- with instant page-to-page navigation (see
 * Layout.jsx, no cross-fade), the outgoing page's `removeChannel()` and
 * the incoming page's `.subscribe()` can land in the same tick. Supabase's
 * realtime client shares one underlying WebSocket across every channel and
 * only tears it down once the last channel unsubscribes -- when a new
 * subscribe races that teardown, the browser logs "WebSocket is closed
 * before the connection is established" and the connection has to be
 * retried, which is what showed up as stutter when clicking through
 * sections quickly (confirmed live: reproduced on 5 of 7 rapid navigations
 * in a scripted test).
 *
 * requestAnimationFrame pushes the subscribe call to the next paint, after
 * the previous page's cleanup has actually run -- cheap, and enough to
 * stop the two from landing in the same synchronous batch.
 *
 * Returns an unsubscribe function with the same shape a bare `useEffect`
 * cleanup expects, so call sites change minimally:
 *
 *   useEffect(() => {
 *     const unsubscribe = subscribeChannel('clients-page-leads', (ch) =>
 *       ch.on('postgres_changes', { event: '*', schema: 'public', table: 'leads' }, reload)
 *     )
 *     return unsubscribe
 *   }, [...])
 */
export function subscribeChannel(name, configure) {
  let channel = null
  let cancelled = false

  const frame = requestAnimationFrame(() => {
    if (cancelled) return
    channel = configure(supabase.channel(name))
    channel.subscribe()
  })

  return () => {
    cancelled = true
    cancelAnimationFrame(frame)
    if (channel) supabase.removeChannel(channel)
  }
}
