import { Route, Routes } from 'react-router-dom'
import Status from './pages/Status'
import Workflow from './pages/Workflow'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Status />} />
      <Route path="/workflow" element={<Workflow />} />
    </Routes>
  )
}
