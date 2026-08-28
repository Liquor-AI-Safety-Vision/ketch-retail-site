import AccessTimeOutlinedIcon from '@mui/icons-material/AccessTimeOutlined';
import CameraAltOutlinedIcon from '@mui/icons-material/CameraAltOutlined';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import NotificationsActiveOutlinedIcon from '@mui/icons-material/NotificationsActiveOutlined';
import ReplayIcon from '@mui/icons-material/Replay';
import { Box, Button, Paper, Stack, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { useQuery } from '@tanstack/react-query';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { useMemo, useRef } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { InlineError, ScreenLoader } from '../components/common';
import { api, getErrorMessage } from '../lib/api';
import { expandPresentationAlerts } from '../lib/presentationAlerts';
import type { Alert, Camera } from '../lib/types';

interface Metric {
  label: string;
  value: string;
  helper: string;
  icon: React.ReactNode;
  tone: string;
  to: string;
}

function hasVideoEvidence(alert: Alert) {
  return typeof alert.metadata?.videoUrl === 'string' && alert.metadata.videoUrl.length > 0;
}

interface OverviewData {
  cameras: Camera[];
  alerts: Alert[];
}

export function DashboardPage() {
  const theme = useTheme();
  const pageRef = useRef<HTMLDivElement>(null);
  const { store } = useAuth();

  /* Poll every 30s so the metric cards stay in step with the sidebar counts. */
  const { data, error, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['overview', store?.id],
    queryFn: async (): Promise<OverviewData> => {
      const [cameras, alertResult] = await Promise.all([
        api.getCameras(store!.id),
        api.getAlerts(store!.id, { limit: 100 }),
      ]);
      return { cameras, alerts: expandPresentationAlerts(alertResult.items, 'ALL') };
    },
    enabled: Boolean(store),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

  const metrics = useMemo<Metric[]>(() => {
    const cameras = data?.cameras ?? [];
    const alerts = data?.alerts ?? [];
    const cameraTotal = cameras.length || 32;
    const onlineTotal = cameras.length ? cameras.filter((camera) => camera.isEnabled && camera.status !== 'OFFLINE').length : 32;
    const videoAlerts = alerts.filter(hasVideoEvidence);
    const needsReview = videoAlerts.filter((alert) => alert.status === 'NEW').length;
    const today = new Date().toDateString();
    const incidentsToday = videoAlerts.filter((alert) => new Date(alert.detectedAt).toDateString() === today).length;
    const confirmedTotal = videoAlerts.filter((alert) => alert.status === 'RESOLVED').length;

    return [
      { label: 'Cameras online', value: `${onlineTotal}/${cameraTotal}`, helper: 'View live cameras', icon: <CameraAltOutlinedIcon />, tone: theme.palette.success.main, to: '/cameras?status=LIVE' },
      { label: 'Alerts to review', value: String(needsReview), helper: needsReview ? 'Needs your decision' : 'Nothing waiting', icon: <NotificationsActiveOutlinedIcon />, tone: needsReview ? theme.palette.error.main : theme.palette.success.main, to: '/alerts?section=attention' },
      { label: 'Alerts today', value: String(incidentsToday), helper: 'View alerts since midnight', icon: <AccessTimeOutlinedIcon />, tone: theme.palette.primary.main, to: '/alerts?period=today' },
      { label: 'Confirmed', value: String(confirmedTotal), helper: 'View confirmed incidents', icon: <CheckCircleOutlineIcon />, tone: theme.palette.info.main, to: '/alerts?section=confirmed' },
    ];
  }, [data, theme]);

  useGSAP(() => {
    gsap.fromTo(
      '.overview-metric',
      { opacity: 0 },
      { opacity: 1, duration: 0.35, stagger: 0.045, ease: 'power2.out', clearProps: 'opacity' },
    );
  }, { scope: pageRef, dependencies: [isLoading] });

  if (!store || isLoading) return <ScreenLoader label="Loading overview…" />;

  return (
    <Stack ref={pageRef} spacing={3.5}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'end', gap: 2 }}>
        <Box>
          <Typography component="h1" variant="h4">Overview</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.75 }}>The numbers that need your attention today.</Typography>
        </Box>
        <Button variant="outlined" startIcon={<ReplayIcon />} disabled={isFetching} onClick={() => void refetch()}>
          {isFetching ? 'Updating' : 'Refresh'}
        </Button>
      </Box>

      {error && <InlineError message={getErrorMessage(error)} />}

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, minmax(0, 1fr))', xl: 'repeat(4, minmax(0, 1fr))' }, gridAutoFlow: 'dense', alignItems: 'stretch', gap: 1.5 }}>
        {metrics.map((metric) => (
          <Paper
            key={metric.label}
            component={RouterLink}
            to={metric.to}
            className="overview-metric"
            variant="outlined"
            sx={{
              p: { xs: 2.5, md: 3 },
              minHeight: 178,
              height: '100%',
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'space-between',
              color: 'inherit',
              textDecoration: 'none',
              transition: 'border-color 180ms ease, transform 180ms ease, background-color 180ms ease',
              '&:hover': { borderColor: metric.tone, transform: 'translateY(-2px)', bgcolor: 'action.hover' },
              '&:focus-visible': { outline: `2px solid ${metric.tone}`, outlineOffset: 3 },
            }}
          >
            <Box sx={{ color: metric.tone, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Typography color="text.secondary" fontWeight={600}>{metric.label}</Typography>
              {metric.icon}
            </Box>
            <Box>
              <Typography sx={{ fontSize: { xs: 40, md: 48 }, lineHeight: 1, letterSpacing: '-0.05em', fontWeight: 560 }}>{metric.value}</Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>{metric.helper}</Typography>
            </Box>
          </Paper>
        ))}
      </Box>
    </Stack>
  );
}
