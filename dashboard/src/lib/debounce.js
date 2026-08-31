/**
 * Collapses a burst of calls into one trailing call. Every page that
 * subscribes to a Supabase realtime channel and reloads its whole list on
 * every change event needs this -- a bulk insert/update (a full pipeline
 * cycle processing many leads at once, or a batch of new discoveries)
 * fires one event per row, and without debouncing that means one full
 * table refetch per row instead of one refetch for the whole burst.
 */
export function debounce(fn, waitMs = 400) {
  let timeout
  return (...args) => {
    clearTimeout(timeout)
    timeout = setTimeout(() => fn(...args), waitMs)
  }
}
