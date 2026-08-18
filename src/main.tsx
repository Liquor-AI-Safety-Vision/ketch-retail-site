import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createHashRouter, RouterProvider } from 'react-router-dom'
import './index.css'
import LandingPage from './pages/landing/LandingPage'
import DashboardPage from './pages/dashboard/DashboardPage'

/* Two-page IA: clean landing → button → console.
   HashRouter so /dashboard survives static hosting + refresh with no rewrites. */
const router = createHashRouter([
  { path: '/', element: <LandingPage /> },
  { path: '/dashboard', element: <DashboardPage /> },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
