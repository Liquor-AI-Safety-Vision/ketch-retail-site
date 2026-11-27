import AccessTimeOutlinedIcon from '@mui/icons-material/AccessTimeOutlined';
import ArrowForwardRoundedIcon from '@mui/icons-material/ArrowForwardRounded';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import NotificationsActiveOutlinedIcon from '@mui/icons-material/NotificationsActiveOutlined';
import ReplayIcon from '@mui/icons-material/Replay';
import VideocamOutlinedIcon from '@mui/icons-material/VideocamOutlined';
import { Box, Button, Paper, Stack, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import { useMemo } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { InlineError, ScreenLoader } from '../components/common';
import { getErrorMessage } from '../lib/api';
import { startOfToday, useAlertSummary, useCameras } from '../lib/queries';
import type { AlertSummaryCount, Camera } from '../lib/types';

interface Metric {
  label: string;
  value: string;
  helper: string;
  icon: React.ReactNode;
  tone: string;
  to: string;
}

interface OverviewData {
  cameras: Camera[];
  all: AlertSummaryCount[];
  today: AlertSummaryCount[];
}

function countOf(counts: AlertSummaryCount[], statuses: string[]) {
  return counts.filter((item) => statuses.includes(item.status)).reduce((sum, item) => sum + item.count, 0);
}

export function DashboardPage() {
  const theme = useTheme();

  const { store } = useAuth();
  // Same cache entries as the Cameras and Alerts pages: no duplicate requests.
  const cameras = useCameras({ refetchInterval: 30_000 });
  const all = useAlertSummary();
  const today = useAlertSummary(startOfToday());
  const queries = [cameras, all, today];
  const isLoading = queries.some((query) => query.isLoading);
  const isFetching = queries.some((query) => query.isFetching);
  const error = queries.find((query) => query.error)?.error ?? null;
  const refetch = () => Promise.all(queries.map((query) => query.refetch()));
  const data = useMemo<OverviewData | undefined>(() => (cameras.data && all.data && today.data
    ? { cameras: cameras.data, all: all.data, today: today.data }
    : undefined), [cameras.data, all.data, today.data]);

  const metrics = useMemo<Metric[]>(() => {
    const cameras = data?.cameras ?? [];
    const cameraTotal = cameras.length;
    const onlineTotal = cameras.filter((camera) => camera.status === 'online').length;
    const needsReview = countOf(data?.all ?? [], ['open', 'acknowledged']);
    const incidentsToday = countOf(data?.today ?? [], ['open', 'acknowledged', 'confirmed', 'dismissed']);
    const confirmedTotal = countOf(data?.all ?? [], ['confirmed']);

    return [
      {
        label: 'Alerts to review',
        value: String(needsReview),
        helper: needsReview ? `${needsReview === 1 ? 'Incident needs' : 'Incidents need'} an owner decision` : 'No incidents are waiting',
        icon: <NotificationsActiveOutlinedIcon />,
        tone: needsReview ? theme.palette.error.main : theme.palette.success.main,
        to: '/alerts?section=attention',
      },
      {
        label: 'Cameras online',
        value: `${onlineTotal}/${cameraTotal}`,
        helper: onlineTotal === cameraTotal ? 'All cameras are being analysed' : `${cameraTotal - onlineTotal} with no signal: not being analysed`,
        icon: <VideocamOutlinedIcon />,
        tone: onlineTotal === cameraTotal ? theme.palette.success.main : theme.palette.warning.main,
        to: '/cameras',
      },
      {
        label: 'Alerts today',
        value: String(incidentsToday),
        helper: 'Recorded since midnight',
        icon: <AccessTimeOutlinedIcon />,
        tone: theme.palette.primary.main,
        to: '/alerts?period=today',
      },
      {
        label: 'Confirmed',
        value: String(confirmedTotal),
        helper: 'Retained for later review',
        icon: <CheckCircleOutlineIcon />,
        tone: theme.palette.info.main,
        to: '/alerts?section=confirmed',
      },
    ];
  }, [data, theme]);

  if (!store || isLoading) return <ScreenLoader label="Loading overview" />;

  const [priorityMetric, ...supportingMetrics] = metrics;

  return (
    <Stack spacing={{ xs: 3, md: 4 }}>
      <Box
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'space-between',
          alignItems: 'flex-end',
          gap: 2,
        }}
      >
        <Box>
          <Typography component="h1" variant="h4">Overview</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.75 }}>
            Monitor availability and incident decisions at a glance.
          </Typography>
        </Box>
        <Button
          variant="outlined"
          startIcon={<ReplayIcon />}
          disabled={isFetching}
          onClick={() => void refetch()}
        >
          {isFetching ? 'Updating' : 'Refresh'}
        </Button>
      </Box>

      {error && <InlineError message={getErrorMessage(error)} />}

      <Paper
        variant="outlined"
        sx={{
          overflow: 'hidden',
          borderRadius: 2,
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', lg: 'minmax(320px, 0.82fr) minmax(0, 1.45fr)' },
        }}
      >
        <Box
          component={RouterLink}
          to={priorityMetric.to}
          sx={{
            minHeight: { xs: 280, md: 330 },
            p: { xs: 3, sm: 4, lg: 4.5 },
            display: 'flex',
            flexDirection: 'column',
            color: 'inherit',
            textDecoration: 'none',
            bgcolor: alpha(priorityMetric.tone, theme.palette.mode === 'dark' ? 0.07 : 0.035),
            borderRight: { lg: '1px solid' },
            borderBottom: { xs: '1px solid', lg: 0 },
            borderColor: 'divider',
            transition: 'background-color 180ms ease',
            '&:hover': { bgcolor: alpha(priorityMetric.tone, theme.palette.mode === 'dark' ? 0.11 : 0.065) },
            '&:focus-visible': { outline: `2px solid ${priorityMetric.tone}`, outlineOffset: -2 },
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2 }}>
            <Typography color="text.secondary" fontWeight={600}>{priorityMetric.label}</Typography>
            <Box sx={{ color: priorityMetric.tone, display: 'grid', placeItems: 'center', '& svg': { fontSize: 25 } }}>
              {priorityMetric.icon}
            </Box>
          </Box>

          <Box sx={{ mt: 'auto', pt: 5 }}>
            <Typography
              sx={{
                color: priorityMetric.tone,
                fontSize: { xs: 52, md: 88 },
                lineHeight: 0.9,
                letterSpacing: '-0.04em',
                fontWeight: 600,
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {priorityMetric.value}
            </Typography>
            <Typography sx={{ mt: 2.5, maxWidth: 310, fontSize: 20, lineHeight: 1.3, fontWeight: 600, letterSpacing: '-0.01em' }}>
              {priorityMetric.helper}
            </Typography>
            <Box sx={{ mt: 3, display: 'flex', alignItems: 'center', gap: 0.75, color: priorityMetric.tone }}>
              <Typography variant="body2" fontWeight={700}>Review alerts</Typography>
              <ArrowForwardRoundedIcon sx={{ fontSize: 19 }} />
            </Box>
          </Box>
        </Box>

        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', sm: 'repeat(3, minmax(0, 1fr))' },
          }}
        >
          {supportingMetrics.map((metric, index) => (
            <Box
              key={metric.label}
              component={RouterLink}
              to={metric.to}
              sx={{
                minHeight: { xs: 190, sm: 330 },
                p: { xs: 3, sm: 3.25, xl: 4 },
                display: 'flex',
                flexDirection: 'column',
                color: 'inherit',
                textDecoration: 'none',
                borderRight: { sm: index < supportingMetrics.length - 1 ? '1px solid' : 0 },
                borderBottom: { xs: index < supportingMetrics.length - 1 ? '1px solid' : 0, sm: 0 },
                borderColor: 'divider',
                transition: 'background-color 180ms ease',
                '&:hover': { bgcolor: 'action.hover' },
                '&:focus-visible': { outline: `2px solid ${metric.tone}`, outlineOffset: -2 },
              }}
            >
              <Box sx={{ color: metric.tone, display: 'grid', placeItems: 'center', alignSelf: 'flex-start', '& svg': { fontSize: 23 } }}>
                {metric.icon}
              </Box>
              <Box sx={{ mt: 'auto', pt: 4 }}>
                <Typography
                  sx={{
                    fontSize: { xs: 34, md: 52 },
                    lineHeight: 1,
                    letterSpacing: '-0.03em',
                    fontWeight: 600,
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {metric.value}
                </Typography>
                <Typography fontWeight={600} sx={{ mt: 2 }}>{metric.label}</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.65, lineHeight: 1.45 }}>
                  {metric.helper}
                </Typography>
                <ArrowForwardRoundedIcon sx={{ mt: 2.5, color: 'text.disabled', fontSize: 19 }} />
              </Box>
            </Box>
          ))}
        </Box>
      </Paper>
    </Stack>
  );
}
