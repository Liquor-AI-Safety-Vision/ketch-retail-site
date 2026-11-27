import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import VideocamOffOutlinedIcon from '@mui/icons-material/VideocamOffOutlined';
import VideocamOutlinedIcon from '@mui/icons-material/VideocamOutlined';
import { Alert, Box, Chip, Paper, Skeleton, Stack, Typography } from '@mui/material';
import type { AlertSeverity, AlertStatus } from '../lib/types';

export function ScreenLoader({ label = 'Loading dashboard…' }: { label?: string }) {
  return (
    <Stack aria-busy="true" aria-label={label} spacing={3} sx={{ minHeight: '50vh', pt: 0.5 }}>
      <Box>
        <Skeleton variant="text" width={190} height={48} />
        <Skeleton variant="text" width="min(420px, 72%)" />
      </Box>
      <Skeleton variant="rounded" height={330} sx={{ borderRadius: 2 }} />
      <Typography variant="caption" color="text.secondary">{label}</Typography>
    </Stack>
  );
}

export function InlineError({ message }: { message: string }) {
  return <Alert severity="error" icon={<ErrorOutlineIcon />}>{message}</Alert>;
}

export function EmptyState({
  title,
  description,
  icon = <VideocamOffOutlinedIcon sx={{ fontSize: 42 }} />,
}: {
  title: string;
  description: string;
  icon?: React.ReactNode;
}) {
  return (
    <Paper variant="outlined" sx={{ py: 7, px: 3, textAlign: 'center', borderStyle: 'dashed' }}>
      <Box sx={{ color: 'text.secondary', mb: 1 }}>{icon}</Box>
      <Typography variant="h6">{title}</Typography>
      <Typography color="text.secondary" sx={{ mt: 0.75, maxWidth: 460, mx: 'auto' }}>{description}</Typography>
    </Paper>
  );
}

export function CameraStatusChip({ active }: { active: boolean }) {
  return <Chip size="small" color={active ? 'success' : 'default'} label={active ? 'Active' : 'Inactive'} />;
}

export const alertStatusLabels: Record<AlertStatus, string> = {
  open: 'Needs review',
  acknowledged: 'Acknowledged',
  confirmed: 'Confirmed',
  dismissed: 'False alarm',
};

export function AlertStatusChip({ status }: { status: AlertStatus }) {
  const colors: Record<AlertStatus, 'error' | 'info' | 'default' | 'success'> = {
    open: 'error',
    acknowledged: 'info',
    confirmed: 'success',
    dismissed: 'default',
  };
  return <Chip size="small" color={colors[status] ?? 'default'} label={alertStatusLabels[status] ?? toTitle(status)} />;
}

export function SeverityChip({ severity }: { severity: AlertSeverity }) {
  const color = severity === 'critical' ? 'error' : severity === 'high' ? 'warning' : 'default';
  return <Chip size="small" variant="outlined" color={color} label={toTitle(severity)} />;
}

// One title per event type the AI pipelines emit (ai/src/pipelines).
const eventTitles: Record<string, string> = {
  suspected_cash_concealment: 'Suspected cash concealment',
  unknown_entry: 'Unknown person in restricted area',
  staff_outside_schedule: 'Staff outside allowed hours',
  watchlist_match: 'Watchlist match at entrance',
  weapon_detected: 'Weapon detected',
};

export const knownEventTypes = Object.keys(eventTitles);

export function formatDateTime(value: string | null | undefined) {
  if (!value) return 'Unavailable';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Unavailable' : new Intl.DateTimeFormat('en-US', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

export function alertTitle(eventType: string) {
  return eventTitles[eventType] ?? toTitle(eventType);
}

export function toTitle(value: string) {
  return value.split('_').filter(Boolean).map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase()).join(' ');
}

export function CameraEmptyState() {
  return <EmptyState title="No cameras found" description="Cameras appear here once the AI service has started with this store's manifest." icon={<VideocamOutlinedIcon sx={{ fontSize: 42 }} />} />;
}

const cameraTypeLabels: Record<string, string> = {
  counter: 'Counter',
  storage: 'Storage',
  cold_storage: 'Cold storage',
  entrance: 'Entrance',
  whole_store: 'Whole store',
  cold_drinks: 'Cold drinks',
};

export function cameraAreaLabel(cameraType: string) {
  return cameraTypeLabels[cameraType] ?? toTitle(cameraType || 'other');
}

// What each AI pipeline watches for, in the words an owner would use.
const pipelineLabels: Record<string, string> = {
  counter_cash_theft_v1: 'Cash drawer and concealment detection',
  storage_intrusion_v1: 'Restricted-area entry and staff schedule',
  cold_storage_intrusion_v1: 'Restricted-area entry and staff schedule',
  entrance_face_capture_v1: 'Face capture and watchlist matching',
  whole_store_tracking_v1: 'Store-wide person tracking',
};

export function pipelineLabel(pipelineId: string) {
  return pipelineLabels[pipelineId] ?? toTitle(pipelineId.replace(/_v\d+$/, ''));
}

export function cameraCoverage(pipelines: string[]) {
  const labels = Array.from(new Set(pipelines.map(pipelineLabel)));
  return labels.length ? labels.join(' · ') : 'Recording only';
}
