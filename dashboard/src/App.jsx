import { lazy, Suspense, useEffect } from 'react'
import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import { Skeleton } from './components/Skeleton'

// Lazy-loaded so a visit to any one page (e.g. Live Feed, the landing
// route) only downloads that page's own code -- previously every route
// was imported eagerly here, so the app shipped one 1MB+ bundle containing
// every page's code (including Recharts for Analytics and the ~600-line
// SVG pan/zoom engine in Workflow) before the very first page could render.
const LiveFeed = lazy(() => import('./pages/LiveFeed'))
const ApprovalQueue = lazy(() => import('./pages/ApprovalQueue'))
const InstagramManualSend = lazy(() => import('./pages/InstagramManualSend'))
const LinkedIn = lazy(() => import('./pages/LinkedIn'))
const PipelineBoard = lazy(() => import('./pages/PipelineBoard'))
const LeadDetail = lazy(() => import('./pages/LeadDetail'))
const Clients = lazy(() => import('./pages/Clients'))
const ClientHistory = lazy(() => import('./pages/ClientHistory'))
const Analytics = lazy(() => import('./pages/Analytics'))
const AccountHealth = lazy(() => import('./pages/AccountHealth'))
const RunStatus = lazy(() => import('./pages/RunStatus'))
const Errors = lazy(() => import('./pages/Errors'))
const Settings = lazy(() => import('./pages/Settings'))
const Workflow = lazy(() => import('./pages/Workflow'))

// Analytics pulls in Recharts as its own ~420KB chunk -- lazy-loading kept
// it out of the initial bundle, but that means the FIRST click into
// Analytics in any session pays that download cost live, a real,
// measured ~800ms stall (confirmed: 67-230ms for every other section vs
// 861ms for Analytics on first visit). Prefetching it quietly once the
// browser is idle after login means it's already cached by the time
// anyone actually clicks there.
function usePrefetchHeavyRoutes() {
  useEffect(() => {
    const prefetch = () => {
      import('./pages/Analytics')
      import('./pages/Workflow')
    }
    if ('requestIdleCallback' in window) {
      const id = requestIdleCallback(prefetch, { timeout: 4000 })
      return () => cancelIdleCallback(id)
    }
    const id = setTimeout(prefetch, 2000)
    return () => clearTimeout(id)
  }, [])
}

function RouteFallback() {
  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:px-8 sm:py-10">
      <Skeleton className="h-8 w-48" />
      <div className="mt-6 space-y-2">
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
      </div>
    </div>
  )
}

export default function App() {
  usePrefetchHeavyRoutes()

  return (
    <Routes>
      {/* Layout gates everything below it behind a logged-in session (see
          components/Layout.jsx) -- Workflow is a real nav section now, same
          as every other page, not a standalone route anymore. */}
      <Route element={<Layout />}>
        <Route
          path="/"
          element={
            <Suspense fallback={<RouteFallback />}>
              <LiveFeed />
            </Suspense>
          }
        />
        <Route
          path="/approval"
          element={
            <Suspense fallback={<RouteFallback />}>
              <ApprovalQueue />
            </Suspense>
          }
        />
        <Route
          path="/instagram-manual"
          element={
            <Suspense fallback={<RouteFallback />}>
              <InstagramManualSend />
            </Suspense>
          }
        />
        <Route
          path="/linkedin"
          element={
            <Suspense fallback={<RouteFallback />}>
              <LinkedIn />
            </Suspense>
          }
        />
        <Route
          path="/pipeline"
          element={
            <Suspense fallback={<RouteFallback />}>
              <PipelineBoard />
            </Suspense>
          }
        />
        <Route
          path="/leads/:id"
          element={
            <Suspense fallback={<RouteFallback />}>
              <LeadDetail />
            </Suspense>
          }
        />
        <Route
          path="/clients"
          element={
            <Suspense fallback={<RouteFallback />}>
              <Clients />
            </Suspense>
          }
        />
        <Route
          path="/client-history"
          element={
            <Suspense fallback={<RouteFallback />}>
              <ClientHistory />
            </Suspense>
          }
        />
        <Route
          path="/analytics"
          element={
            <Suspense fallback={<RouteFallback />}>
              <Analytics />
            </Suspense>
          }
        />
        <Route
          path="/accounts"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AccountHealth />
            </Suspense>
          }
        />
        <Route
          path="/run-status"
          element={
            <Suspense fallback={<RouteFallback />}>
              <RunStatus />
            </Suspense>
          }
        />
        <Route
          path="/errors"
          element={
            <Suspense fallback={<RouteFallback />}>
              <Errors />
            </Suspense>
          }
        />
        <Route
          path="/workflow"
          element={
            <Suspense fallback={<RouteFallback />}>
              <Workflow />
            </Suspense>
          }
        />
        <Route
          path="/settings"
          element={
            <Suspense fallback={<RouteFallback />}>
              <Settings />
            </Suspense>
          }
        />
      </Route>
    </Routes>
  )
}
