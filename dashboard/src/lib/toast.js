import { useEffect, useState } from 'react'

/**
 * Minimal module-level pub-sub toast store -- no Context provider needed,
 * any component (including a realtime subscription deep in a page) can
 * call pushToast() directly, and ToastContainer (mounted once in Layout)
 * renders whatever's active.
 */
let toasts = []
let listeners = []

function notify() {
  listeners.forEach((l) => l([...toasts]))
}

export function pushToast({ title, body }) {
  const id = crypto.randomUUID()
  toasts = [...toasts, { id, title, body }]
  notify()
  setTimeout(() => dismissToast(id), 5000)
  return id
}

export function dismissToast(id) {
  toasts = toasts.filter((t) => t.id !== id)
  notify()
}

export function useToasts() {
  const [state, setState] = useState(toasts)
  useEffect(() => {
    listeners.push(setState)
    return () => {
      listeners = listeners.filter((l) => l !== setState)
    }
  }, [])
  return state
}
