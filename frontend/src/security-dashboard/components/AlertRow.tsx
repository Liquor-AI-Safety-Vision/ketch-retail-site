import ChevronRightRoundedIcon from '@mui/icons-material/ChevronRightRounded';
import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded';
import { Box, ButtonBase, CircularProgress, LinearProgress, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '../auth/AuthProvider';
import type { DashboardApi } from '../lib/api';
import type { Alert, AlertDetail, Store } from '../lib/types';
import { AlertStatusChip, SeverityChip, alertTitle, formatAgo, formatDateTime, severityColor } from './common';

// One alert as a list row, with its snapshot. Used by the Alerts list and
// the latest alerts on Overview; both share the media cache below.

export function isEmergency(alert: Alert) {
  return alert.eventType === 'weapon_detected' || alert.severity === 'critical';
}

export function alertPersonLabel(alert: Alert) {
  const label = alert.identityLabel?.trim();
  if (alert.eventType === 'unknown_entry') return 'Unknown person';
  if (alert.eventType === 'suspected_cash_concealment') return 'Staff at counter';
  if (label && !/^track_\d+$/.test(label)) return label;
  return alert.eventType === 'watchlist_match' ? 'Watchlist person' : 'Unidentified';
}

export interface AlertMediaUrls {
  detail: AlertDetail;
  clipUrl: string | null;
  snapshotUrl: string | null;
}

// The alert's snapshot and clip, through short-lived playback links (the API
// never exposes the bucket). Cached for less than the link lifetime.
export async function loadAlertMedia(api: DashboardApi, storeId: string, alertId: string): Promise<AlertMediaUrls> {
  const detail = await api.getAlert(storeId, alertId);
  const clip = detail.media.find((media) => media.kind === 'clip');
  const snapshot = detail.media.find((media) => media.kind === 'snapshot');
  const [clipUrl, snapshotUrl] = await Promise.all([
    clip ? api.getMediaPlaybackUrl(storeId, alertId, clip.id).then((link) => link.url) : null,
    snapshot ? api.getMediaPlaybackUrl(storeId, alertId, snapshot.id).then((link) => link.url) : null,
  ]);
  return { detail, clipUrl, snapshotUrl };
}

export function useAlertMedia(store: Store | null, alertId: string | null) {
  const { api } = useAuth();
  return useQuery({
    queryKey: ['alert-media', store?.id, alertId],
    queryFn: () => loadAlertMedia(api, store!.id, alertId!),
    enabled: Boolean(store && alertId),
    staleTime: 5 * 60_000,
    gcTime: 10 * 60_000,
    retry: false,
  });
}

function AlertThumbnail({ alert, store }: { alert: Alert; store: Store | null }) {
  // One request per thumbnail (GET .../snapshot), not detail + playback links:
  // the full media set is only loaded when the alert is opened.
  const { api } = useAuth();
  const { data: src, isLoading } = useQuery({
    queryKey: ['alert-snapshot', store?.id, alert.id],
    queryFn: () => api.getAlertSnapshotUrl(store!.id, alert.id),
    enabled: Boolean(store && alert.hasSnapshot),
    staleTime: Infinity,
    gcTime: 30 * 60_000,
    retry: false,
  });
  return (
    <Box sx={{ position: 'relative', width: '100%', height: '100%', bgcolor: '#050606', display: 'grid', placeItems: 'center' }}>
      {src ? (
        <Box component="img" src={src} alt="" sx={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }} />
      ) : isLoading ? (
        <CircularProgress size={18} sx={{ color: '#5F6664' }} />
      ) : (
        <Typography variant="caption" sx={{ color: '#9AA19E', px: 1, textAlign: 'center' }}>No snapshot</Typography>
      )}
      <Box aria-hidden="true" sx={{ position: 'absolute', right: 6, bottom: 6, width: 24, height: 24, borderRadius: '50%', bgcolor: 'rgba(0,0,0,.6)', color: '#FFF', display: 'grid', placeItems: 'center' }}>
        <PlayArrowRoundedIcon sx={{ fontSize: 18 }} />
      </Box>
    </Box>
  );
}


// One alert per row: snapshot, what happened, where and when. The whole row
// opens the review dialog; there is no second button to aim for.
export function AlertRow({ alert, store, saving, onOpen }: { alert: Alert; store: Store | null; saving: boolean; onOpen: () => void }) {
  const theme = useTheme();
  const accent = severityColor(alert.severity, theme.palette.mode);
  const emergency = isEmergency(alert);
  const ago = formatAgo(alert.detectedAt);
  return (
    <ButtonBase
      onClick={onOpen}
      focusRipple
      sx={{
        width: '100%', display: 'flex', alignItems: 'stretch', textAlign: 'left', gap: { xs: 1.5, sm: 2 },
        p: { xs: 1.25, sm: 1.5 }, pl: { xs: 1.5, sm: 2 }, position: 'relative',
        borderLeft: '4px solid', borderLeftColor: alert.status === 'confirmed' ? 'success.main' : accent,
        transition: 'background-color 150ms ease',
        '&:hover': { bgcolor: 'action.hover' },
        '&.Mui-focusVisible': { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: -2 },
      }}
    >
      {saving && <LinearProgress sx={{ position: 'absolute', top: 0, left: 0, right: 0 }} />}
      <Box sx={{ flexShrink: 0, width: { xs: 104, sm: 144 }, aspectRatio: '16 / 9', borderRadius: 1, overflow: 'hidden', alignSelf: 'center' }}>
        <AlertThumbnail alert={alert} store={store} />
      </Box>
      <Box sx={{ minWidth: 0, flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 0.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}>
          <Typography component="span" fontWeight={600} sx={{ fontSize: 16, lineHeight: 1.3, color: emergency ? 'error.main' : 'text.primary', overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
            {alertTitle(alert.eventType)}
          </Typography>
        </Box>
        <Typography component="span" variant="body2" color="text.secondary" noWrap>
          {alert.cameraName ?? alert.cameraId} · {alertPersonLabel(alert)}
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>
          <Typography component="span" variant="body2" color="text.secondary" title={formatDateTime(alert.detectedAt)} sx={{ fontVariantNumeric: 'tabular-nums' }}>
            {ago ?? formatDateTime(alert.detectedAt)}
          </Typography>
          <Box sx={{ display: { xs: 'none', sm: 'contents' } }}>
            <SeverityChip severity={alert.severity} />
          </Box>
          {alert.status === 'acknowledged' && <AlertStatusChip status={alert.status} />}
          {alert.status === 'confirmed' && alert.reviewedByName && (
            <Typography component="span" variant="body2" color="text.secondary">· Confirmed by {alert.reviewedByName}</Typography>
          )}
        </Box>
      </Box>
      <Box sx={{ display: { xs: 'none', md: 'flex' }, alignItems: 'center', flexShrink: 0, color: 'text.secondary', gap: 0.5, pr: 0.5 }}>
        <Typography component="span" variant="body2" fontWeight={600} color={emergency ? 'error.main' : 'primary.main'}>
          {alert.status === 'confirmed' ? 'View' : emergency ? 'Review now' : 'Review'}
        </Typography>
        <ChevronRightRoundedIcon fontSize="small" />
      </Box>
    </ButtonBase>
  );
}
