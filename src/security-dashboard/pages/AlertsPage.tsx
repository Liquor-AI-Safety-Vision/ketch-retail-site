import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import CloseIcon from '@mui/icons-material/Close';
import PlayCircleOutlineIcon from '@mui/icons-material/PlayCircleOutline';
import ReplayIcon from '@mui/icons-material/Replay';
import { Alert as MuiAlert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, IconButton, InputLabel, LinearProgress, MenuItem, Pagination, Paper, Select, Stack, Typography } from '@mui/material';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { EmptyState, InlineError, ScreenLoader, alertTitle, formatDateTime, toTitle } from '../components/common';
import { getErrorMessage } from '../lib/api';
import { hideConfirmedAlertLocally, isPresentationOnlyAlert, presentationBaseAlertId, setPresentationAlertDecision, storageAlertSessionFor } from '../lib/presentationAlerts';
import type { Alert, AlertDetail } from '../lib/types';

type ReviewDecision = 'match' | 'false-alarm';

const alertsPerPage = 5;

const contextImages = [
  '/cctv/counter.png',
  '/cctv/aisle.png',
  '/cctv/stockroom.png',
  '/cctv/entrance.png',
  '/cctv/backdoor.png',
  '/cctv/parking.png',
];

const alertDescriptions: Record<string, string> = {
  CASH_DRAWER_WITHOUT_CUSTOMER: 'A staff member opened the cash drawer while no customer transaction was visible. No cash removal was confirmed.',
  SUSPICIOUS_CASH_HANDLING: 'The system tracked cash being removed from the drawer and moved toward a staff member’s pocket.',
  POS_VOID_OR_REFUND: 'Unusual register activity was detected and may need a quick review.',
  UNAUTHORIZED_STOCKROOM_ACCESS: 'A staff member entered outside the allowed schedule or an unknown person entered the stockroom.',
  HIGH_VALUE_ZONE_DWELL: 'A person stayed unusually long near high-value products.',
  WEAPON_DETECTED: 'The system detected violence or an object that may be a dangerous weapon.',
  SUSPICIOUS_PRODUCT_CONCEALMENT: 'Customer behavior consistent with possible shoplifting was detected from a whole-store camera.',
  REGULAR_SHOPLIFTER: 'A returning customer linked to a previously confirmed shoplifting incident was detected at the entrance.',
};

const alertTitles: Record<string, string> = {
  WEAPON_DETECTED: 'Violence or weapon detected',
  CASH_DRAWER_WITHOUT_CUSTOMER: 'Cash drawer opened without customer',
  SUSPICIOUS_CASH_HANDLING: 'Suspected cash concealment',
  POS_VOID_OR_REFUND: 'Suspicious register activity',
  UNAUTHORIZED_STOCKROOM_ACCESS: 'Unauthorized stockroom access',
  HIGH_VALUE_ZONE_DWELL: 'Unusual dwell near high-value products',
  SUSPICIOUS_PRODUCT_CONCEALMENT: 'Suspected customer shoplifting',
  REGULAR_SHOPLIFTER: 'Regular shoplifter',
};

const employeeBehaviorTypes = [
  'CASH_DRAWER_WITHOUT_CUSTOMER',
  'SUSPICIOUS_CASH_HANDLING',
  'POS_VOID_OR_REFUND',
] as const;

const employeeBehaviorFolderType = 'UNUSUAL_EMPLOYEE_BEHAVIOR';

const alertFolderDefinitions = [
  { type: 'WEAPON_DETECTED', title: alertTitles.WEAPON_DETECTED, alertTypes: ['WEAPON_DETECTED'], emergency: true },
  { type: employeeBehaviorFolderType, title: 'Unusual employee behavior', alertTypes: [...employeeBehaviorTypes], emergency: false },
  { type: 'UNAUTHORIZED_STOCKROOM_ACCESS', title: alertTitles.UNAUTHORIZED_STOCKROOM_ACCESS, alertTypes: ['UNAUTHORIZED_STOCKROOM_ACCESS'], emergency: false },
  { type: 'HIGH_VALUE_ZONE_DWELL', title: alertTitles.HIGH_VALUE_ZONE_DWELL, alertTypes: ['HIGH_VALUE_ZONE_DWELL'], emergency: false },
  { type: 'SUSPICIOUS_PRODUCT_CONCEALMENT', title: alertTitles.SUSPICIOUS_PRODUCT_CONCEALMENT, alertTypes: ['SUSPICIOUS_PRODUCT_CONCEALMENT'], emergency: false },
  { type: 'REGULAR_SHOPLIFTER', title: alertTitles.REGULAR_SHOPLIFTER, alertTypes: ['REGULAR_SHOPLIFTER'], emergency: false },
];

function isDetectedToday(alert: Alert) {
  return new Date(alert.detectedAt).toDateString() === new Date().toDateString();
}

function imageForAlert(alert: Alert) {
  const value = alert.cameraId ?? alert.id;
  const seed = Array.from(value).reduce((total, character) => total + character.charCodeAt(0), 0);
  return contextImages[seed % contextImages.length];
}

interface OriginalAlertMedia {
  videoUrl: string;
  thumbnailUrl: string;
  personLabel: string;
}

function metadataString(alert: Alert, key: string) {
  const value = alert.metadata?.[key];
  return typeof value === 'string' ? value : null;
}

function originalAlertMedia(alert: Alert): OriginalAlertMedia | null {
  const isPresentationFixture = alert.metadata?.sampleOnly === true || alert.metadata?.sourceSystem === 'cctv-upload-fixture';
  if (!isPresentationFixture) return null;
  const presentationVideoUrl = metadataString(alert, 'presentationVideoUrl');
  const presentationThumbnailUrl = metadataString(alert, 'presentationThumbnailUrl');
  const presentationPersonLabel = metadataString(alert, 'presentationPersonLabel');
  if (presentationVideoUrl && presentationThumbnailUrl) {
    return {
      videoUrl: presentationVideoUrl,
      thumbnailUrl: presentationThumbnailUrl,
      personLabel: presentationPersonLabel ?? toTitle(alert.subjectPersonCategory),
    };
  }

  const storageSession = storageAlertSessionFor(alert.sourceEventId);
  if (alert.type === 'UNAUTHORIZED_STOCKROOM_ACCESS' && storageSession) {
    const assetBase = `/demo-source/alerts/storage/segments/storage-session-${storageSession.sessionId}-clip-01`;
    return {
      videoUrl: `${assetBase}.mp4`,
      thumbnailUrl: `${assetBase}.jpg`,
      personLabel: storageSession.personLabel,
    };
  }

  const variant = Array.from(alert.sourceEventId).reduce((total, character) => total + character.charCodeAt(0), 0);

  if (alert.type === 'UNAUTHORIZED_STOCKROOM_ACCESS') {
    return variant % 2 === 0
      ? {
          videoUrl: '/demo-source/alerts/storage/storage_1.mp4#t=180',
          thumbnailUrl: '/demo-source/alerts/storage/storage_1.jpg',
          personLabel: 'Unknown',
        }
      : {
          videoUrl: '/demo-source/alerts/storage/storage_2.mp4#t=60',
          thumbnailUrl: '/demo-source/alerts/storage/storage_2.jpg',
          personLabel: 'Employee',
        };
  }

  if (alert.type === 'CASH_DRAWER_WITHOUT_CUSTOMER' || alert.type === 'POS_VOID_OR_REFUND') {
    return {
      videoUrl: '/demo-source/alerts/counter-generated/cvlp_h3_00043_Left.mp4',
      thumbnailUrl: '/demo-source/alerts/counter-generated/cvlp_h3_00043_Left.jpg',
      personLabel: 'Employee',
    };
  }

  if (alert.type === 'SUSPICIOUS_CASH_HANDLING') {
    return variant % 2 === 0
      ? {
          videoUrl: '/demo-source/alerts/counter-generated/cvlp_h3_00013_Right.mp4',
          thumbnailUrl: '/demo-source/alerts/counter-generated/cvlp_h3_00013_Right.jpg',
          personLabel: 'Employee',
        }
      : {
          videoUrl: '/demo-source/alerts/counter-generated/cvlp_h3_00045_Left.mp4',
          thumbnailUrl: '/demo-source/alerts/counter-generated/cvlp_h3_00045_Left.jpg',
          personLabel: 'Employee',
        };
  }

  return null;
}

function metadataUrl(alert: Alert, key: 'thumbnailUrl' | 'videoUrl' | 'trackingUrl') {
  const originalMedia = originalAlertMedia(alert);
  if (originalMedia && key !== 'trackingUrl') return originalMedia[key];
  if (originalMedia && key === 'trackingUrl') return null;
  const value = alert.metadata?.[key];
  return typeof value === 'string' ? value : null;
}

function alertPersonLabel(alert: Alert) {
  return originalAlertMedia(alert)?.personLabel ?? toTitle(alert.subjectPersonCategory);
}

function hasBakedTracking(alert: Alert) {
  return originalAlertMedia(alert) !== null;
}

interface DetectionBoxValue {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface TrackedBox extends DetectionBoxValue {
  trackId: number;
  label: string;
  confidence: number;
}

interface TrackFrame {
  time: number;
  boxes: TrackedBox[];
}

interface TrackingPayload {
  fps: number;
  model: string;
  tracker: string;
  frames: TrackFrame[];
}

function detectionForAlert(alert: Alert) {
  const value = alert.metadata?.detectionBox;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const box: DetectionBoxValue = {
    x: Number(record.x),
    y: Number(record.y),
    width: Number(record.width),
    height: Number(record.height),
  };
  if (Object.values(box).some((coordinate) => !Number.isFinite(coordinate) || coordinate < 0 || coordinate > 1)) return null;
  const label = typeof alert.metadata?.detectionLabel === 'string' ? alert.metadata.detectionLabel : 'Person';
  return { box, label };
}

function DetectionOverlay({ alert }: { alert: Alert }) {
  const detection = detectionForAlert(alert);
  if (!detection) return null;
  const { box, label } = detection;
  return (
    <Box
      sx={{
        position: 'absolute',
        left: `${box.x * 100}%`,
        top: `${box.y * 100}%`,
        width: `${box.width * 100}%`,
        height: `${box.height * 100}%`,
        border: '2px solid #FF5C53',
        boxShadow: '0 0 0 1px rgba(0,0,0,.55), inset 0 0 18px rgba(255,92,83,.08)',
        pointerEvents: 'none',
      }}
    >
      <Typography sx={{ position: 'absolute', left: -2, top: -2, bgcolor: '#FF5C53', color: '#160604', px: 0.75, py: 0.25, fontSize: 9, fontWeight: 850, letterSpacing: '.08em', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>
        {label}
      </Typography>
    </Box>
  );
}

const trackColors = ['#FF5C53', '#F0C987', '#6BC5D2', '#9BD2AC', '#C19BFF'];

function TrackedBoxes({ boxes }: { boxes: TrackedBox[] }) {
  return boxes.map((box) => {
    const color = trackColors[Math.abs(box.trackId) % trackColors.length];
    return (
      <Box
        key={box.trackId}
        sx={{
          position: 'absolute',
          left: `${box.x * 100}%`,
          top: `${box.y * 100}%`,
          width: `${box.width * 100}%`,
          height: `${box.height * 100}%`,
          border: `2px solid ${color}`,
          boxShadow: '0 0 0 1px rgba(0,0,0,.58)',
          pointerEvents: 'none',
          transition: 'left 70ms linear, top 70ms linear, width 70ms linear, height 70ms linear',
        }}
      >
        <Typography sx={{ position: 'absolute', left: -2, top: -2, bgcolor: color, color: '#090B0C', px: 0.65, py: 0.2, fontSize: 8, fontWeight: 850, letterSpacing: '.05em', whiteSpace: 'nowrap' }}>
          {box.label} #{box.trackId} · {Math.round(box.confidence * 100)}%
        </Typography>
      </Box>
    );
  });
}

function TrackedAlertVideo({ alert, videoUrl, thumbnail }: { alert: Alert; videoUrl: string; thumbnail: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const lastFrameRef = useRef(-1);
  const [tracking, setTracking] = useState<TrackingPayload | null>(null);
  const [boxes, setBoxes] = useState<TrackedBox[]>([]);
  const trackingUrl = metadataUrl(alert, 'trackingUrl');

  useEffect(() => {
    if (!trackingUrl) return;
    const controller = new AbortController();
    void fetch(trackingUrl, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`Tracking data returned ${response.status}.`);
        return response.json() as Promise<TrackingPayload>;
      })
      .then(setTracking)
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return;
        setTracking(null);
      });
    return () => controller.abort();
  }, [trackingUrl]);

  useEffect(() => {
    if (!tracking || !videoRef.current) return;
    let animationFrame = 0;
    const update = () => {
      const video = videoRef.current;
      if (video && tracking.frames.length > 0) {
        const frameIndex = Math.min(tracking.frames.length - 1, Math.max(0, Math.round(video.currentTime * tracking.fps)));
        if (frameIndex !== lastFrameRef.current) {
          lastFrameRef.current = frameIndex;
          setBoxes(tracking.frames[frameIndex]?.boxes ?? []);
        }
      }
      animationFrame = requestAnimationFrame(update);
    };
    animationFrame = requestAnimationFrame(update);
    return () => cancelAnimationFrame(animationFrame);
  }, [tracking]);

  return (
    <Box sx={{ aspectRatio: '16 / 9', bgcolor: '#050606', overflow: 'hidden', position: 'relative' }}>
      <Box ref={videoRef} component="video" src={videoUrl} poster={thumbnail} controls playsInline sx={{ width: '100%', height: '100%', objectFit: 'contain' }} />
      {!hasBakedTracking(alert) && (tracking ? <TrackedBoxes boxes={boxes} /> : <DetectionOverlay alert={alert} />)}
      {tracking && (
        <Typography sx={{ position: 'absolute', right: 10, top: 10, bgcolor: 'rgba(5,6,6,.78)', color: '#A8DAB7', px: 0.8, py: 0.4, fontSize: 9, fontWeight: 750, letterSpacing: '.08em', pointerEvents: 'none' }}>
          {tracking.model} · {tracking.tracker}
        </Typography>
      )}
    </Box>
  );
}

function hasVideo(alert: Alert) {
  return Boolean(metadataUrl(alert, 'videoUrl')) || alert.hasVideoEvidence;
}

function isEmergency(alert: Alert) {
  return alert.type === 'WEAPON_DETECTED';
}

function situationTitle(type: string) {
  if (alertTitles[type]) return alertTitles[type];
  const title = alertTitle(type).toLocaleLowerCase();
  return title.charAt(0).toLocaleUpperCase() + title.slice(1);
}

function folderTitleForAlert(type: string) {
  return alertFolderDefinitions.find((folder) => folder.alertTypes.includes(type))?.title ?? situationTitle(type);
}

function sortActiveAlerts(items: Alert[]) {
  return [...items].sort((left, right) => {
    if (isEmergency(left) !== isEmergency(right)) return isEmergency(left) ? -1 : 1;
    return new Date(right.detectedAt).getTime() - new Date(left.detectedAt).getTime();
  });
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

export function AlertsPage() {
  const attentionSectionRef = useRef<HTMLDivElement>(null);
  const confirmedSectionRef = useRef<HTMLDivElement>(null);
  const { store, api, isDemo } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [confirmedAlerts, setConfirmedAlerts] = useState<Alert[]>([]);
  const [attentionPage, setAttentionPage] = useState(1);
  const [confirmedPage, setConfirmedPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reviewingId, setReviewingId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AlertDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [falseAlarmTarget, setFalseAlarmTarget] = useState<AlertDetail | null>(null);
  const openedAlertParamRef = useRef<string | null>(null);
  const requestedType = searchParams.get('type');
  const normalizedRequestedType = requestedType && employeeBehaviorTypes.includes(requestedType as (typeof employeeBehaviorTypes)[number])
    ? employeeBehaviorFolderType
    : requestedType;
  const selectedFolderDefinition = normalizedRequestedType
    ? alertFolderDefinitions.find((folder) => folder.type === normalizedRequestedType) ?? null
    : null;
  const selectedType = selectedFolderDefinition?.type ?? null;
  const selectedAlertTypes = selectedFolderDefinition?.alertTypes ?? null;
  const todayOnly = searchParams.get('period') === 'today';
  const requestedSection = searchParams.get('section');
  const activeSection: 'attention' | 'confirmed' = requestedSection === 'confirmed' ? 'confirmed' : 'attention';
  const requestedAlertId = (searchParams.get('alertId') ?? '').trim();

  const attachPlaybackUrl = useCallback(async (alert: AlertDetail) => {
    if (!store || metadataUrl(alert, 'videoUrl')) return alert;
    const videoEvidence = alert.evidence.find((evidence) => evidence.mimeType.startsWith('video/'));
    if (!videoEvidence) return alert;
    const playback = await api.getEvidencePlaybackUrl(store.id, alert.id, videoEvidence.id);
    return {
      ...alert,
      metadata: { ...(alert.metadata ?? {}), videoUrl: playback.url },
    };
  }, [api, store]);

  const loadAlerts = useCallback(async () => {
    if (!store) return;
    setLoading(true);
    setError(null);
    try {
      const [activeResult, confirmedResult] = await Promise.all([
        api.getAlerts(store.id, { status: 'NEW', limit: 100 }),
        api.getAlerts(store.id, { status: 'RESOLVED', limit: 100 }),
      ]);
      setAlerts(sortActiveAlerts(activeResult.items.filter(hasVideo)));
      setConfirmedAlerts(confirmedResult.items.filter(hasVideo));
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
    count: activeSection === 'attention'
      ? periodAlerts.filter((alert) => definition.alertTypes.includes(alert.type)).length
      : periodConfirmedAlerts.filter((alert) => definition.alertTypes.includes(alert.type)).length,
  }));
  const filteredAlerts = selectedAlertTypes
    ? periodAlerts.filter((alert) => selectedAlertTypes.includes(alert.type))
    : periodAlerts;
  const filteredConfirmedAlerts = selectedAlertTypes
    ? periodConfirmedAlerts.filter((alert) => selectedAlertTypes.includes(alert.type))
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

  const reviewAlert = async (alert: Alert, decision: ReviewDecision) => {
    if (isDemo || !store || reviewingId || isEmergency(alert)) return;
    setReviewingId(alert.id);
    setError(null);
    try {
      const actionAlertId = presentationBaseAlertId(alert);
      const presentationOnly = isPresentationOnlyAlert(alert);
      if (decision === 'match') {
        if (presentationOnly) setPresentationAlertDecision(alert, 'RESOLVED');
        else await api.actionAlert(store.id, actionAlertId, 'resolve', 'Owner review: confirmed as a real incident.');
        setConfirmedAlerts((current) => [{
          ...alert,
          status: 'RESOLVED',
          acknowledgedAt: new Date().toISOString(),
          resolutionNote: 'Owner review: confirmed as a real incident.',
        }, ...current.filter((item) => item.id !== alert.id)]);
      } else {
        if (presentationOnly) setPresentationAlertDecision(alert, 'DISMISSED');
        else await api.actionAlert(store.id, actionAlertId, 'dismiss', 'Owner review: marked as a false alarm.');
      }
      setAlerts((current) => current.filter((item) => presentationOnly ? item.id !== alert.id : presentationBaseAlertId(item) !== actionAlertId));
      if (detail?.id === alert.id) setDetail(null);
      window.dispatchEvent(new Event('alerts:changed'));
    } catch (cause) {
      setError(getErrorMessage(cause));
    } finally {
      setReviewingId(null);
    }
  };

  const openContext = useCallback(async (alert: Alert) => {
    if (!store) return;
    setDetailLoading(true);
    setDetail({ ...alert, acknowledgedById: null, acknowledgedByName: null, evidence: [] });
    if (isPresentationOnlyAlert(alert)) {
      setDetailLoading(false);
      return;
    }
    try {
      const apiDetail = await api.getAlert(store.id, presentationBaseAlertId(alert));
      const mergedDetail: AlertDetail = {
        ...apiDetail,
        ...alert,
        acknowledgedById: apiDetail.acknowledgedById,
        acknowledgedByName: apiDetail.acknowledgedByName,
        evidence: apiDetail.evidence,
      };
      setDetail(await attachPlaybackUrl(mergedDetail));
    } catch (cause) {
      setError(getErrorMessage(cause));
    } finally {
      setDetailLoading(false);
    }
  }, [api, attachPlaybackUrl, store]);

  useEffect(() => {
    if (!requestedAlertId) {
      openedAlertParamRef.current = null;
      return;
    }
    if (!store || loading || openedAlertParamRef.current === requestedAlertId) return;
    openedAlertParamRef.current = requestedAlertId;

    const listedAlert = [...alerts, ...confirmedAlerts].find((alert) => (
      alert.id === requestedAlertId || presentationBaseAlertId(alert) === requestedAlertId
    ));
    if (listedAlert) {
      void openContext(listedAlert);
      return;
    }

    setDetailLoading(true);
    setDetail(null);
    void api.getAlert(store.id, requestedAlertId)
      .then(attachPlaybackUrl)
      .then((alert) => setDetail(alert))
      .catch((cause) => setError(getErrorMessage(cause)))
      .finally(() => setDetailLoading(false));
  }, [alerts, api, attachPlaybackUrl, confirmedAlerts, loading, openContext, requestedAlertId, store]);

  const closeDetail = () => {
    setDetail(null);
    if (!requestedAlertId) return;
    const next = new URLSearchParams(searchParams);
    next.delete('alertId');
    setSearchParams(next, { replace: true });
  };

  const markConfirmedAlertAsFalseAlarm = async () => {
    if (isDemo || !falseAlarmTarget || !store || reviewingId) return;
    setReviewingId(falseAlarmTarget.id);
    setError(null);
    try {
      const isPresentationClip = typeof falseAlarmTarget.metadata?.presentationClipNumber === 'number';
      if (isPresentationOnlyAlert(falseAlarmTarget) || isPresentationClip) {
        hideConfirmedAlertLocally(falseAlarmTarget);
      } else {
        await api.actionAlert(store.id, presentationBaseAlertId(falseAlarmTarget), 'dismiss', 'Owner correction: confirmed by mistake and reclassified as a false alarm.');
      }
      setConfirmedAlerts((current) => current.filter((alert) => alert.id !== falseAlarmTarget.id));
      if (detail?.id === falseAlarmTarget.id) setDetail(null);
      setFalseAlarmTarget(null);
      window.dispatchEvent(new Event('alerts:changed'));
    } catch (cause) {
      setError(getErrorMessage(cause));
    } finally {
      setReviewingId(null);
    }
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
            Emergency alerts require immediate review. Suspicious behavior alerts can be confirmed or dismissed as false alarms.
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
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.7 }}>Total video alerts</Typography>
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
          {selectedType ? `${selectedFolderTitle} · ` : ''}{todayOnly ? 'Detected today · ' : ''}Only alerts with recorded video are shown.
        </Typography>
      {filteredAlerts.length === 0 ? (
        <EmptyState title="You are all caught up" description={`There are no new video alerts in ${selectedFolderTitle.toLocaleLowerCase()} to review.`} />
      ) : (
        <Stack spacing={1.5}>
          {visibleAlerts.map((alert) => {
            const isSaving = reviewingId === alert.id;
            const thumbnail = metadataUrl(alert, 'thumbnailUrl') ?? imageForAlert(alert);
            return (
              <Paper key={alert.id} className="alert-review-card" variant="outlined" sx={{ overflow: 'hidden', position: 'relative' }}>
                {isSaving && <LinearProgress sx={{ position: 'absolute', zIndex: 2, top: 0, left: 0, right: 0 }} />}
                <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '280px minmax(0, 1fr)' }, minHeight: 196 }}>
                  <Box
                    component="button"
                    type="button"
                    aria-label={`Play video for ${situationTitle(alert.type)}`}
                    onClick={() => void openContext(alert)}
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
                    <Box sx={{ position: 'relative', width: '100%', aspectRatio: '16 / 9', overflow: 'hidden' }}>
                      <Box className="alert-thumbnail" component="img" src={thumbnail} alt={`Video preview for ${situationTitle(alert.type)}`} sx={{ width: '100%', height: '100%', position: 'absolute', inset: 0, objectFit: 'cover', filter: 'brightness(.72) contrast(1.12)' }} />
                      {!hasBakedTracking(alert) && <DetectionOverlay alert={alert} />}
                    </Box>
                    <Box className="play-indicator" aria-hidden="true" sx={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', pointerEvents: 'none' }}>
                      <PlayCircleOutlineIcon sx={{ color: '#FFF', fontSize: 45, filter: 'drop-shadow(0 2px 10px rgba(0,0,0,.72))' }} />
                    </Box>
                  </Box>

                  <Box sx={{ p: { xs: 2.25, md: 3 }, minWidth: 0 }}>
                    <Stack direction="row" spacing={1.25} alignItems="center" useFlexGap flexWrap="wrap">
                      <Typography variant="overline" sx={{ color: isEmergency(alert) ? 'error.main' : 'primary.main', lineHeight: 1.4 }}>
                        {isEmergency(alert) ? 'Emergency review' : folderTitleForAlert(alert.type)}
                      </Typography>
                      <Typography aria-hidden="true" color="divider">·</Typography>
                      <Typography variant="body2" color="text.secondary">{formatDateTime(alert.detectedAt)}</Typography>
                    </Stack>
                    <Typography variant="h5" sx={{ mt: 1.5 }}>{situationTitle(alert.type)}</Typography>
                    <Typography color="text.secondary" sx={{ mt: 1, lineHeight: 1.65 }}>{alertDescriptions[alert.type] ?? 'The security system detected activity that needs a quick review.'}</Typography>
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2.5, mt: 2 }}>
                      <ContextValue label="Camera" value={alert.cameraName ?? 'Unknown camera'} />
                      <ContextValue label="Person" value={alertPersonLabel(alert)} />
                    </Box>
                    <Stack direction={{ xs: 'column', sm: 'row' }} alignItems={{ xs: 'stretch', sm: 'center' }} justifyContent="space-between" spacing={1.5} sx={{ mt: 2.5 }}>
                      <Typography variant="caption" color={isEmergency(alert) ? 'error.main' : 'text.secondary'} sx={{ maxWidth: 420, lineHeight: 1.55 }}>
                        {isEmergency(alert)
                          ? 'Immediate review required. Follow the store emergency procedure after reviewing the footage.'
                          : 'Open the footage before confirming or dismissing this alert.'}
                      </Typography>
                      <Button
                        variant={isEmergency(alert) ? 'contained' : 'outlined'}
                        color={isEmergency(alert) ? 'error' : 'primary'}
                        startIcon={<PlayCircleOutlineIcon />}
                        onClick={() => void openContext(alert)}
                        sx={{ flexShrink: 0, alignSelf: { xs: 'flex-start', sm: 'center' } }}
                      >
                        {isEmergency(alert) ? 'Review now' : 'Review video'}
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
              <ConfirmedAlertCard key={alert.id} alert={alert} onOpen={openContext} />
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

      <Dialog open={Boolean(detail)} onClose={() => !detailLoading && closeDetail()} fullWidth maxWidth="md">
        <DialogTitle sx={{ pr: 7 }}>
          {detail ? folderTitleForAlert(detail.type) : 'Alert context'}
          <IconButton aria-label="Close" onClick={closeDetail} sx={{ position: 'absolute', right: 12, top: 10 }}><CloseIcon /></IconButton>
        </DialogTitle>
        <DialogContent>
          {detailLoading && <Box sx={{ display: 'grid', placeItems: 'center', minHeight: 260 }}><CircularProgress /></Box>}
          {!detailLoading && detail && (
            <AlertContext
              alert={detail}
              readOnly={isDemo}
              reviewing={reviewingId === detail.id}
              onReview={(decision) => reviewAlert(detail, decision)}
              onReclassify={() => setFalseAlarmTarget(detail)}
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
            This video will be removed from Confirmed alerts and treated as a false alarm. This action only changes the review classification.
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

function ConfirmedAlertCard({ alert, onOpen }: { alert: Alert; onOpen: (alert: Alert) => void | Promise<void> }) {
  const thumbnail = metadataUrl(alert, 'thumbnailUrl') ?? imageForAlert(alert);
  return (
    <Paper className="alert-review-card" variant="outlined" sx={{ overflow: 'hidden' }}>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '280px minmax(0, 1fr)' }, minHeight: 190 }}>
        <Box
          component="button"
          type="button"
          aria-label={`Play video for ${situationTitle(alert.type)}`}
          onClick={() => void onOpen(alert)}
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
            '&:hover .alert-thumbnail': { transform: 'scale(1.018)', filter: 'brightness(.58) contrast(1.1)' },
            '&:hover .play-indicator, &:focus-visible .play-indicator': { opacity: 1, transform: 'scale(1)' },
            '&:focus-visible': { outline: '2px solid #78B990', outlineOffset: -2 },
          }}
        >
          <Box sx={{ position: 'relative', width: '100%', aspectRatio: '16 / 9', overflow: 'hidden' }}>
            <Box className="alert-thumbnail" component="img" src={thumbnail} alt={`Video preview for ${situationTitle(alert.type)}`} sx={{ width: '100%', height: '100%', position: 'absolute', inset: 0, objectFit: 'cover', filter: 'brightness(.66) contrast(1.08)' }} />
            {!hasBakedTracking(alert) && <DetectionOverlay alert={alert} />}
          </Box>
          <Box className="play-indicator" aria-hidden="true" sx={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', pointerEvents: 'none' }}>
            <PlayCircleOutlineIcon sx={{ color: '#FFF', fontSize: 45, filter: 'drop-shadow(0 2px 10px rgba(0,0,0,.72))' }} />
          </Box>
        </Box>
        <Box sx={{ p: { xs: 2.25, md: 3 }, minWidth: 0 }}>
          <Stack direction="row" spacing={1.25} alignItems="center" useFlexGap flexWrap="wrap">
            <Typography variant="overline" color="success.main" sx={{ lineHeight: 1.4 }}>Confirmed incident</Typography>
            <Typography aria-hidden="true" color="divider">·</Typography>
            <Typography variant="body2" color="text.secondary">{folderTitleForAlert(alert.type)}</Typography>
            <Typography aria-hidden="true" color="divider">·</Typography>
            <Typography variant="body2" color="text.secondary">{formatDateTime(alert.detectedAt)}</Typography>
          </Stack>
          <Typography variant="h6" sx={{ mt: 1.5 }}>{situationTitle(alert.type)}</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.75, lineHeight: 1.65 }}>{alertDescriptions[alert.type] ?? 'Confirmed security incident.'}</Typography>
          <Stack direction={{ xs: 'column', sm: 'row' }} alignItems={{ xs: 'stretch', sm: 'end' }} justifyContent="space-between" gap={2} sx={{ mt: 2 }}>
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2.5 }}>
              <ContextValue label="Camera" value={alert.cameraName ?? 'Unknown camera'} />
              <ContextValue label="Person" value={alertPersonLabel(alert)} />
            </Box>
            <Button variant="text" startIcon={<PlayCircleOutlineIcon />} onClick={() => void onOpen(alert)} sx={{ flexShrink: 0, alignSelf: { xs: 'flex-start', sm: 'end' } }}>
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

function AlertContext({ alert, readOnly, reviewing, onReview, onReclassify }: { alert: AlertDetail; readOnly: boolean; reviewing: boolean; onReview: (decision: ReviewDecision) => void | Promise<void>; onReclassify: () => void }) {
  const videoUrl = metadataUrl(alert, 'videoUrl');
  const thumbnail = metadataUrl(alert, 'thumbnailUrl') ?? imageForAlert(alert);
  const isConfirmed = alert.status === 'RESOLVED';

  return (
    <Stack spacing={2.5}>
      {videoUrl ? (
        <TrackedAlertVideo alert={alert} videoUrl={videoUrl} thumbnail={thumbnail} />
      ) : (
        <Box sx={{ aspectRatio: '16 / 9', bgcolor: '#050606', overflow: 'hidden', position: 'relative' }}>
          <Box component="img" src={thumbnail} alt="Alert context" sx={{ width: '100%', height: '100%', objectFit: 'cover', filter: 'brightness(.78) contrast(1.1)' }} />
          {!hasBakedTracking(alert) && <DetectionOverlay alert={alert} />}
        </Box>
      )}
      {!videoUrl && <MuiAlert severity="info">A video link has not been attached to this alert yet. The available camera snapshot is shown instead.</MuiAlert>}
      {readOnly && <MuiAlert severity="info">Read-only demo. Videos are available to view; sign in as an owner to manage real incidents.</MuiAlert>}
      <Typography color="text.secondary" sx={{ lineHeight: 1.7 }}>{alertDescriptions[alert.type] ?? 'Review the recorded context before making a decision.'}</Typography>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 1fr', sm: readOnly || isEmergency(alert) ? '1fr 1fr' : 'minmax(150px, .8fr) minmax(130px, .65fr) minmax(310px, 1.55fr)' }, gap: 2, alignItems: 'end' }}>
        <ContextValue label="Detected" value={formatDateTime(alert.detectedAt)} />
        <ContextValue label="Camera" value={alert.cameraName ?? 'Unknown'} />
        {!readOnly && !isEmergency(alert) && !isConfirmed && (
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ gridColumn: { xs: '1 / -1', sm: 'auto' }, justifySelf: { sm: 'end' }, width: { xs: '100%', sm: 'auto' } }}>
            <Button
              variant="contained"
              color="success"
              startIcon={<CheckCircleOutlineIcon />}
              disabled={reviewing}
              onClick={() => void onReview('match')}
              sx={{ minWidth: 160 }}
            >
              Match · Real incident
            </Button>
            <Button
              variant="outlined"
              startIcon={<CloseIcon />}
              disabled={reviewing}
              onClick={() => void onReview('false-alarm')}
              sx={{ minWidth: 132 }}
            >
              False alarm
            </Button>
          </Stack>
        )}
        {!readOnly && isConfirmed && (
          <Button
            variant="text"
            color="inherit"
            onClick={onReclassify}
            sx={{ gridColumn: { xs: '1 / -1', sm: 'auto' }, justifySelf: 'end', alignSelf: 'end', minWidth: 0, p: 0.25, color: 'text.secondary', fontSize: 13, fontWeight: 500, textDecoration: 'underline', textUnderlineOffset: 3, '&:hover': { color: 'error.main', bgcolor: 'transparent', textDecoration: 'underline' } }}
          >
            Confirmed by mistake?
          </Button>
        )}
      </Box>
    </Stack>
  );
}
