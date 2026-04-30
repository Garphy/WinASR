import { BrowserRouter, Routes, Route } from 'react-router-dom'
import TaskListPage from './components/TaskListPage'
import UploadPage from './components/UploadPage'
import WorkspacePage from './components/WorkspacePage'

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <header className="app-header">
          <h1 className="app-title">WinASR</h1>
          <span className="app-subtitle">Audio Transcription &amp; Editor</span>
        </header>
        <main className="app-main">
          <Routes>
            <Route path="/" element={<TaskListPage />} />
            <Route path="/upload" element={<UploadPage />} />
            <Route path="/task/:taskId" element={<WorkspacePage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
