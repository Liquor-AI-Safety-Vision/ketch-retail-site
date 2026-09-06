import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import VideocamOffOutlinedIcon from '@mui/icons-material/VideocamOffOutlined';
import VideocamOutlinedIcon from '@mui/icons-material/VideocamOutlined';
import { Alert, Box, Chip, Paper, Skeleton, Stack, Typography } from '@mui/material';
import type { AlertStatus, CameraStatus } from '../lib/types';

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

export function CameraStatusChip({ status, enabled = true }: { status: CameraStatus; enabled?: boolean }) {
  const label = !enabled ? 'Disabled' : status.charAt(0) + status.slice(1).toLowerCase();
  const color = !enabled || status === 'DISABLED' ? 'default' : status === 'ONLINE' ? 'success' : 'warning';
  return <Chip size="small" color={color} label={label} />;
}

export function AlertStatusChip({ status }: { status: AlertStatus }) {
  const colors: Record<AlertStatus, 'error' | 'info' | 'default' | 'success'> = {
    NEW: 'error',
    ACKNOWLEDGED: 'info',
    DISMISSED: 'default',
    RESOLVED: 'success',
  };
  return <Chip size="small" color={colors[status]} label={toTitle(status)} />;
}

export function formatDateTime(value: string | null | undefined) {
  if (!value) return 'Unavailable';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Unavailable' : new Intl.DateTimeFormat('en-US', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

export function alertTitle(type: string) {
  return type.split('_').map((part) => part.charAt(0) + part.slice(1).toLowerCase()).join(' ');
}

export function toTitle(value: string) {
  return value.split('_').map((part) => part.charAt(0) + part.slice(1).toLowerCase()).join(' ');
}

export function CameraEmptyState() {
  return <EmptyState title="No cameras found" description="Add a camera to begin configuring its secure stream reference and detection zones." icon={<VideocamOutlinedIcon sx={{ fontSize: 42 }} />} />;
}
