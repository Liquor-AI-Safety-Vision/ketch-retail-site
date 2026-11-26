import '@fontsource-variable/outfit';
import { CssBaseline, ThemeProvider } from '@mui/material';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useMemo } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { useColorMode } from '../theme/ColorModeProvider';
import { AuthProvider, useAuth } from './auth/AuthProvider';
import { AppShell } from './components/AppShell';
import { ScreenLoader } from './components/common';
import { AlertsPage } from './pages/AlertsPage';
import { CamerasPage } from './pages/CamerasPage';
import { DashboardPage } from './pages/DashboardPage';
import { LoginPage } from './pages/LoginPage';
import { createDashboardTheme } from './theme';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: true,
    },
  },
});

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

export default function SecurityDashboardApp() {
  const { mode } = useColorMode();
  const theme = useMemo(() => createDashboardTheme(mode), [mode]);

  return (
    <QueryClientProvider client={queryClient}>
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
    </QueryClientProvider>
  );
}
