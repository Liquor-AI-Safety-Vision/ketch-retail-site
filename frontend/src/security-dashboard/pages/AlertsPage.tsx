import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import ChevronLeftRoundedIcon from '@mui/icons-material/ChevronLeftRounded';
import ChevronRightRoundedIcon from '@mui/icons-material/ChevronRightRounded';
import CloseIcon from '@mui/icons-material/Close';
import DoneIcon from '@mui/icons-material/Done';
import HourglassEmptyIcon from '@mui/icons-material/HourglassEmpty';
import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded';
import ReplayIcon from '@mui/icons-material/Replay';
import {
  Alert as MuiAlert, Box, Button, ButtonBase, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, IconButton, LinearProgress, MenuItem, Pagination, Paper, Select, Snackbar, Stack,
  ToggleButton, ToggleButtonGroup, Tooltip, Typography, useMediaQuery,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import {
  AlertStatusChip, EmptyState, InlineError, ScreenLoader, SeverityChip, alertTitle, formatAgo, formatDateTime,
  reasonLabel, severityColor,
} from '../components/common';
import { ApiError, getErrorMessage, type DashboardApi } from '../lib/api';
import { queryKeys, startOfToday, useAlertSummary } from '../lib/queries';
import type { Alert, AlertAction, AlertDetail, AlertFilters, AlertPage, Store } from '../lib/types';

// Every alert here was written by the AI service (ai/): one row per event its
// pipelines raised, with a snapshot and, about post_s seconds later, a clip in
// R2. Nothing on this page is generated in the browser.

const alertsPerPage = 10;
const attentionStatuses = ['open', 'acknowledged'] as const;
const confirmedStatuses = ['confirmed'] as const;
type Section = 'attention' | 'confirmed';

const alertDescriptions: Record<string, string> = {
  suspected_cash_concealment: 'At the counter, cash was tracked leaving the drawer toward a staff member with no customer transaction in progress.',
  unknown_entry: 'A person who does not match any registered staff member entered a restricted area.',
  staff_outside_schedule: 'A registered staff member entered a restricted area outside the hours they are allowed in.',
  watchlist_match: 'A face captured at the entrance matched a person on this store\'s watchlist.',
  weapon_detected: 'An object that may be a dangerous weapon was detected. Follow the store emergency procedure.',
};

// Case folders over the AI's event types. Restricted-area alerts from the
// storage and cold-storage pipelines share one folder. A folder whose
// detector is not deployed (`hiddenWhenEmpty`) stays out of the filter until
// it has alerts, so the owner is not offered a feature they do not have.
const alertFolderDefinitions = [
  { type: 'weapon_detected', title: 'Weapon detected', eventTypes: ['weapon_detected'], emergency: true, hiddenWhenEmpty: true },
  { type: 'suspected_cash_concealment', title: 'Suspected cash concealment', eventTypes: ['suspected_cash_concealment'], emergency: false, hiddenWhenEmpty: false },
  { type: 'restricted_area', title: 'Restricted area access', eventTypes: ['unknown_entry', 'staff_outside_schedule'], emergency: false, hiddenWhenEmpty: false },
  { type: 'watchlist_match', title: 'Watchlist match', eventTypes: ['watchlist_match'], emergency: false, hiddenWhenEmpty: false },
];

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

const decisionNotes: Record<AlertAction, string> = {
  confirm: 'Owner review: confirmed as a real incident.',
  dismiss: 'Owner review: marked as a false alarm.',
  acknowledge: 'Seen; review in progress.',
};

const decisionToasts: Record<AlertAction, string> = {
  confirm: 'Confirmed as a real incident.',
  dismiss: 'Marked as a false alarm.',
  acknowledge: 'Marked as seen.',
};

export function AlertsPage() {
  const listRef = useRef<HTMLDivElement>(null);
  const { store, api, isDemo, user } = useAuth();
  const queryClient = useQueryClient();
  const theme = useTheme();
  const phone = useMediaQuery(theme.breakpoints.down('sm'));
  const readOnly = isDemo || api.readOnly;
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const [reviewingId, setReviewingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
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
  const activeSection: Section = searchParams.get('section') === 'confirmed' ? 'confirmed' : 'attention';
  const requestedAlertId = (searchParams.get('alertId') ?? '').trim();

  // Server-side paging: the list is fetched one page at a time with the
  // API's cursor, and every count comes from /alerts/summary, so both stay
  // right however many alerts the store has.
  const sectionStatuses = activeSection === 'attention' ? attentionStatuses : confirmedStatuses;
  const periodFrom = useMemo(() => (todayOnly ? startOfToday() : undefined), [todayOnly]);
  const statusParam = sectionStatuses.join(',');
  const eventTypeParam = selectedEventTypes?.join(',');
  const filterKey = `${statusParam}|${eventTypeParam ?? ''}|${periodFrom ?? ''}`;
  // cursors[i] fetches page i + 1; page 1 needs none.
  const cursorsRef = useRef<{ key: string; cursors: (string | undefined)[] }>({ key: '', cursors: [undefined] });

  // The page belongs to one filter: changing a filter starts again at page 1
  // in the same render, without first fetching the old page number.
  const [pageState, setPageState] = useState({ key: filterKey, page: 1 });
  const page = pageState.key === filterKey ? pageState.page : 1;
  const setPage = useCallback((next: number) => setPageState({ key: filterKey, page: next }), [filterKey]);

  const listQuery = useQuery({
    queryKey: [...queryKeys.alertPages(store?.id), filterKey, page],
    queryFn: async (): Promise<AlertPage & { page: number }> => {
      const filters: AlertFilters = { status: statusParam, eventType: eventTypeParam, from: periodFrom, limit: alertsPerPage };
      if (cursorsRef.current.key !== filterKey) cursorsRef.current = { key: filterKey, cursors: [undefined] };
      const cursors = cursorsRef.current.cursors;
      // Pages before this one that were never visited are walked first, for their cursors.
      while (cursors.length < page) {
        const walked = await api.getAlerts(store!.id, { ...filters, cursor: cursors[cursors.length - 1] });
        if (!walked.nextCursor) break;
        cursors.push(walked.nextCursor);
      }
      const target = Math.min(page, cursors.length);
      const result = await api.getAlerts(store!.id, { ...filters, cursor: cursors[target - 1] });
      if (result.nextCursor) cursors[target] = result.nextCursor;
      return { ...result, page: target };
    },
    enabled: Boolean(store),
    staleTime: 15_000,
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  });
  const summaryQuery = useAlertSummary(periodFrom);

  const pageItems = useMemo(() => listQuery.data?.items ?? [], [listQuery.data]);
  const total = listQuery.data?.total ?? 0;
  const summary = useMemo(() => summaryQuery.data ?? [], [summaryQuery.data]);
  // A page past the end (alerts were decided meanwhile) falls back to the last one.
  // Placeholder data is the previous page's, so it never moves the page.
  useEffect(() => {
    if (listQuery.data && !listQuery.isPlaceholderData && listQuery.data.page !== page) setPage(listQuery.data.page);
  }, [listQuery.data, listQuery.isPlaceholderData, page, setPage]);

  const refreshAll = useCallback(() => {
    cursorsRef.current = { key: '', cursors: [undefined] };
    void queryClient.invalidateQueries({ queryKey: queryKeys.alertPages(store?.id) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.alertSummaryAll(store?.id) });
  }, [queryClient, store?.id]);

  const countOf = useCallback((statuses: readonly string[], eventTypes?: readonly string[]) => summary
    .filter((item) => statuses.includes(item.status) && (!eventTypes || eventTypes.includes(item.eventType)))
    .reduce((sum, item) => sum + item.count, 0), [summary]);
  const attentionTotal = countOf(attentionStatuses);
  const confirmedTotal = countOf(confirmedStatuses);
  const emergencyTotal = countOf(attentionStatuses, ['weapon_detected']);
  const caseOptions = alertFolderDefinitions
    .map((definition) => ({
      ...definition,
      count: countOf(sectionStatuses, definition.eventTypes),
      everCount: countOf([...attentionStatuses, ...confirmedStatuses, 'dismissed'], definition.eventTypes),
    }))
    .filter((option) => !option.hiddenWhenEmpty || option.everCount > 0 || option.type === selectedType);
  const selectedFolderTitle = selectedFolderDefinition?.title ?? 'All cases';
  const pageCount = Math.max(1, Math.ceil(total / alertsPerPage));

  // The open alert lives in the URL (?alertId=), so the phone's Back button
  // closes the review instead of leaving the page, and a reload reopens it.
  // Opening from the list pushes one history entry; stepping or
  // auto-advancing replaces it, so Back always returns to the list.
  const pushedRef = useRef(false);
  const detailIdRef = useRef<string | null>(null);
  useEffect(() => { detailIdRef.current = detailAlert?.id ?? null; }, [detailAlert]);
  const showAlert = useCallback((alert: Alert, history: 'push' | 'replace') => {
    openedAlertParamRef.current = alert.id;
    setActionError(null);
    setDetailAlert(alert);
    if (history === 'push') pushedRef.current = true;
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set('alertId', alert.id);
      return next;
    }, { replace: history === 'replace' });
  }, [setSearchParams]);

  const closeDetail = useCallback(() => {
    setDetailAlert(null);
    setActionError(null);
    if (pushedRef.current) {
      pushedRef.current = false;
      navigate(-1);
      return;
    }
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      next.delete('alertId');
      return next;
    }, { replace: true });
  }, [navigate, setSearchParams]);

  // Decisions go to the API, which serializes them and keeps an audit trail;
  // the lists and counts are refetched from it, not patched optimistically.
  // A decided alert leaves this list, so the dialog moves on to the next one.
  // decidingRef blocks a second tap that lands before the buttons re-render
  // disabled; the server would refuse it anyway, with a confusing error.
  const decidingRef = useRef(false);
  const decide = async (alert: Alert, action: AlertAction, note = decisionNotes[action]) => {
    if (readOnly || !store || decidingRef.current) return false;
    decidingRef.current = true;
    setReviewingId(alert.id);
    setActionError(null);
    try {
      const result = await api.actionAlert(store.id, alert.id, action, note);
      const review = { status: result.status, reviewedAt: result.reviewedAt, reviewNote: result.reviewNote, reviewedById: user?.id ?? null, reviewedByName: user?.displayName ?? null };
      // The open dialog reads the cached detail; patch it rather than refetch,
      // which would also mint new media links and restart the clip.
      queryClient.setQueryData<AlertMediaUrls>(['alert-media', store.id, alert.id], (cached) => cached && { ...cached, detail: { ...cached.detail, ...review } });
      const leavesList = activeSection === 'attention' ? result.status !== 'open' && result.status !== 'acknowledged' : result.status !== 'confirmed';
      // Only move the dialog if the owner is still looking at this alert.
      if (detailIdRef.current === alert.id) {
        if (!leavesList) {
          setDetailAlert({ ...alert, ...review });
        } else {
          const index = pageItems.findIndex((item) => item.id === alert.id);
          const next = index >= 0 ? pageItems[index + 1] ?? pageItems[index - 1] : undefined;
          if (next) showAlert(next, 'replace');
          else closeDetail();
        }
      }
      setToast(decisionToasts[action]);
      refreshAll();
      return true;
    } catch (cause) {
      // 409: someone else (another owner or tab) decided it first.
      if (cause instanceof ApiError && cause.status === 409) {
        setActionError('This alert was already decided, possibly from another device or account. The list has been refreshed.');
        void queryClient.invalidateQueries({ queryKey: ['alert-media', store.id, alert.id] });
        refreshAll();
      } else {
        setActionError(getErrorMessage(cause));
      }
      return false;
    } finally {
      decidingRef.current = false;
      setReviewingId(null);
    }
  };

  useEffect(() => {
    if (!requestedAlertId) {
      // Back button (or a link without alertId): the review closes.
      openedAlertParamRef.current = null;
      pushedRef.current = false;
      setDetailAlert(null);
      return;
    }
    if (!store || listQuery.isLoading || openedAlertParamRef.current === requestedAlertId) return;
    openedAlertParamRef.current = requestedAlertId;
    const listed = pageItems.find((alert) => alert.id === requestedAlertId);
    if (listed) {
      setDetailAlert(listed);
      return;
    }
    // A link from a notification may point at an alert outside these lists
    // (e.g. already dismissed); show it from the API directly.
    void api.getAlert(store.id, requestedAlertId)
      .then((alert) => setDetailAlert(alert))
      .catch((cause) => setActionError(getErrorMessage(cause)));
  }, [api, listQuery.isLoading, pageItems, requestedAlertId, store]);

  const detailIndex = detailAlert ? pageItems.findIndex((item) => item.id === detailAlert.id) : -1;
  const goTo = useCallback((offset: number) => {
    if (detailIndex < 0) return;
    const target = pageItems[detailIndex + offset];
    if (target) showAlert(target, 'replace');
  }, [detailIndex, pageItems, showAlert]);

  const markConfirmedAlertAsFalseAlarm = async () => {
    if (!falseAlarmTarget) return;
    const done = await decide(falseAlarmTarget, 'dismiss', 'Owner correction: confirmed by mistake and reclassified as a false alarm.');
    if (done) setFalseAlarmTarget(null);
  };

  const updateParams = (change: (next: URLSearchParams) => void) => {
    const next = new URLSearchParams(searchParams);
    change(next);
    setSearchParams(next);
  };
  const selectCase = (type: string | null) => updateParams((next) => {
    if (type) next.set('type', type);
    else next.delete('type');
    next.set('section', activeSection);
  });
  const selectSection = (section: Section) => updateParams((next) => next.set('section', section));
  const clearPeriodFilter = () => updateParams((next) => { next.delete('period'); next.set('section', activeSection); });

  if (listQuery.isLoading || summaryQuery.isLoading) return <ScreenLoader label="Loading alerts…" />;

  const loadError = listQuery.error ?? summaryQuery.error;
  const fetching = listQuery.isFetching || summaryQuery.isFetching;

  return (
    <Stack spacing={2.5}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'end', gap: 2 }}>
        <Box>
          <Typography component="h1" variant="h4">Alerts</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.75, maxWidth: 700 }}>
            Open an alert, watch the clip, then confirm a real incident or mark a false alarm.
          </Typography>
        </Box>
        <Button variant="outlined" startIcon={<ReplayIcon />} onClick={refreshAll} disabled={fetching}>
          {fetching ? 'Updating' : 'Refresh'}
        </Button>
      </Box>

      {loadError && <InlineError message={getErrorMessage(loadError)} />}
      {actionError && !detailAlert && <InlineError message={actionError} />}
      {emergencyTotal > 0 && selectedType !== 'weapon_detected' && (
        <MuiAlert severity="error" action={<Button color="inherit" size="small" onClick={() => selectCase('weapon_detected')}>Review now</Button>}>
          {emergencyTotal === 1 ? 'A weapon alert needs' : `${emergencyTotal} weapon alerts need`} immediate review.
        </MuiAlert>
      )}

      <Box
        component="section"
        aria-label="Alert filters"
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 1.5,
          alignItems: 'center',
          p: { xs: 1.25, sm: 1.5 },
          border: '1px solid',
          borderColor: 'divider',
          borderRadius: 1.5,
          bgcolor: 'background.paper',
        }}
      >
        <ToggleButtonGroup
          exclusive
          size="small"
          value={activeSection}
          onChange={(_, value: Section | null) => value && selectSection(value)}
          aria-label="Alert status"
          sx={{ flex: { xs: '1 1 100%', sm: '0 0 auto' }, '& .MuiToggleButton-root': { flex: { xs: 1, sm: 'none' }, px: 1.75, textTransform: 'none', fontWeight: 600, fontSize: 14 } }}
        >
          <ToggleButton value="attention">
            Needs decision
            <CountBadge value={attentionTotal} tone={attentionTotal ? 'error' : 'neutral'} />
          </ToggleButton>
          <ToggleButton value="confirmed">
            Confirmed
            <CountBadge value={confirmedTotal} tone="neutral" />
          </ToggleButton>
        </ToggleButtonGroup>

        <FormControl size="small" sx={{ flex: { xs: '1 1 100%', sm: '0 1 300px' }, minWidth: 220 }}>
          <Select
            id="alert-case-filter"
            value={selectedType ?? 'ALL'}
            inputProps={{ 'aria-label': 'Case' }}
            onChange={(event) => selectCase(event.target.value === 'ALL' ? null : event.target.value)}
            // Same text size and weight as the status toggle beside it; the
            // "Case" prefix replaces a floating label that made the box taller.
            sx={{ fontSize: 14, fontWeight: 600 }}
            renderValue={(value) => (
              <Box component="span" sx={{ display: 'flex', gap: 0.75, minWidth: 0 }}>
                <Box component="span" sx={{ color: 'text.secondary', fontWeight: 400 }}>Case</Box>
                <Box component="span" sx={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {value === 'ALL' ? 'All cases' : alertFolderDefinitions.find((definition) => definition.type === value)?.title ?? 'All cases'}
                </Box>
              </Box>
            )}
          >
            <MenuItem value="ALL">
              <CaseOption title="All cases" count={activeSection === 'attention' ? attentionTotal : confirmedTotal} />
            </MenuItem>
            {caseOptions.map((option) => (
              <MenuItem key={option.type} value={option.type} sx={{ whiteSpace: 'normal', py: 1.25 }}>
                <CaseOption title={option.title} count={option.count} urgent={option.emergency && option.count > 0} />
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        {todayOnly && (
          <Chip color="primary" variant="outlined" label="Detected today" onDelete={clearPeriodFilter} />
        )}
        <Typography variant="body2" color="text.secondary" aria-live="polite" sx={{ ml: { sm: 'auto' }, fontVariantNumeric: 'tabular-nums' }}>
          {total} {total === 1 ? 'alert' : 'alerts'}
        </Typography>
      </Box>

      <Box ref={listRef} sx={{ scrollMarginTop: 96, position: 'relative' }}>
        {listQuery.isPlaceholderData && <LinearProgress sx={{ position: 'absolute', top: -6, left: 0, right: 0 }} />}
        {total === 0 ? (
          activeSection === 'attention'
            ? <EmptyState title="You are all caught up" description={`There are no alerts in ${selectedFolderTitle.toLocaleLowerCase()} waiting for a decision.`} icon={<CheckCircleOutlineIcon sx={{ fontSize: 42 }} />} />
            : <EmptyState title="No confirmed alerts yet" description={`Confirmed incidents in ${selectedFolderTitle.toLocaleLowerCase()} will appear here.`} />
        ) : (
          <Paper variant="outlined" component="ul" aria-label={activeSection === 'attention' ? 'Alerts needing a decision' : 'Confirmed alerts'} sx={{ listStyle: 'none', m: 0, p: 0, overflow: 'hidden' }}>
            {pageItems.map((alert, index) => (
              <Box component="li" key={alert.id} sx={{ borderTop: index ? '1px solid' : 0, borderColor: 'divider' }}>
                <AlertRow alert={alert} store={store} saving={reviewingId === alert.id} onOpen={() => showAlert(alert, 'push')} />
              </Box>
            ))}
          </Paper>
        )}
        {pageCount > 1 && (
          <Box sx={{ mt: 2, display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 1.5 }}>
            <Typography variant="body2" color="text.secondary">Page {page} of {pageCount}</Typography>
            <Pagination
              page={page}
              count={pageCount}
              onChange={(_, value) => { setPage(value); listRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}
              shape="rounded"
              color="primary"
              siblingCount={phone ? 0 : 1}
              boundaryCount={1}
            />
          </Box>
        )}
      </Box>

      <Dialog
        open={Boolean(detailAlert)}
        onClose={closeDetail}
        fullWidth
        fullScreen={phone}
        maxWidth="md"
        aria-labelledby="alert-detail-title"
        PaperProps={{ onKeyDown: (event: React.KeyboardEvent) => {
          // Arrow keys step through the list, unless a control (e.g. the video's seek bar) wants them.
          const target = event.target as HTMLElement;
          if (target.closest('video, input, textarea, [role="slider"]')) return;
          if (event.key === 'ArrowRight') { event.preventDefault(); goTo(1); }
          if (event.key === 'ArrowLeft') { event.preventDefault(); goTo(-1); }
        } }}
      >
        {detailAlert && (
          <AlertDetailView
            key={detailAlert.id}
            alert={detailAlert}
            store={store}
            readOnly={readOnly}
            reviewing={reviewingId === detailAlert.id}
            error={actionError}
            position={detailIndex >= 0 ? { index: detailIndex, count: pageItems.length } : null}
            onPrev={() => goTo(-1)}
            onNext={() => goTo(1)}
            onClose={closeDetail}
            onDecide={(action) => decide(detailAlert, action)}
            onReclassify={() => setFalseAlarmTarget(detailAlert)}
          />
        )}
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
            This alert will be removed from Confirmed and recorded as a false alarm. The change is kept in the audit trail.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5, gap: 1 }}>
          <Button onClick={() => setFalseAlarmTarget(null)} color="inherit" disabled={Boolean(reviewingId)}>Cancel</Button>
          <Button onClick={() => void markConfirmedAlertAsFalseAlarm()} color="error" variant="contained" disabled={Boolean(reviewingId)}>
            {reviewingId ? 'Updating…' : 'Yes, mark as false alarm'}
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={Boolean(toast)}
        autoHideDuration={3500}
        onClose={() => setToast(null)}
        message={toast}
        // On a phone the review dialog's decision bar owns the bottom edge.
        anchorOrigin={phone ? { vertical: 'top', horizontal: 'center' } : { vertical: 'bottom', horizontal: 'left' }}
      />
    </Stack>
  );
}

function CountBadge({ value, tone }: { value: number; tone: 'error' | 'neutral' }) {
  return (
    <Box
      component="span"
      sx={{
        ml: 1, px: 0.75, minWidth: 22, borderRadius: 10, fontSize: 12, lineHeight: '20px', textAlign: 'center', fontVariantNumeric: 'tabular-nums',
        bgcolor: tone === 'error' ? 'error.main' : 'action.selected',
        color: tone === 'error' ? 'error.contrastText' : 'text.primary',
      }}
    >
      {value}
    </Box>
  );
}

function CaseOption({ title, count, urgent = false }: { title: string; count: number; urgent?: boolean }) {
  return (
    <Box component="span" sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 3, width: '100%' }}>
      <Box component="span" sx={{ flex: 1, minWidth: 0, lineHeight: 1.4 }}>{title}</Box>
      <Typography component="span" variant="body2" color={urgent ? 'error.main' : 'text.secondary'} sx={{ flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>
        {count}
      </Typography>
    </Box>
  );
}

function AlertThumbnail({ alert, store }: { alert: Alert; store: Store | null }) {
  const { data, isLoading } = useAlertMedia(store, alert.hasSnapshot ? alert.id : null);
  const src = data?.snapshotUrl;
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
function AlertRow({ alert, store, saving, onOpen }: { alert: Alert; store: Store | null; saving: boolean; onOpen: () => void }) {
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

function ContextValue({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{label}</Typography>
      <Box sx={{ mt: 0.25, fontWeight: 600, fontSize: 14 }}>{children}</Box>
    </Box>
  );
}

// The review dialog. The video and facts scroll; the decision bar does not,
// so Confirm / False alarm are on screen at every size without scrolling.
function AlertDetailView({ alert, store, readOnly, reviewing, error, position, onPrev, onNext, onClose, onDecide, onReclassify }: {
  alert: Alert;
  store: Store | null;
  readOnly: boolean;
  reviewing: boolean;
  error: string | null;
  position: { index: number; count: number } | null;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  onDecide: (action: AlertAction) => Promise<boolean>;
  onReclassify: () => void;
}) {
  const { data, error: mediaError, isLoading, isFetching, refetch } = useAlertMedia(store, alert.id);
  const relinkedRef = useRef(false);
  const current = data?.detail ?? alert;
  const isConfirmed = current.status === 'confirmed';
  const isDismissed = current.status === 'dismissed';
  const undecided = current.status === 'open' || current.status === 'acknowledged';
  const reasons = current.reasonCodes.map(reasonLabel).filter((label): label is string => Boolean(label));
  const hasPrev = Boolean(position && position.index > 0);
  const hasNext = Boolean(position && position.index < position.count - 1);

  return (
    <>
      <DialogTitle id="alert-detail-title" sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 1.25, pl: { xs: 2, sm: 3 }, pr: 1, borderBottom: '1px solid', borderColor: 'divider' }}>
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography component="span" sx={{ display: 'block', fontSize: { xs: 16, sm: 20 }, fontWeight: 600, lineHeight: 1.3 }} noWrap>
            {alertTitle(current.eventType)}
          </Typography>
          <Typography component="span" variant="body2" color="text.secondary" sx={{ display: 'block' }} noWrap>
            {folderTitleForAlert(current.eventType)}{position ? ` · ${position.index + 1} of ${position.count}` : ''}
          </Typography>
        </Box>
        {position && position.count > 1 && (
          <>
            <Tooltip title="Previous (←)"><span><IconButton aria-label="Previous alert" onClick={onPrev} disabled={!hasPrev}><ChevronLeftRoundedIcon /></IconButton></span></Tooltip>
            <Tooltip title="Next (→)"><span><IconButton aria-label="Next alert" onClick={onNext} disabled={!hasNext}><ChevronRightRoundedIcon /></IconButton></span></Tooltip>
          </>
        )}
        <IconButton aria-label="Close" onClick={onClose}><CloseIcon /></IconButton>
      </DialogTitle>

      <DialogContent tabIndex={0} aria-label="Alert details" sx={{ p: { xs: 0, sm: 3 }, pt: { xs: 0, sm: '20px !important' } }}>
        <Stack spacing={2.25}>
          <Box sx={{ width: '100%', aspectRatio: '16 / 9', maxHeight: { sm: '52vh' }, bgcolor: '#050606', overflow: 'hidden', position: 'relative', display: 'grid', placeItems: 'center', borderRadius: { sm: 1 } }}>
            {isLoading ? <CircularProgress /> : data?.clipUrl ? (
              <Box
                key={data.clipUrl}
                component="video"
                src={data.clipUrl}
                poster={data.snapshotUrl ?? undefined}
                controls
                autoPlay
                muted
                playsInline
                // Playback links expire (15 min). A clip left open longer fails
                // on replay; fetch fresh links once instead of showing a dead
                // player (once only: a clip that is truly broken must not loop).
                onError={() => { if (!isFetching && !relinkedRef.current) { relinkedRef.current = true; void refetch(); } }}
                sx={{ width: '100%', height: '100%', objectFit: 'contain' }}
              />
            ) : data?.snapshotUrl ? (
              <Box component="img" src={data.snapshotUrl} alt="Alert snapshot" sx={{ width: '100%', height: '100%', objectFit: 'contain' }} />
            ) : (
              <Typography sx={{ color: '#9AA19E' }}>No media for this alert.</Typography>
            )}
          </Box>
          <Stack spacing={2} sx={{ px: { xs: 2, sm: 0 }, pb: { xs: 2, sm: 0 } }}>
            {mediaError && <InlineError message={getErrorMessage(mediaError)} />}
            {!isLoading && !mediaError && !data?.clipUrl && (
              <MuiAlert
                severity="info"
                icon={<HourglassEmptyIcon />}
                action={<Button color="inherit" size="small" onClick={() => void refetch()}>Check again</Button>}
              >
                The clip is still being recorded and uploaded; it usually appears within a minute. The snapshot is shown meanwhile.
              </MuiAlert>
            )}
            <Typography color="text.secondary" sx={{ lineHeight: 1.65 }}>{alertDescriptions[current.eventType] ?? 'Review the recorded context before making a decision.'}</Typography>
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'repeat(2, minmax(0, 1fr))', sm: 'repeat(3, minmax(0, 1fr))' }, gap: 2 }}>
              <ContextValue label="Detected">{formatDateTime(current.detectedAt)}</ContextValue>
              <ContextValue label="Camera">{current.cameraName ?? current.cameraId}</ContextValue>
              <ContextValue label="Person">{alertPersonLabel(current)}</ContextValue>
              {current.identityScore !== null && current.eventType === 'watchlist_match' && (
                <ContextValue label="Face similarity">{`${Math.round(current.identityScore * 100)}%`}</ContextValue>
              )}
              <ContextValue label="Severity"><SeverityChip severity={current.severity} /></ContextValue>
              <ContextValue label="Status"><AlertStatusChip status={current.status} /></ContextValue>
            </Box>
            {reasons.length > 0 && (
              <Box>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>Why the AI flagged it</Typography>
                <Box component="ul" sx={{ m: 0, mt: 0.5, pl: 2.5, '& li': { fontSize: 14, lineHeight: 1.6 } }}>
                  {reasons.map((reason) => <li key={reason}>{reason}</li>)}
                </Box>
              </Box>
            )}
            {current.reviewedAt && (
              <Typography variant="body2" color="text.secondary">
                Reviewed {formatDateTime(current.reviewedAt)}{current.reviewedByName ? ` by ${current.reviewedByName}` : ''}{current.reviewNote ? ` · ${current.reviewNote}` : ''}
              </Typography>
            )}
            <Box component="details" sx={{ color: 'text.secondary', fontSize: 14, '& summary': { cursor: 'pointer', minHeight: 44, display: 'flex', alignItems: 'center', fontWeight: 600 } }}>
              <summary>Technical details</summary>
              <Box sx={{ display: 'grid', gridTemplateColumns: 'auto 1fr', columnGap: 2, rowGap: 0.5, pb: 1, fontFamily: 'monospace', fontSize: 12, wordBreak: 'break-all' }}>
                <span>Alert ID</span><span>{current.id}</span>
                <span>Pipeline</span><span>{current.pipelineType}</span>
                <span>Event</span><span>{current.eventType}</span>
                {current.reasonCodes.length > 0 && <><span>Reason codes</span><span>{current.reasonCodes.join(', ')}</span></>}
              </Box>
            </Box>
          </Stack>
        </Stack>
      </DialogContent>

      <DialogActions sx={{ flexDirection: 'column', alignItems: 'stretch', gap: 1, px: { xs: 2, sm: 3 }, py: 1.5, borderTop: '1px solid', borderColor: 'divider', '& > :not(style) ~ :not(style)': { ml: 0 } }}>
        {error && <InlineError message={error} />}
        {readOnly ? (
          <Typography variant="body2" color="text.secondary">Read-only demo. Sign in as an owner to confirm or dismiss alerts.</Typography>
        ) : undecided ? (
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', justifyContent: 'flex-end', '& .MuiButton-root': { flex: { xs: '1 1 0', sm: '0 0 auto' } } }}>
            {current.status === 'open' && (
              <Button variant="text" color="inherit" startIcon={<DoneIcon />} disabled={reviewing} onClick={() => void onDecide('acknowledge')} sx={{ display: { xs: 'none', sm: 'inline-flex' } }}>
                Seen, decide later
              </Button>
            )}
            <Button variant="outlined" color="inherit" startIcon={<CloseIcon />} disabled={reviewing} onClick={() => void onDecide('dismiss')}>
              False alarm
            </Button>
            <Button variant="contained" color="primary" startIcon={<CheckCircleOutlineIcon />} disabled={reviewing} onClick={() => void onDecide('confirm')}>
              {reviewing ? 'Saving…' : 'Real incident'}
            </Button>
          </Box>
        ) : isConfirmed ? (
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2, flexWrap: 'wrap' }}>
            <Typography variant="body2" color="success.main" fontWeight={600}>Confirmed as a real incident.</Typography>
            <Button variant="text" color="inherit" onClick={onReclassify} sx={{ color: 'text.secondary', textDecoration: 'underline', textUnderlineOffset: 3 }}>
              Confirmed by mistake?
            </Button>
          </Box>
        ) : isDismissed ? (
          <Typography variant="body2" color="text.secondary">This alert was dismissed as a false alarm.</Typography>
        ) : null}
      </DialogActions>
    </>
  );
}
