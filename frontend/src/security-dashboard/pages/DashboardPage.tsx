import ArrowForwardRoundedIcon from '@mui/icons-material/ArrowForwardRounded';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import ReplayIcon from '@mui/icons-material/Replay';
import { Box, Button, Paper, Stack, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { Link as RouterLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { AlertRow } from '../components/AlertRow';
import { InlineError, ScreenLoader, cameraAreaLabel, formatAgo } from '../components/common';
import { getErrorMessage } from '../lib/api';
import { queryKeys, startOfToday, useAlertSummary, useCameras } from '../lib/queries';
import type { AlertSummaryCount, Camera } from '../lib/types';

// Overview answers three questions without a click: is anything waiting for
// me, which alerts are they, and is every camera being watched.

const latestCount = 5;

function countOf(counts: AlertSummaryCount[] | undefined, statuses: string[]) {
  return (counts ?? []).filter((item) => statuses.includes(item.status)).reduce((sum, item) => sum + item.count, 0);
}

const statusOrder: Record<string, number> = { offline: 0, unknown: 1, inactive: 2, online: 3 };

export function DashboardPage() {
  const theme = useTheme();
  const navigate = useNavigate();
  const { store, api } = useAuth();
  // Same cache entries as the Cameras and Alerts pages: no duplicate requests.
  const cameras = useCameras({ refetchInterval: 30_000 });
  const all = useAlertSummary();
  const today = useAlertSummary(startOfToday());
  const latest = useQuery({
    queryKey: [...queryKeys.alertPages(store?.id), 'latest'],
    queryFn: () => api.getAlerts(store!.id, { status: 'open,acknowledged', limit: latestCount }),
    enabled: Boolean(store),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
  const queries = [cameras, all, today, latest];
  const isLoading = queries.some((query) => query.isLoading);
  const isFetching = queries.some((query) => query.isFetching);
  const error = queries.find((query) => query.error)?.error ?? null;
  const refetch = () => Promise.all(queries.map((query) => query.refetch()));

  const cameraList = useMemo(
    () => [...(cameras.data ?? [])].sort((a, b) => (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9) || a.name.localeCompare(b.name)),
    [cameras.data],
  );

  if (!store || isLoading) return <ScreenLoader label="Loading overview" />;

  const needsDecision = countOf(all.data, ['open', 'acknowledged']);
  const cameraTotal = cameraList.length;
  const onlineTotal = cameraList.filter((camera) => camera.status === 'online').length;
  const silent = cameraTotal - onlineTotal;
  const todayTotal = countOf(today.data, ['open', 'acknowledged', 'confirmed', 'dismissed']);
  const confirmedTotal = countOf(all.data, ['confirmed']);
  const latestItems = latest.data?.items ?? [];

  const stats = [
    { label: 'Need a decision', value: String(needsDecision), tone: needsDecision ? theme.palette.error.main : theme.palette.text.primary, to: '/alerts?section=attention' },
    { label: 'Cameras online', value: `${onlineTotal}/${cameraTotal}`, tone: silent ? theme.palette.warning.main : theme.palette.text.primary, to: '/cameras' },
    { label: 'Alerts today', value: String(todayTotal), tone: theme.palette.text.primary, to: '/alerts?period=today' },
    { label: 'Confirmed', value: String(confirmedTotal), tone: theme.palette.text.primary, to: '/alerts?section=confirmed' },
  ];

  return (
    <Stack spacing={{ xs: 2.5, md: 3 }}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'flex-end', gap: 2 }}>
        <Box>
          <Typography component="h1" variant="h4">Overview</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.75 }}>
            {needsDecision
              ? `${needsDecision} ${needsDecision === 1 ? 'alert needs' : 'alerts need'} your decision.`
              : 'Nothing is waiting for a decision.'}
            {silent ? ` ${silent} ${silent === 1 ? 'camera has' : 'cameras have'} no signal.` : ''}
          </Typography>
        </Box>
        <Button variant="outlined" startIcon={<ReplayIcon />} disabled={isFetching} onClick={() => void refetch()}>
          {isFetching ? 'Updating' : 'Refresh'}
        </Button>
      </Box>

      {error && <InlineError message={getErrorMessage(error)} />}

      <Paper variant="outlined" component="nav" aria-label="Store summary" sx={{ display: 'grid', gridTemplateColumns: { xs: 'repeat(2, minmax(0, 1fr))', md: 'repeat(4, minmax(0, 1fr))' }, overflow: 'hidden' }}>
        {stats.map((stat, index) => (
          <Box
            key={stat.label}
            component={RouterLink}
            to={stat.to}
            sx={{
              p: { xs: 2, sm: 2.5 },
              color: 'inherit',
              textDecoration: 'none',
              // Colour inside each rule: a responsive '1px solid' alone resets
              // the colour to the text colour and draws a black line.
              borderLeft: { xs: index % 2 ? `1px solid ${theme.palette.divider}` : 0, md: index ? `1px solid ${theme.palette.divider}` : 0 },
              borderTop: { xs: index > 1 ? `1px solid ${theme.palette.divider}` : 0, md: 0 },
              transition: 'background-color 150ms ease',
              '&:hover': { bgcolor: 'action.hover' },
              '&:focus-visible': { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: -2 },
            }}
          >
            <Typography sx={{ fontSize: { xs: 28, sm: 34 }, lineHeight: 1.1, fontWeight: 600, letterSpacing: '-0.02em', fontVariantNumeric: 'tabular-nums', color: stat.tone }}>
              {stat.value}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{stat.label}</Typography>
          </Box>
        ))}
      </Paper>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1.6fr) minmax(300px, 1fr)' }, gap: { xs: 2.5, md: 3 }, alignItems: 'start' }}>
        <Box component="section" aria-labelledby="overview-latest">
          <SectionHeader id="overview-latest" title="Waiting for your decision" to="/alerts?section=attention" action={needsDecision > latestCount ? `All ${needsDecision}` : 'All alerts'} />
          {latestItems.length === 0 ? (
            <Paper variant="outlined" sx={{ p: 3, display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <CheckCircleOutlineIcon sx={{ color: 'success.main' }} />
              <Typography color="text.secondary">You are all caught up. New alerts appear here as the AI raises them.</Typography>
            </Paper>
          ) : (
            <Paper variant="outlined" component="ul" aria-label="Latest alerts needing a decision" sx={{ listStyle: 'none', m: 0, p: 0, overflow: 'hidden' }}>
              {latestItems.map((alert, index) => (
                <Box component="li" key={alert.id} sx={{ borderTop: index ? '1px solid' : 0, borderColor: 'divider' }}>
                  <AlertRow alert={alert} store={store} saving={false} onOpen={() => navigate(`/alerts?section=attention&alertId=${encodeURIComponent(alert.id)}`)} />
                </Box>
              ))}
            </Paper>
          )}
        </Box>

        <Box component="section" aria-labelledby="overview-cameras">
          <SectionHeader id="overview-cameras" title="Cameras" to="/cameras" action="Open cameras" />
          <Paper variant="outlined" component="ul" aria-label="Camera status" sx={{ listStyle: 'none', m: 0, p: 0, overflow: 'hidden' }}>
            {cameraList.map((camera, index) => <CameraStatusRow key={camera.id} camera={camera} first={index === 0} />)}
            {cameraList.length === 0 && (
              <Box component="li" sx={{ p: 2.5 }}><Typography color="text.secondary">No cameras yet.</Typography></Box>
            )}
          </Paper>
        </Box>
      </Box>
    </Stack>
  );
}

function SectionHeader({ id, title, to, action }: { id: string; title: string; to: string; action: string }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2, mb: 1.25 }}>
      <Typography id={id} component="h2" variant="h5">{title}</Typography>
      <Button component={RouterLink} to={to} endIcon={<ArrowForwardRoundedIcon />} sx={{ flexShrink: 0 }}>{action}</Button>
    </Box>
  );
}

const cameraStatusText: Record<string, string> = {
  online: 'Online',
  offline: 'No signal',
  unknown: 'Never connected',
  inactive: 'Turned off',
};

function CameraStatusRow({ camera, first }: { camera: Camera; first: boolean }) {
  const theme = useTheme();
  const online = camera.status === 'online';
  const color = online ? theme.palette.success.main : camera.status === 'inactive' ? theme.palette.text.secondary : theme.palette.warning.main;
  const ago = camera.status === 'offline' ? formatAgo(camera.lastFrameAt) : null;
  return (
    <Box component="li" sx={{ display: 'flex', alignItems: 'center', gap: 1.5, px: 2, py: 1.25, minHeight: 56, borderTop: first ? 0 : '1px solid', borderColor: 'divider' }}>
      <FiberManualRecordIcon aria-hidden="true" sx={{ fontSize: 12, color }} />
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Typography fontWeight={600} noWrap>{camera.name}</Typography>
        <Typography variant="body2" color="text.secondary" noWrap>{cameraAreaLabel(camera.cameraType)}</Typography>
      </Box>
      <Typography component="div" variant="body2" sx={{ color, fontWeight: 600, textAlign: 'right', flexShrink: 0 }}>
        {cameraStatusText[camera.status] ?? camera.status}
        {ago && <Box component="span" sx={{ display: 'block', color: 'text.secondary', fontWeight: 400 }}>{ago}</Box>}
      </Typography>
    </Box>
  );
}
