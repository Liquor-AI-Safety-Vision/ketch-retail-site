import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { createHashRouter, RouterProvider } from 'react-router-dom'
import './index.css'
import LandingPage from './pages/landing/LandingPage'
import { ColorModeProvider } from './theme/ColorModeProvider'

const SecurityDashboardApp = lazy(() => import('./security-dashboard/SecurityDashboardApp'))

function DashboardRoute() {
  return (
    <Suspense
      fallback={(
        <div className="grid min-h-[100dvh] place-items-center bg-cream text-sm text-ink-600">
          Loading security dashboard…
        </div>
      )}
    >
      <SecurityDashboardApp />
    </Suspense>
  )
}

/* Two-page IA: clean landing → button → console.
   HashRouter so /dashboard survives static hosting + refresh with no rewrites. */
const router = createHashRouter([
  { path: '/', element: <LandingPage /> },
  { path: '/*', element: <DashboardRoute /> },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ColorModeProvider>
      <RouterProvider router={router} />
    </ColorModeProvider>
  </StrictMode>,
)
