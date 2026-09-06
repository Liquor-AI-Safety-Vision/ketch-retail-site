import { CssBaseline, ThemeProvider } from '@mui/material';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useMemo } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth } from './auth/AuthProvider';
import { AppShell } from './components/AppShell';
import { ScreenLoader } from './components/common';
import { AlertsPage } from './pages/AlertsPage';
import { CamerasPage } from './pages/CamerasPage';
import { DashboardPage } from './pages/DashboardPage';
import { LoginPage } from './pages/LoginPage';
import { ColorModeProvider, useColorMode } from './theme/ColorModeProvider';
import { createDashboardTheme } from './theme/dashboardTheme';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: true,
    },
  },
});

/**
 * Visitors land on the dashboard without signing in, exactly as the Ketch
 * deployment does: the read-only demo data is served client-side. Only a deep
 * link to a real alert requires an owner session, because that reads the API.
 */
function ProtectedLayout() {
  const { user, isLoading } = useAuth();
  const location = useLocation();
  const alertId = new URLSearchParams(location.search).get('alertId') ?? '';
  const privateAlertLink = location.pathname === '/alerts'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(alertId);

  if (isLoading) return <ScreenLoader label="Restoring your secure session…" />;
  if (!user && privateAlertLink) return <Navigate to="/login" replace state={{ from: location }} />;
  return <AppShell key={user?.id ?? 'public-demo'} />;
}

function DashboardRedirect() {
  const { isLoading } = useAuth();

  if (isLoading) return <ScreenLoader />;
  return <Navigate to="/dashboard" replace />;
}

function ThemedApp() {
  const { mode } = useColorMode();
  const theme = useMemo(() => createDashboardTheme(mode), [mode]);

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<ProtectedLayout />}>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/alerts" element={<AlertsPage />} />
            <Route path="/cameras" element={<CamerasPage />} />
          </Route>
          <Route path="*" element={<DashboardRedirect />} />
        </Routes>
      </AuthProvider>
    </ThemeProvider>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ColorModeProvider>
          <ThemedApp />
        </ColorModeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
