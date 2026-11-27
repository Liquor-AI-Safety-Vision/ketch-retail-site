import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import VideocamOffOutlinedIcon from '@mui/icons-material/VideocamOffOutlined';
import VideocamOutlinedIcon from '@mui/icons-material/VideocamOutlined';
import { Alert, Box, Chip, Paper, Skeleton, Stack, Typography } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
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

// Status is about the owner's decision, so it never borrows the severity
// colours: neutral while undecided, green once confirmed.
export function AlertStatusChip({ status }: { status: AlertStatus }) {
  const colors: Record<AlertStatus, 'info' | 'default' | 'success'> = {
    open: 'default',
    acknowledged: 'info',
    confirmed: 'success',
    dismissed: 'default',
  };
  return (
    <Chip
      size="small"
      variant={status === 'open' ? 'filled' : 'outlined'}
      color={colors[status] ?? 'default'}
      label={alertStatusLabels[status] ?? toTitle(status)}
    />
  );
}

// One severity scale everywhere: warning amber, high orange, critical red.
const severityColors: Record<AlertSeverity, { light: string; dark: string }> = {
  warning: { light: '#8A5A00', dark: '#E2B33F' },
  high: { light: '#B4470F', dark: '#F08A4B' },
  critical: { light: '#B42318', dark: '#F07167' },
};

export function severityColor(severity: AlertSeverity, mode: 'light' | 'dark') {
  return (severityColors[severity] ?? severityColors.warning)[mode];
}

const severityLabels: Record<AlertSeverity, string> = { warning: 'Warning', high: 'High', critical: 'Critical' };

export function SeverityChip({ severity }: { severity: AlertSeverity }) {
  const theme = useTheme();
  const color = severityColor(severity, theme.palette.mode);
  return (
    <Chip
      size="small"
      variant="outlined"
      label={severityLabels[severity] ?? toTitle(severity)}
      sx={{
        color,
        borderColor: alpha(color, 0.55),
        bgcolor: alpha(color, theme.palette.mode === 'dark' ? 0.14 : 0.08),
        ...(severity === 'critical' && { bgcolor: color, borderColor: color, color: theme.palette.mode === 'dark' ? '#1A0B09' : '#FFFFFF' }),
      }}
    />
  );
}

// Why the AI raised an alert, in an owner's words. Codes without a plain
// meaning for an owner are kept out of the main view (technical details).
const reasonLabels: Record<string, string> = {
  ZONE_ENTRY_CONFIRMED: 'Entered the restricted area',
  UNKNOWN_PERSON_ENTERED_STORAGE: 'Person not recognised as staff',
  STAFF_ENTERED_OUTSIDE_ALLOWED_WINDOW: 'Outside the staff member\'s allowed hours',
  OUTSIDE_ALLOWED_WINDOW: 'Outside allowed hours',
  WATCHLIST_FACE_MATCHED: 'Face matched the watchlist',
  UNKNOWN_FACE: 'Face not recognised',
  NO_FACE_CAPTURED: 'No usable face captured',
  FACE_IDENTIFICATION_FAILED: 'Face could not be identified',
  DRAWER_OPEN_CONFIRMED: 'Cash drawer was open',
  NO_CUSTOMER_AT_STATION: 'No customer at the counter',
};

export function reasonLabel(code: string): string | null {
  return reasonLabels[code] ?? null;
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

/** "3 min ago", "2 h ago", "5 days ago". */
export function formatAgo(value: string | null | undefined, now = Date.now()) {
  if (!value) return null;
  const at = new Date(value).getTime();
  if (Number.isNaN(at)) return null;
  const minutes = Math.max(0, Math.round((now - at) / 60_000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

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
