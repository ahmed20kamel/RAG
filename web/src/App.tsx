import { Component, Suspense, lazy, useEffect, useState, type ErrorInfo, type ReactNode } from 'react'
import { Navigate, Route, BrowserRouter as Router, Routes, useNavigate } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { AppShell } from '@/components/layout/AppShell'
import { RequireAuth } from '@/components/layout/RequireAuth'
import { ToastViewport } from '@/components/ui/ToastViewport'
import { Button, ErrorState } from '@/components/ui/primitives'
import { useTranslation } from '@/hooks/useTranslation'
import { applyPreferences, usePreferences } from '@/state/preferences'
import { createQueryClient } from '@/services/query-client'

// Every route is split out, so opening the chat does not download the document viewer.
const ChatPage = lazy(() => import('@/pages/ChatPage').then((m) => ({ default: m.ChatPage })))
const LibraryPage = lazy(() => import('@/pages/LibraryPage').then((m) => ({ default: m.LibraryPage })))
const KnowledgePage = lazy(() => import('@/pages/KnowledgePage').then((m) => ({ default: m.KnowledgePage })))
const UploadPage = lazy(() => import('@/pages/UploadPage').then((m) => ({ default: m.UploadPage })))
const DocumentPage = lazy(() => import('@/pages/DocumentPage').then((m) => ({ default: m.DocumentPage })))
const QualityPage = lazy(() => import('@/pages/QualityPage').then((m) => ({ default: m.QualityPage })))
const SettingsPage = lazy(() => import('@/pages/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const LoginPage = lazy(() => import('@/pages/LoginPage').then((m) => ({ default: m.LoginPage })))
const UsersPage = lazy(() => import('@/pages/UsersPage').then((m) => ({ default: m.UsersPage })))
const MonitoringPage = lazy(() =>
  import('@/pages/MonitoringPage').then((m) => ({ default: m.MonitoringPage })),
)
const CandidatesPage = lazy(() =>
  import('@/pages/CandidatesPage').then((m) => ({ default: m.CandidatesPage })),
)

class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Interface error', error, info.componentStack)
  }

  render() {
    if (this.state.failed) return <BoundaryFallback />
    return this.props.children
  }
}

function BoundaryFallback() {
  const { t } = useTranslation()
  return (
    <div style={{ display: 'grid', placeItems: 'center', minHeight: '100dvh' }}>
      <ErrorState
        title={t('errors.boundary')}
        body={t('errors.generic')}
        action={
          <Button variant="primary" icon="refresh" onClick={() => window.location.reload()}>
            {t('errors.reload')}
          </Button>
        }
      />
    </div>
  )
}

function NotFound() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  return (
    <div className="page">
      <ErrorState
        title={t('errors.notFound')}
        body={t('errors.notFoundBody')}
        action={
          <Button variant="primary" onClick={() => navigate('/chat')}>
            {t('errors.goHome')}
          </Button>
        }
      />
    </div>
  )
}

/** Keeps `<html>` in step with the stored theme, language and direction. */
function PreferencesEffect() {
  const theme = usePreferences((state) => state.theme)
  const language = usePreferences((state) => state.language)
  useEffect(() => applyPreferences(theme, language), [theme, language])
  return null
}

export function App() {
  const [queryClient] = useState(createQueryClient)
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <PreferencesEffect />
        <Router>
          <Routes>
            <Route
              path="login"
              element={
                <Suspense fallback={null}>
                  <LoginPage />
                </Suspense>
              }
            />
            <Route
              element={
                <RequireAuth>
                  <AppShell />
                </RequireAuth>
              }
            >
              <Route index element={<Navigate to="/chat" replace />} />
              <Route path="chat" element={<ChatPage />} />
              <Route path="library" element={<LibraryPage />} />
              <Route path="knowledge" element={<KnowledgePage />} />
              <Route path="candidates" element={<CandidatesPage />} />
              <Route path="upload" element={<UploadPage />} />
              <Route path="documents/:documentId" element={<DocumentPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="admin/users" element={<UsersPage />} />
              <Route path="admin/monitoring" element={<MonitoringPage />} />
              <Route path="admin/quality" element={<QualityPage />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </Router>
        <ToastViewport />
      </QueryClientProvider>
    </ErrorBoundary>
  )
}
