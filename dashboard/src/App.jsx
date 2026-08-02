import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import LiveFeed from './pages/LiveFeed'
import ApprovalQueue from './pages/ApprovalQueue'
import InstagramManualSend from './pages/InstagramManualSend'
import PipelineBoard from './pages/PipelineBoard'
import LeadDetail from './pages/LeadDetail'
import ClientHistory from './pages/ClientHistory'
import Analytics from './pages/Analytics'
import AccountHealth from './pages/AccountHealth'
import RunStatus from './pages/RunStatus'
import Settings from './pages/Settings'
import Workflow from './pages/Workflow'

export default function App() {
  return (
    <Routes>
      {/* Layout gates everything below it behind a logged-in session (see
          components/Layout.jsx) -- Workflow is a real nav section now, same
          as every other page, not a standalone route anymore. */}
      <Route element={<Layout />}>
        <Route path="/" element={<LiveFeed />} />
        <Route path="/approval" element={<ApprovalQueue />} />
        <Route path="/instagram-manual" element={<InstagramManualSend />} />
        <Route path="/pipeline" element={<PipelineBoard />} />
        <Route path="/leads/:id" element={<LeadDetail />} />
        <Route path="/client-history" element={<ClientHistory />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/accounts" element={<AccountHealth />} />
        <Route path="/run-status" element={<RunStatus />} />
        <Route path="/workflow" element={<Workflow />} />
        <Route path="/settings" element={<Settings />} />
      </Route>
    </Routes>
  )
}
