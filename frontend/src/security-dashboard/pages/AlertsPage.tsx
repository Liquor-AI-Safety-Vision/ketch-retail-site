import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import CloseIcon from '@mui/icons-material/Close';
import DoneIcon from '@mui/icons-material/Done';
import HourglassEmptyIcon from '@mui/icons-material/HourglassEmpty';
import PlayCircleOutlineIcon from '@mui/icons-material/PlayCircleOutline';
import ReplayIcon from '@mui/icons-material/Replay';
import { Alert as MuiAlert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, IconButton, InputLabel, LinearProgress, MenuItem, Pagination, Paper, Select, Stack, Typography } from '@mui/material';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { AlertStatusChip, EmptyState, InlineError, ScreenLoader, SeverityChip, alertTitle, formatDateTime, toTitle } from '../components/common';
import { getErrorMessage, type DashboardApi } from '../lib/api';
import type { Alert, AlertAction, AlertDetail, Store } from '../lib/types';

// Every alert here was written by the AI service (ai/): one row per event its
// pipelines raised, with a snapshot and, about post_s seconds later, a clip in
// R2. Nothing on this page is generated in the browser.

const alertsPerPage = 5;

const alertDescriptions: Record<string, string> = {
  suspected_cash_concealment: 'At the counter, cash was tracked leaving the drawer toward a staff member with no customer transaction in progress.',
  unknown_entry: 'A person who does not match any registered staff member entered a restricted area.',
  staff_outside_schedule: 'A registered staff member entered a restricted area outside the hours they are allowed in.',
  watchlist_match: 'A face captured at the entrance matched a person on this store\'s watchlist.',
  weapon_detected: 'An object that may be a dangerous weapon was detected. Follow the store emergency procedure.',
};

// Case folders over the AI's event types. Restricted-area alerts from the
// storage and cold-storage pipelines share one folder.
const alertFolderDefinitions = [
  { type: 'weapon_detected', title: 'Weapon detected', eventTypes: ['weapon_detected'], emergency: true },
  { type: 'suspected_cash_concealment', title: 'Suspected cash concealment', eventTypes: ['suspected_cash_concealment'], emergency: false },
  { type: 'restricted_area', title: 'Restricted area access', eventTypes: ['unknown_entry', 'staff_outside_schedule'], emergency: false },
  { type: 'watchlist_match', title: 'Watchlist match', eventTypes: ['watchlist_match'], emergency: false },
];

const reasonLabels: Record<string, string> = {
  ZONE_ENTRY_CONFIRMED: 'Zone entry confirmed',
  WATCHLIST_FACE_MATCHED: 'Face matched watchlist',
  UNKNOWN_FACE: 'Face not recognised',
  NO_FACE_CAPTURED: 'No usable face captured',
  OUTSIDE_ALLOWED_WINDOW: 'Outside allowed hours',
};

function isDetectedToday(alert: Alert) {
  return new Date(alert.detectedAt).toDateString() === new Date().toDateString();
}

function isEmergency(alert: Alert) {
  return alert.eventType === 'weapon_detected' || alert.severity === 'critical';
}

function folderTitleForAlert(eventType: string) {
  return alertFolderDefinitions.find((folder) => folder.eventTypes.includes(eventType))?.title ?? alertTitle(eventType);
}

function alertPersonLabel(alert: Alert) {
  const label = alert.identityLabel?.trim();
  if (alert.eventType === 'unknown_entry') return 'Unknown person';
  if (alert.eventType === 'suspected_cash_concealment') return 'Staff at counter';
  if (label && !/^track_\d+$/.test(label)) return label;
  return alert.eventType === 'watchlist_match' ? 'Watchlist person' : 'Unidentified';
}

function reasonLabel(code: string) {
  return reasonLabels[code] ?? toTitle(code);
}

function sortActiveAlerts(items: Alert[]) {
  return [...items].sort((left, right) => {
    if (isEmergency(left) !== isEmergency(right)) return isEmergency(left) ? -1 : 1;
    return new Date(right.detectedAt).getTime() - new Date(left.detectedAt).getTime();
  });
}

interface AlertMediaUrls {
  detail: AlertDetail;
  clipUrl: string | null;
  snapshotUrl: string | null;
}

// The alert's snapshot and clip, through short-lived playback links (the API
// never exposes the bucket). Cached for less than the link lifetime.
async function loadAlertMedia(api: DashboardApi, storeId: string, alertId: string): Promise<AlertMediaUrls> {
  const detail = await api.getAlert(storeId, alertId);
  const clip = detail.media.find((media) => media.kind === 'clip');
  const snapshot = detail.media.find((media) => media.kind === 'snapshot');
  const [clipUrl, snapshotUrl] = await Promise.all([
    clip ? api.getMediaPlaybackUrl(storeId, alertId, clip.id).then((link) => link.url) : null,
    snapshot ? api.getMediaPlaybackUrl(storeId, alertId, snapshot.id).then((link) => link.url) : null,
  ]);
  return { detail, clipUrl, snapshotUrl };
}

function useAlertMedia(store: Store | null, alertId: string | null) {
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

function AlertThumbnail({ alert, store, dim = 0.72 }: { alert: Alert; store: Store | null; dim?: number }) {
  const { data, isLoading } = useAlertMedia(store, alert.hasSnapshot ? alert.id : null);
  const src = data?.snapshotUrl;
  return (
    <Box sx={{ position: 'relative', width: '100%', aspectRatio: '16 / 9', overflow: 'hidden', bgcolor: '#050606', display: 'grid', placeItems: 'center' }}>
      {src ? (
        <Box className="alert-thumbnail" component="img" src={src} alt={`Snapshot for ${alertTitle(alert.eventType)}`} sx={{ width: '100%', height: '100%', position: 'absolute', inset: 0, objectFit: 'cover', filter: `brightness(${dim}) contrast(1.12)` }} />
      ) : isLoading ? (
        <CircularProgress size={22} sx={{ color: '#5F6664' }} />
      ) : (
        <Typography variant="caption" sx={{ color: '#6A716F' }}>{alert.hasSnapshot ? 'Snapshot unavailable' : 'No snapshot'}</Typography>
      )}
    </Box>
  );
}

function AlertPagination({ page, count, onChange }: { page: number; count: number; onChange: (page: number) => void }) {
  if (count <= 1) return null;
  return (
    <Box sx={{ mt: 2.25, display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 1.5 }}>
      <Typography variant="caption" color="text.secondary">Page {page} of {count}</Typography>
      <Pagination
        page={page}
        count={count}
        onChange={(_, value) => onChange(value)}
        shape="rounded"
        color="primary"
        siblingCount={1}
        boundaryCount={1}
        size="small"
      />
    </Box>
  );
}

const decisionNotes: Record<AlertAction, string> = {
  confirm: 'Owner review: confirmed as a real incident.',
  dismiss: 'Owner review: marked as a false alarm.',
  acknowledge: 'Seen; review in progress.',
};

export function AlertsPage() {
  const attentionSectionRef = useRef<HTMLDivElement>(null);
  const confirmedSectionRef = useRef<HTMLDivElement>(null);
  const { store, api, isDemo, user } = useAuth();
  const queryClient = useQueryClient();
  const readOnly = isDemo || api.readOnly;
  const [searchParams, setSearchParams] = useSearchParams();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [confirmedAlerts, setConfirmedAlerts] = useState<Alert[]>([]);
  const [attentionPage, setAttentionPage] = useState(1);
  const [confirmedPage, setConfirmedPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reviewingId, setReviewingId] = useState<string | null>(null);
  const [detailAlert, setDetailAlert] = useState<Alert | null>(null);
  const [falseAlarmTarget, setFalseAlarmTarget] = useState<Alert | null>(null);
  const openedAlertParamRef = useRef<string | null>(null);
  const requestedType = searchParams.get('type');
  const selectedFolderDefinition = requestedType
    ? alertFolderDefinitions.find((folder) => folder.type === requestedType || folder.eventTypes.includes(requestedType)) ?? null
    : null;
  const selectedType = selectedFolderDefinition?.type ?? null;
  const selectedEventTypes = selectedFolderDefinition?.eventTypes ?? null;
  const todayOnly = searchParams.get('period') === 'today';
  const requestedSection = searchParams.get('section');
  const activeSection: 'attention' | 'confirmed' = requestedSection === 'confirmed' ? 'confirmed' : 'attention';
  const requestedAlertId = (searchParams.get('alertId') ?? '').trim();

  const loadAlerts = useCallback(async () => {
    if (!store) return;
    setLoading(true);
    setError(null);
    try {
      const [openResult, acknowledgedResult, confirmedResult] = await Promise.all([
        api.getAlerts(store.id, { status: 'open', limit: 100 }),
        api.getAlerts(store.id, { status: 'acknowledged', limit: 100 }),
        api.getAlerts(store.id, { status: 'confirmed', limit: 100 }),
      ]);
      setAlerts(sortActiveAlerts([...openResult.items, ...acknowledgedResult.items]));
      setConfirmedAlerts(confirmedResult.items);
    } catch (cause) {
      setError(getErrorMessage(cause));
    } finally {
      setLoading(false);
    }
  }, [api, store]);

  useEffect(() => { void loadAlerts(); }, [loadAlerts]);

  const periodAlerts = useMemo(
    () => todayOnly ? alerts.filter(isDetectedToday) : alerts,
    [alerts, todayOnly],
  );
  const periodConfirmedAlerts = useMemo(
    () => todayOnly ? confirmedAlerts.filter(isDetectedToday) : confirmedAlerts,
    [confirmedAlerts, todayOnly],
  );
  const caseOptions = alertFolderDefinitions.map((definition) => ({
    ...definition,
    count: (activeSection === 'attention' ? periodAlerts : periodConfirmedAlerts)
      .filter((alert) => definition.eventTypes.includes(alert.eventType)).length,
  }));
  const filteredAlerts = selectedEventTypes
    ? periodAlerts.filter((alert) => selectedEventTypes.includes(alert.eventType))
    : periodAlerts;
  const filteredConfirmedAlerts = selectedEventTypes
    ? periodConfirmedAlerts.filter((alert) => selectedEventTypes.includes(alert.eventType))
    : periodConfirmedAlerts;
  const selectedFolderTitle = selectedFolderDefinition?.title ?? 'All situations';
  const currentResultCount = activeSection === 'attention' ? filteredAlerts.length : filteredConfirmedAlerts.length;

  const attentionPageCount = Math.max(1, Math.ceil(filteredAlerts.length / alertsPerPage));
  const confirmedPageCount = Math.max(1, Math.ceil(filteredConfirmedAlerts.length / alertsPerPage));
  const safeAttentionPage = Math.min(attentionPage, attentionPageCount);
  const safeConfirmedPage = Math.min(confirmedPage, confirmedPageCount);
  const visibleAlerts = filteredAlerts.slice((safeAttentionPage - 1) * alertsPerPage, safeAttentionPage * alertsPerPage);
  const visibleConfirmedAlerts = filteredConfirmedAlerts.slice((safeConfirmedPage - 1) * alertsPerPage, safeConfirmedPage * alertsPerPage);

  useEffect(() => {
    setAttentionPage((current) => Math.min(current, attentionPageCount));
  }, [attentionPageCount]);

  useEffect(() => {
    setConfirmedPage((current) => Math.min(current, confirmedPageCount));
  }, [confirmedPageCount]);

  useEffect(() => {
    setAttentionPage(1);
    setConfirmedPage(1);
  }, [selectedType, todayOnly, activeSection]);

  // Decisions go to the API, which serializes them and keeps an audit trail;
  // the lists are updated from its answer, not optimistically.
  const decide = async (alert: Alert, action: AlertAction, note = decisionNotes[action]) => {
    if (readOnly || !store || reviewingId) return false;
    setReviewingId(alert.id);
    setError(null);
    try {
      const result = await api.actionAlert(store.id, alert.id, action, note);
      const review = { status: result.status, reviewedAt: result.reviewedAt, reviewNote: result.reviewNote, reviewedById: user?.id ?? null, reviewedByName: user?.displayName ?? null };
      const updated: Alert = { ...alert, ...review };
      // The open dialog reads the cached detail; patch it rather than refetch,
      // which would also mint new media links and restart the clip.
      queryClient.setQueryData<AlertMediaUrls>(['alert-media', store.id, alert.id], (cached) => cached && { ...cached, detail: { ...cached.detail, ...review } });
      setAlerts((current) => result.status === 'acknowledged'
        ? current.map((item) => item.id === alert.id ? updated : item)
        : current.filter((item) => item.id !== alert.id));
      setConfirmedAlerts((current) => result.status === 'confirmed'
        ? [updated, ...current.filter((item) => item.id !== alert.id)]
        : current.filter((item) => item.id !== alert.id));
      setDetailAlert((current) => current?.id === alert.id ? (result.status === 'acknowledged' ? updated : null) : current);
      window.dispatchEvent(new Event('alerts:changed'));
      return true;
    } catch (cause) {
      setError(getErrorMessage(cause));
      return false;
    } finally {
      setReviewingId(null);
    }
  };

  useEffect(() => {
    if (!requestedAlertId) {
      openedAlertParamRef.current = null;
      return;
    }
    if (!store || loading || openedAlertParamRef.current === requestedAlertId) return;
    openedAlertParamRef.current = requestedAlertId;
    const listed = [...alerts, ...confirmedAlerts].find((alert) => alert.id === requestedAlertId);
    if (listed) {
      setDetailAlert(listed);
      return;
    }
    // A link from a notification may point at an alert outside these lists
    // (e.g. already dismissed); show it from the API directly.
    void api.getAlert(store.id, requestedAlertId)
      .then((alert) => setDetailAlert(alert))
      .catch((cause) => setError(getErrorMessage(cause)));
  }, [alerts, api, confirmedAlerts, loading, requestedAlertId, store]);

  const closeDetail = () => {
    setDetailAlert(null);
    if (!requestedAlertId) return;
    const next = new URLSearchParams(searchParams);
    next.delete('alertId');
    setSearchParams(next, { replace: true });
  };

  const markConfirmedAlertAsFalseAlarm = async () => {
    if (!falseAlarmTarget) return;
    const done = await decide(falseAlarmTarget, 'dismiss', 'Owner correction: confirmed by mistake and reclassified as a false alarm.');
    if (done) setFalseAlarmTarget(null);
  };

  const selectCase = (type: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (type) next.set('type', type);
    else next.delete('type');
    next.set('section', activeSection);
    setSearchParams(next);
  };

  const selectStatus = (status: 'attention' | 'confirmed') => {
    const next = new URLSearchParams(searchParams);
    next.set('section', status);
    setSearchParams(next);
  };

  const clearPeriodFilter = () => {
    const next = new URLSearchParams(searchParams);
    next.delete('period');
    next.set('section', activeSection);
    setSearchParams(next);
  };

  if (loading) return <ScreenLoader label="Loading alerts…" />;

  return (
    <Stack spacing={3.5}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'end', gap: 2 }}>
        <Box>
          <Typography component="h1" variant="h4">Alert</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.75, maxWidth: 700 }}>
            Alerts raised by the AI on this store&apos;s cameras. Review the clip, then confirm a real incident or dismiss a false alarm.
          </Typography>
        </Box>
        <Button variant="outlined" startIcon={<ReplayIcon />} onClick={() => void loadAlerts()}>Refresh</Button>
      </Box>

      {error && <InlineError message={error} />}

      <Paper
        variant="outlined"
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', sm: 'repeat(3, minmax(0, 1fr))' },
          overflow: 'hidden',
          bgcolor: 'background.paper',
        }}
      >
        <Box sx={{ px: 2.5, py: 2.25 }}>
          <Typography sx={{ fontSize: 30, lineHeight: 1, fontWeight: 680 }}>
            {periodAlerts.length + periodConfirmedAlerts.length}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.7 }}>Total alerts</Typography>
        </Box>
        <Box sx={{ px: 2.5, py: 2.25, borderTop: { xs: '1px solid', sm: 0 }, borderLeft: { sm: '1px solid' }, borderColor: 'divider' }}>
          <Typography sx={{ fontSize: 30, lineHeight: 1, fontWeight: 680, color: periodAlerts.length ? 'error.main' : 'text.primary' }}>{periodAlerts.length}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.7 }}>Needs an owner decision</Typography>
        </Box>
        <Box sx={{ px: 2.5, py: 2.25, borderTop: { xs: '1px solid', sm: 0 }, borderLeft: { sm: '1px solid' }, borderColor: 'divider' }}>
          <Typography sx={{ fontSize: 30, lineHeight: 1, fontWeight: 680 }}>{periodConfirmedAlerts.length}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.7 }}>Confirmed incidents</Typography>
        </Box>
      </Paper>

      <Box
        component="section"
        aria-label="Alert filters"
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'repeat(2, minmax(0, 1fr))', lg: 'minmax(0, 1.2fr) minmax(0, .8fr) auto' },
          gap: 1.5,
          alignItems: 'center',
          p: { xs: 1.5, sm: 2 },
          border: '1px solid',
          borderColor: 'divider',
          borderRadius: 1.5,
          bgcolor: 'background.paper',
        }}
      >
        <FormControl size="small" fullWidth>
          <InputLabel id="alert-case-filter-label">Case</InputLabel>
          <Select
            labelId="alert-case-filter-label"
            id="alert-case-filter"
            value={selectedType ?? 'ALL'}
            label="Case"
            MenuProps={{ slotProps: { paper: { sx: { maxHeight: 400 } } } }}
            onChange={(event) => selectCase(event.target.value === 'ALL' ? null : event.target.value)}
            renderValue={(value) => value === 'ALL'
              ? 'All cases'
              : alertFolderDefinitions.find((definition) => definition.type === value)?.title ?? 'All cases'}
          >
            <MenuItem value="ALL">
              <Box component="span" sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 3, width: '100%' }}>
                <span>All cases</span>
                <Typography component="span" variant="caption" color="text.secondary" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                  {activeSection === 'attention' ? periodAlerts.length : periodConfirmedAlerts.length}
                </Typography>
              </Box>
            </MenuItem>
            {caseOptions.map((option) => (
              <MenuItem key={option.type} value={option.type} sx={{ whiteSpace: 'normal', py: 1.25 }}>
                <Box component="span" sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 3, width: '100%' }}>
                  <Box component="span" sx={{ flex: 1, minWidth: 0, lineHeight: 1.4 }}>{option.title}</Box>
                  <Typography component="span" variant="caption" color={option.emergency && option.count > 0 ? 'error.main' : 'text.secondary'} sx={{ flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>
                    {option.count}
                  </Typography>
                </Box>
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl size="small" fullWidth>
          <InputLabel id="alert-status-filter-label">Status</InputLabel>
          <Select
            labelId="alert-status-filter-label"
            id="alert-status-filter"
            value={activeSection}
            label="Status"
            onChange={(event) => selectStatus(event.target.value as 'attention' | 'confirmed')}
          >
            <MenuItem value="attention">Needs attention</MenuItem>
            <MenuItem value="confirmed">Confirmed</MenuItem>
          </Select>
        </FormControl>

        <Box
          aria-live="polite"
          sx={{
            minWidth: { lg: 150 },
            gridColumn: { sm: '1 / -1', lg: 'auto' },
            pt: { sm: 1.5, lg: 0 },
            pl: { lg: 1.5 },
            borderTop: { sm: '1px solid', lg: 0 },
            borderLeft: { lg: '1px solid' },
            borderColor: 'divider',
          }}
        >
          <Typography variant="caption" color="text.secondary">Showing</Typography>
          <Typography sx={{ mt: 0.15, fontWeight: 650, fontVariantNumeric: 'tabular-nums' }}>
            {currentResultCount} {activeSection === 'attention' ? 'to review' : 'confirmed'}
          </Typography>
          {todayOnly && (
            <Chip
              size="small"
              color="primary"
              variant="outlined"
              label="Detected today"
              onDelete={clearPeriodFilter}
              sx={{ mt: 1 }}
            />
          )}
        </Box>
      </Box>

      {activeSection === 'attention' && (
      <Box ref={attentionSectionRef} sx={{ scrollMarginTop: 96 }}>
        <Typography variant="h5">Needs attention</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, mb: 2 }}>
          {selectedType ? `${selectedFolderTitle} · ` : ''}{todayOnly ? 'Detected today · ' : ''}Open and acknowledged alerts, emergencies first.
        </Typography>
      {filteredAlerts.length === 0 ? (
        <EmptyState title="You are all caught up" description={`There are no alerts in ${selectedFolderTitle.toLocaleLowerCase()} to review.`} />
      ) : (
        <Stack spacing={1.5}>
          {visibleAlerts.map((alert) => {
            const isSaving = reviewingId === alert.id;
            const emergency = isEmergency(alert);
            return (
              <Paper key={alert.id} className="alert-review-card" variant="outlined" sx={{ overflow: 'hidden', position: 'relative' }}>
                {isSaving && <LinearProgress sx={{ position: 'absolute', zIndex: 2, top: 0, left: 0, right: 0 }} />}
                <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '280px minmax(0, 1fr)' }, minHeight: 196 }}>
                  <ThumbnailButton alert={alert} store={store} onOpen={() => setDetailAlert(alert)} />

                  <Box sx={{ p: { xs: 2.25, md: 3 }, minWidth: 0 }}>
                    <Stack direction="row" spacing={1.25} alignItems="center" useFlexGap flexWrap="wrap">
                      <Typography variant="overline" sx={{ color: emergency ? 'error.main' : 'primary.main', lineHeight: 1.4 }}>
                        {emergency ? 'Emergency review' : folderTitleForAlert(alert.eventType)}
                      </Typography>
                      <Typography aria-hidden="true" color="divider">·</Typography>
                      <Typography variant="body2" color="text.secondary">{formatDateTime(alert.detectedAt)}</Typography>
                      <SeverityChip severity={alert.severity} />
                      {alert.status === 'acknowledged' && <AlertStatusChip status={alert.status} />}
                    </Stack>
                    <Typography variant="h5" sx={{ mt: 1.5 }}>{alertTitle(alert.eventType)}</Typography>
                    <Typography color="text.secondary" sx={{ mt: 1, lineHeight: 1.65 }}>{alertDescriptions[alert.eventType] ?? 'The AI detected activity that needs a quick review.'}</Typography>
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2.5, mt: 2 }}>
                      <ContextValue label="Camera" value={alert.cameraName ?? alert.cameraId} />
                      <ContextValue label="Person" value={alertPersonLabel(alert)} />
                      <ContextValue label="Clip" value={alert.hasVideoEvidence ? 'Recorded' : 'Uploading…'} />
                    </Box>
                    <Stack direction={{ xs: 'column', sm: 'row' }} alignItems={{ xs: 'stretch', sm: 'center' }} justifyContent="space-between" spacing={1.5} sx={{ mt: 2.5 }}>
                      <Typography variant="caption" color={emergency ? 'error.main' : 'text.secondary'} sx={{ maxWidth: 420, lineHeight: 1.55 }}>
                        {emergency
                          ? 'Immediate review required. Follow the store emergency procedure after reviewing the footage.'
                          : 'Open the footage before confirming or dismissing this alert.'}
                      </Typography>
                      <Button
                        variant={emergency ? 'contained' : 'outlined'}
                        color={emergency ? 'error' : 'primary'}
                        startIcon={<PlayCircleOutlineIcon />}
                        onClick={() => setDetailAlert(alert)}
                        sx={{ flexShrink: 0, alignSelf: { xs: 'flex-start', sm: 'center' } }}
                      >
                        {emergency ? 'Review now' : 'Review video'}
                      </Button>
                    </Stack>
                  </Box>
                </Box>
              </Paper>
            );
          })}
          <AlertPagination
            page={safeAttentionPage}
            count={attentionPageCount}
            onChange={(page) => {
              setAttentionPage(page);
              attentionSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }}
          />
        </Stack>
      )}
      </Box>
      )}

      {activeSection === 'confirmed' && (
      <Box ref={confirmedSectionRef} sx={{ pt: 1, scrollMarginTop: 96 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="end" gap={2} sx={{ mb: 2 }}>
          <Box>
            <Typography variant="h5">Confirmed alerts</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>Real incidents confirmed by the owner, retained for later review.</Typography>
          </Box>
          <Chip size="small" color="success" variant="outlined" label={`${filteredConfirmedAlerts.length} confirmed`} />
        </Stack>
        {filteredConfirmedAlerts.length === 0 ? (
          <EmptyState title="No confirmed alerts yet" description={`Confirmed incidents in ${selectedFolderTitle.toLocaleLowerCase()} will appear here.`} />
        ) : (
          <Stack spacing={1.5}>
            {visibleConfirmedAlerts.map((alert) => (
              <ConfirmedAlertCard key={alert.id} alert={alert} store={store} onOpen={() => setDetailAlert(alert)} />
            ))}
            <AlertPagination
              page={safeConfirmedPage}
              count={confirmedPageCount}
              onChange={(page) => {
                setConfirmedPage(page);
                confirmedSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
              }}
            />
          </Stack>
        )}
      </Box>
      )}

      <Dialog open={Boolean(detailAlert)} onClose={closeDetail} fullWidth maxWidth="md">
        <DialogTitle sx={{ pr: 7 }}>
          {detailAlert ? folderTitleForAlert(detailAlert.eventType) : 'Alert context'}
          <IconButton aria-label="Close" onClick={closeDetail} sx={{ position: 'absolute', right: 12, top: 10 }}><CloseIcon /></IconButton>
        </DialogTitle>
        <DialogContent>
          {detailAlert && (
            <AlertContext
              alert={detailAlert}
              store={store}
              readOnly={readOnly}
              reviewing={reviewingId === detailAlert.id}
              onDecide={(action) => decide(detailAlert, action)}
              onReclassify={() => setFalseAlarmTarget(detailAlert)}
            />
          )}
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(falseAlarmTarget)}
        onClose={() => !reviewingId && setFalseAlarmTarget(null)}
        fullWidth
        maxWidth="xs"
        aria-labelledby="false-alarm-confirmation-title"
      >
        <DialogTitle id="false-alarm-confirmation-title">Mark this alert as a false alarm?</DialogTitle>
        <DialogContent>
          <Typography color="text.secondary" sx={{ lineHeight: 1.7 }}>
            This alert will be removed from Confirmed alerts and recorded as a false alarm. The change is kept in the audit trail.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5, gap: 1 }}>
          <Button onClick={() => setFalseAlarmTarget(null)} color="inherit" disabled={Boolean(reviewingId)}>Cancel</Button>
          <Button onClick={() => void markConfirmedAlertAsFalseAlarm()} color="error" variant="contained" disabled={Boolean(reviewingId)}>
            {reviewingId ? 'Updating…' : 'Yes, mark as false alarm'}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}

function ThumbnailButton({ alert, store, onOpen, dim }: { alert: Alert; store: Store | null; onOpen: () => void; dim?: number }) {
  return (
    <Box
      component="button"
      type="button"
      aria-label={`Play video for ${alertTitle(alert.eventType)}`}
      onClick={onOpen}
      sx={{
        position: 'relative',
        minHeight: { xs: 210, md: '100%' },
        overflow: 'hidden',
        bgcolor: '#050606',
        display: 'grid',
        placeItems: 'center',
        width: '100%',
        p: 0,
        border: 0,
        color: 'inherit',
        cursor: 'pointer',
        '& .alert-thumbnail': { transition: 'transform 280ms ease, filter 280ms ease' },
        '& .play-indicator': { opacity: 0, transform: 'scale(.92)', transition: 'opacity 180ms ease, transform 180ms ease' },
        '&:hover .alert-thumbnail': { transform: 'scale(1.018)', filter: 'brightness(.62) contrast(1.14)' },
        '&:hover .play-indicator, &:focus-visible .play-indicator': { opacity: 1, transform: 'scale(1)' },
        '&:focus-visible': { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: -2 },
      }}
    >
      <AlertThumbnail alert={alert} store={store} dim={dim} />
      <Box className="play-indicator" aria-hidden="true" sx={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', pointerEvents: 'none' }}>
        <PlayCircleOutlineIcon sx={{ color: '#FFF', fontSize: 45, filter: 'drop-shadow(0 2px 10px rgba(0,0,0,.72))' }} />
      </Box>
    </Box>
  );
}

function ConfirmedAlertCard({ alert, store, onOpen }: { alert: Alert; store: Store | null; onOpen: () => void }) {
  return (
    <Paper className="alert-review-card" variant="outlined" sx={{ overflow: 'hidden' }}>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '280px minmax(0, 1fr)' }, minHeight: 190 }}>
        <ThumbnailButton alert={alert} store={store} onOpen={onOpen} dim={0.66} />
        <Box sx={{ p: { xs: 2.25, md: 3 }, minWidth: 0 }}>
          <Stack direction="row" spacing={1.25} alignItems="center" useFlexGap flexWrap="wrap">
            <Typography variant="overline" color="success.main" sx={{ lineHeight: 1.4 }}>Confirmed incident</Typography>
            <Typography aria-hidden="true" color="divider">·</Typography>
            <Typography variant="body2" color="text.secondary">{folderTitleForAlert(alert.eventType)}</Typography>
            <Typography aria-hidden="true" color="divider">·</Typography>
            <Typography variant="body2" color="text.secondary">{formatDateTime(alert.detectedAt)}</Typography>
          </Stack>
          <Typography variant="h6" sx={{ mt: 1.5 }}>{alertTitle(alert.eventType)}</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.75, lineHeight: 1.65 }}>{alertDescriptions[alert.eventType] ?? 'Confirmed security incident.'}</Typography>
          <Stack direction={{ xs: 'column', sm: 'row' }} alignItems={{ xs: 'stretch', sm: 'end' }} justifyContent="space-between" gap={2} sx={{ mt: 2 }}>
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2.5 }}>
              <ContextValue label="Camera" value={alert.cameraName ?? alert.cameraId} />
              <ContextValue label="Person" value={alertPersonLabel(alert)} />
              {alert.reviewedByName && <ContextValue label="Confirmed by" value={alert.reviewedByName} />}
            </Box>
            <Button variant="text" startIcon={<PlayCircleOutlineIcon />} onClick={onOpen} sx={{ flexShrink: 0, alignSelf: { xs: 'flex-start', sm: 'end' } }}>
              View incident
            </Button>
          </Stack>
        </Box>
      </Box>
    </Paper>
  );
}

function ContextValue({ label, value }: { label: string; value: string }) {
  return <Box><Typography variant="caption" color="text.secondary">{label}</Typography><Typography variant="body2" fontWeight={650} sx={{ mt: 0.2 }}>{value}</Typography></Box>;
}

function AlertContext({ alert, store, readOnly, reviewing, onDecide, onReclassify }: {
  alert: Alert;
  store: Store | null;
  readOnly: boolean;
  reviewing: boolean;
  onDecide: (action: AlertAction) => Promise<boolean>;
  onReclassify: () => void;
}) {
  const { data, error, isLoading, refetch } = useAlertMedia(store, alert.id);
  const current = data?.detail ?? alert;
  const isConfirmed = current.status === 'confirmed';
  const isDismissed = current.status === 'dismissed';
  const undecided = current.status === 'open' || current.status === 'acknowledged';

  return (
    <Stack spacing={2.5}>
      <Box sx={{ aspectRatio: '16 / 9', bgcolor: '#050606', overflow: 'hidden', position: 'relative', display: 'grid', placeItems: 'center' }}>
        {isLoading ? <CircularProgress /> : data?.clipUrl ? (
          <Box key={data.clipUrl} component="video" src={data.clipUrl} poster={data.snapshotUrl ?? undefined} controls autoPlay muted playsInline sx={{ width: '100%', height: '100%', objectFit: 'contain' }} />
        ) : data?.snapshotUrl ? (
          <Box component="img" src={data.snapshotUrl} alt="Alert snapshot" sx={{ width: '100%', height: '100%', objectFit: 'contain' }} />
        ) : (
          <Typography sx={{ color: '#6A716F' }}>No media for this alert.</Typography>
        )}
      </Box>
      {error && <InlineError message={getErrorMessage(error)} />}
      {!isLoading && !error && !data?.clipUrl && (
        <MuiAlert
          severity="info"
          icon={<HourglassEmptyIcon />}
          action={<Button color="inherit" size="small" onClick={() => void refetch()}>Check again</Button>}
        >
          The clip is recorded for 15 seconds after the alert and then uploaded; it usually appears within a minute. The snapshot is shown meanwhile.
        </MuiAlert>
      )}
      {readOnly && <MuiAlert severity="info">Read-only demo. Sign in as an owner to review your store&apos;s alerts.</MuiAlert>}
      <Typography color="text.secondary" sx={{ lineHeight: 1.7 }}>{alertDescriptions[current.eventType] ?? 'Review the recorded context before making a decision.'}</Typography>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2.5 }}>
        <ContextValue label="Detected" value={formatDateTime(current.detectedAt)} />
        <ContextValue label="Camera" value={current.cameraName ?? current.cameraId} />
        <ContextValue label="Person" value={alertPersonLabel(current)} />
        {current.identityScore !== null && current.eventType === 'watchlist_match' && (
          <ContextValue label="Face similarity" value={`${Math.round(current.identityScore * 100)}%`} />
        )}
        <Box><Typography variant="caption" color="text.secondary">Status</Typography><Box sx={{ mt: 0.3 }}><AlertStatusChip status={current.status} /></Box></Box>
        <Box><Typography variant="caption" color="text.secondary">Severity</Typography><Box sx={{ mt: 0.3 }}><SeverityChip severity={current.severity} /></Box></Box>
      </Box>
      {current.reasonCodes.length > 0 && (
        <Stack direction="row" spacing={0.75} useFlexGap flexWrap="wrap" aria-label="Why the AI raised this alert">
          {current.reasonCodes.map((code) => <Chip key={code} size="small" variant="outlined" label={reasonLabel(code)} />)}
        </Stack>
      )}
      {current.reviewedAt && (
        <Typography variant="body2" color="text.secondary">
          Reviewed {formatDateTime(current.reviewedAt)}{current.reviewedByName ? ` by ${current.reviewedByName}` : ''}{current.reviewNote ? ` · ${current.reviewNote}` : ''}
        </Typography>
      )}
      {!readOnly && undecided && (
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} justifyContent="flex-end">
          {current.status === 'open' && (
            <Button variant="text" color="inherit" startIcon={<DoneIcon />} disabled={reviewing} onClick={() => void onDecide('acknowledge')}>
              Acknowledge
            </Button>
          )}
          <Button variant="outlined" startIcon={<CloseIcon />} disabled={reviewing} onClick={() => void onDecide('dismiss')} sx={{ minWidth: 132 }}>
            False alarm
          </Button>
          <Button variant="contained" color="success" startIcon={<CheckCircleOutlineIcon />} disabled={reviewing} onClick={() => void onDecide('confirm')} sx={{ minWidth: 160 }}>
            Confirm · Real incident
          </Button>
        </Stack>
      )}
      {!readOnly && isConfirmed && (
        <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Button
            variant="text"
            color="inherit"
            onClick={onReclassify}
            sx={{ minWidth: 0, p: 0.25, color: 'text.secondary', fontSize: 13, fontWeight: 500, textDecoration: 'underline', textUnderlineOffset: 3, '&:hover': { color: 'error.main', bgcolor: 'transparent', textDecoration: 'underline' } }}
          >
            Confirmed by mistake?
          </Button>
        </Box>
      )}
      {isDismissed && <MuiAlert severity="success">This alert was dismissed as a false alarm.</MuiAlert>}
    </Stack>
  );
}
