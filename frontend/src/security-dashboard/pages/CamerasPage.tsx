import ChevronLeftRoundedIcon from '@mui/icons-material/ChevronLeftRounded';
import ChevronRightRoundedIcon from '@mui/icons-material/ChevronRightRounded';
import CloseIcon from '@mui/icons-material/Close';
import FullscreenExitIcon from '@mui/icons-material/FullscreenExit';
import FullscreenIcon from '@mui/icons-material/Fullscreen';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import OpenInFullIcon from '@mui/icons-material/OpenInFull';
import RefreshIcon from '@mui/icons-material/Refresh';
import SearchIcon from '@mui/icons-material/Search';
import VideocamOffOutlinedIcon from '@mui/icons-material/VideocamOffOutlined';
import { Box, Button, Card, CardActionArea, Chip, Dialog, FormControl, FormControlLabel, IconButton, InputAdornment, MenuItem, Paper, Select, Stack, Switch, TextField, ToggleButton, ToggleButtonGroup, Typography, useMediaQuery } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { useQueries, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { CameraEmptyState, InlineError, ScreenLoader, cameraAreaLabel, cameraCoverage, formatAgo, formatDateTime } from '../components/common';
import { getErrorMessage } from '../lib/api';
import { useCameras } from '../lib/queries';
import type { Camera, CameraFeed, CameraZone } from '../lib/types';

interface FeedCard {
  camera: Camera;
  feed: CameraFeed | null;
  area: string;
  coverage: string;
  channelNumber: number;
}

type StatusFilter = 'ALL' | 'LIVE' | 'OFFLINE';

interface CameraPreferences {
  search: string;
  status: StatusFilter;
  area: string;
}

const preferencesKey = 'liquor-store.camera-filters';
const scrollKey = 'liquor-store.camera-scroll';
const defaultPreferences: CameraPreferences = { search: '', status: 'ALL', area: 'ALL' };

function restorePreferences(): CameraPreferences {
  try {
    const stored = sessionStorage.getItem(preferencesKey);
    if (!stored) return defaultPreferences;
    const value = JSON.parse(stored) as Partial<CameraPreferences>;
    return {
      search: typeof value.search === 'string' ? value.search : '',
      status: value.status === 'LIVE' || value.status === 'OFFLINE' ? value.status : 'ALL',
      area: typeof value.area === 'string' ? value.area : 'ALL',
    };
  } catch {
    return defaultPreferences;
  }
}

const overlayButton = { color: '#FFF', bgcolor: 'rgba(0,0,0,.5)', '&:hover': { bgcolor: 'rgba(0,0,0,.78)' } } as const;

const zoneColors = ['#F2B84B', '#6FC3DF', '#E57373', '#9CCC65', '#BA68C8'];

// Zones are fractions of the frame, so they overlay any rendering size.
function ZoneOverlay({ zones }: { zones: CameraZone[] }) {
  return (
    <Box component="svg" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden sx={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
      {zones.map((zone, index) => {
        const color = zoneColors[index % zoneColors.length];
        const points = zone.pointsNorm.map(([x, y]) => `${x},${y}`).join(' ');
        const key = `${zone.pipelineId}:${zone.name}:${index}`;
        // The counter pipeline also defines lines (e.g. the split between checkouts).
        return zone.type === 'line'
          ? <polyline key={key} points={points} fill="none" stroke={color} strokeWidth={2} strokeDasharray="6 4" vectorEffect="non-scaling-stroke" />
          : <polygon key={key} points={points} fill={color} fillOpacity={0.14} stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" />;
      })}
    </Box>
  );
}

const cameraStatusBadge: Record<string, string> = {
  online: 'ONLINE',
  offline: 'NO SIGNAL',
  unknown: 'NOT CONNECTED',
  inactive: 'TURNED OFF',
};

function feedLabel(feed: CameraFeed | null, short: boolean) {
  if (!feed) return short ? 'ACTIVE' : 'NO LIVE VIEW';
  if (feed.kind !== 'hls') return short ? 'LOOP' : 'RECORDED LOOP';
  // Test mode: MediaMTX is replaying the store's footage, analysed live by the AI.
  if (feed.isRecording) return short ? 'TEST' : 'LIVE · TEST MODE (LOOPED FOOTAGE)';
  return 'LIVE';
}

// Plays a feed: a plain file, or an HLS playlist -- natively where the
// browser can (Safari), otherwise through hls.js, loaded only when needed.
function FeedVideo({ feed, onSize, onError }: {
  feed: CameraFeed;
  onSize: (size: { width: number; height: number }) => void;
  onError: (message: string | null) => void;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    let destroyed = false;
    let retryTimer: number | undefined;
    let cleanup = () => {};
    if (feed.kind === 'hls' && !video.canPlayType('application/vnd.apple.mpegurl')) {
      void import('hls.js').then(({ default: Hls }) => {
        if (destroyed) return;
        if (!Hls.isSupported()) {
          onError('This browser cannot play live video.');
          return;
        }
        // No worker: the CSP allows scripts from this origin only, not blob: workers.
        const hls = new Hls({ enableWorker: false, lowLatencyMode: true, liveSyncDurationCount: 2 });
        hls.on(Hls.Events.ERROR, (_event, data) => {
          if (!data.fatal) return;
          if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
            onError('Live stream unavailable. Retrying…');
            // Cleared on close: a retry must not restart a destroyed player.
            window.clearTimeout(retryTimer);
            retryTimer = window.setTimeout(() => { if (!destroyed) hls.startLoad(); }, 3000);
          } else {
            hls.recoverMediaError();
          }
        });
        hls.on(Hls.Events.FRAG_LOADED, () => onError(null));
        hls.loadSource(feed.videoUrl);
        hls.attachMedia(video);
        cleanup = () => hls.destroy();
      });
    } else {
      video.src = feed.videoUrl;
    }
    return () => { destroyed = true; window.clearTimeout(retryTimer); cleanup(); };
  }, [feed.kind, feed.videoUrl, onError]);
  return (
    <Box
      component="video"
      ref={ref}
      poster={feed.posterUrl ?? undefined}
      autoPlay
      muted
      loop={feed.kind !== 'hls'}
      playsInline
      controls={false}
      disablePictureInPicture
      onLoadedMetadata={(event) => {
        const { videoWidth, videoHeight } = event.currentTarget;
        if (videoWidth && videoHeight) onSize({ width: videoWidth, height: videoHeight });
      }}
      onContextMenu={(event) => event.preventDefault()}
      sx={{ display: 'block', width: '100%', height: '100%', objectFit: 'fill', bgcolor: '#000' }}
    />
  );
}

function statusHeadline(camera: Camera) {
  if (camera.status === 'inactive') return 'CAMERA TURNED OFF';
  if (camera.status === 'unknown') return 'NEVER CONNECTED';
  const ago = formatAgo(camera.lastFrameAt);
  return ago ? `NO SIGNAL · LAST FRAME ${ago.toUpperCase()}` : 'NO SIGNAL';
}

// What the owner is told when there is no picture: the real state, never
// reassurance that the camera is being watched when it is not.
function cameraNotice(camera: Camera): { title: string; detail: string } {
  switch (camera.status) {
    case 'online':
      return {
        title: 'Live view is not set up on this server.',
        detail: 'The AI is receiving and analysing this camera. Its alerts and clips appear under Alerts.',
      };
    case 'offline': {
      const ago = formatAgo(camera.lastFrameAt);
      return {
        title: ago ? `No signal from this camera since ${formatDateTime(camera.lastFrameAt)} (${ago}).` : 'No signal from this camera.',
        detail: 'The AI is not receiving its video, so nothing it sees raises an alert. Check the camera\'s power and network cable, and that the AI server is running.',
      };
    }
    case 'unknown':
      return {
        title: 'This camera has never sent video to the AI.',
        detail: 'Nothing it sees raises an alert yet. Check that the AI server has been started with this store\'s camera list and that the camera address is correct.',
      };
    default:
      return {
        title: 'This camera is turned off.',
        detail: 'It is disabled in the store\'s configuration and is not being analysed.',
      };
  }
}

export function CamerasPage() {
  const restoredScrollRef = useRef(false);
  const { store, api } = useAuth();
  const [searchParams] = useSearchParams();
  const [preferences, setPreferences] = useState<CameraPreferences>(() => {
    const restored = restorePreferences();
    const requestedStatus = searchParams.get('status');
    return {
      ...restored,
      status: requestedStatus === 'LIVE' || requestedStatus === 'OFFLINE' ? requestedStatus : restored.status,
    };
  });
  const [selectedFeedId, setSelectedFeedId] = useState<string | null>(null);

  const {
    data: cameras = [],
    error,
    isLoading,
    isFetching,
    refetch,
  } = useCameras();

  const feedQueries = useQueries({
    queries: cameras.map((camera) => ({
      queryKey: ['camera-feed', store?.id, camera.id],
      queryFn: () => api.getCameraFeed(store!.id, camera.id),
      enabled: Boolean(store),
      staleTime: 5 * 60_000,
    })),
  });

  const feeds = useMemo<FeedCard[]>(() => cameras.map((camera, index) => ({
    camera,
    feed: feedQueries[index]?.data ?? null,
    area: cameraAreaLabel(camera.cameraType),
    coverage: cameraCoverage(camera.pipelines),
    channelNumber: index + 1,
  })), [cameras, feedQueries]);

  const [showZones, setShowZones] = useState(true);
  const theme = useTheme();
  // A phone held sideways is wide but short: it gets the full screen too.
  const phone = useMediaQuery(`(max-width: ${theme.breakpoints.values.sm - 0.05}px), (max-height: 500px)`);
  const viewerRef = useRef<HTMLDivElement>(null);
  const touchRef = useRef<{ x: number; y: number } | null>(null);
  // iPhone Safari has no element full screen; the viewer is already full
  // screen there, so the button only shows where the browser supports it.
  const fullscreenSupported = typeof document !== 'undefined' && Boolean(document.fullscreenEnabled);
  const [isFullscreen, setIsFullscreen] = useState(false);
  useEffect(() => {
    const onChange = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);
  const onlineCount = feeds.filter(({ camera }) => camera.status === 'online').length;
  const areas = useMemo<string[]>(() => Array.from(new Set<string>(feeds.map(({ area }) => area))).sort((left, right) => left.localeCompare(right)), [feeds]);
  const selectedArea = preferences.area === 'ALL' || areas.includes(preferences.area) ? preferences.area : 'ALL';

  const scopedFeeds = useMemo(() => {
    const search = preferences.search.trim().toLocaleLowerCase();
    return feeds.filter(({ camera, area, coverage }) => {
      if (selectedArea !== 'ALL' && area !== selectedArea) return false;
      if (search && !`${camera.name} ${coverage} ${area}`.toLocaleLowerCase().includes(search)) return false;
      return true;
    });
  }, [feeds, preferences.search, selectedArea]);

  const scopedOnlineCount = scopedFeeds.filter(({ camera }) => camera.status === 'online').length;
  const scopedOfflineCount = scopedFeeds.length - scopedOnlineCount;
  const filteredFeeds = useMemo(() => scopedFeeds.filter(({ camera }) => {
    const online = camera.status === 'online';
    if (preferences.status === 'LIVE') return online;
    if (preferences.status === 'OFFLINE') return !online;
    return true;
  }), [preferences.status, scopedFeeds]);

  const selectedFeed = useMemo(() => feeds.find(({ camera }) => camera.id === selectedFeedId) ?? null, [feeds, selectedFeedId]);
  const selectedFeedOnline = Boolean(selectedFeed?.camera.isActive);
  // The frame's shape: from the feed when known (demo), else from the video
  // once its first frame decodes (live HLS).
  const [videoSize, setVideoSize] = useState<{ width: number; height: number } | null>(null);
  const [playError, setPlayError] = useState<string | null>(null);
  useEffect(() => { setVideoSize(null); setPlayError(null); }, [selectedFeedId]);
  const frame = {
    width: selectedFeed?.feed?.width ?? videoSize?.width ?? 16,
    height: selectedFeed?.feed?.height ?? videoSize?.height ?? 9,
  };
  const { data: selectedZones = [] } = useQuery({
    queryKey: ['zones', store?.id, selectedFeedId],
    queryFn: () => api.getZones(store!.id, selectedFeedId!),
    enabled: Boolean(store && selectedFeedId),
    staleTime: 5 * 60_000,
  });

  useEffect(() => {
    sessionStorage.setItem(preferencesKey, JSON.stringify(preferences));
  }, [preferences]);

  useEffect(() => {
    if (isLoading || restoredScrollRef.current) return;
    restoredScrollRef.current = true;
    const savedPosition = Number(sessionStorage.getItem(scrollKey));
    if (Number.isFinite(savedPosition) && savedPosition > 0) requestAnimationFrame(() => window.scrollTo({ top: savedPosition }));
  }, [isLoading]);

  useEffect(() => {
    const saveScrollPosition = () => sessionStorage.setItem(scrollKey, String(window.scrollY));
    window.addEventListener('scroll', saveScrollPosition, { passive: true });
    return () => window.removeEventListener('scroll', saveScrollPosition);
  }, []);

  // Stepping through cameras in the viewer follows the list as filtered.
  const selectedIndex = filteredFeeds.findIndex(({ camera }) => camera.id === selectedFeedId);
  const stepCamera = (offset: number) => {
    if (filteredFeeds.length < 2) return;
    const from = selectedIndex < 0 ? 0 : selectedIndex;
    const next = filteredFeeds[(from + offset + filteredFeeds.length) % filteredFeeds.length];
    setSelectedFeedId(next.camera.id);
  };
  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void viewerRef.current?.requestFullscreen?.();
  };

  if (!store || isLoading) return <ScreenLoader label="Loading cameras…" />;

  return (
    <Stack spacing={3} sx={{ overflowX: 'hidden' }}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'end', gap: 2 }}>
        <Box sx={{ maxWidth: 760 }}>
          <Typography component="h1" variant="h4">Cameras</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.75 }}>
            Select a camera to open its feed and the zones its AI pipelines watch.
          </Typography>
        </Box>
        <Chip
          color={onlineCount === feeds.length && feeds.length > 0 ? 'success' : 'warning'}
          label={`${onlineCount} of ${feeds.length} online`}
        />
      </Box>

      {error && <InlineError message={getErrorMessage(error)} />}

      <Paper
        variant="outlined"
        sx={{
          position: 'sticky',
          top: { xs: 66, sm: 12 },
          zIndex: 5,
          p: { xs: 1.5, sm: 2 },
          bgcolor: 'background.paper',
          backdropFilter: 'blur(16px)',
          borderColor: 'divider',
        }}
      >
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'minmax(260px, 1.5fr) minmax(160px, .7fr) auto' }, gap: 1.25 }}>
          <TextField
            size="small"
            value={preferences.search}
            onChange={(event) => setPreferences((current) => ({ ...current, search: event.target.value }))}
            placeholder="Search cameras"
            aria-label="Search cameras"
            InputProps={{ startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> }}
          />
          <FormControl size="small">
            <Select
              value={selectedArea}
              inputProps={{ 'aria-label': 'Area' }}
              onChange={(event) => setPreferences((current) => ({ ...current, area: event.target.value }))}
              // Same pattern as the Case filter on Alerts: the label sits inside the box.
              renderValue={(value) => (
                <Box component="span" sx={{ display: 'flex', gap: 0.75, minWidth: 0 }}>
                  <Box component="span" sx={{ color: 'text.secondary' }}>Area</Box>
                  <Box component="span" sx={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis' }}>{value === 'ALL' ? 'All areas' : value}</Box>
                </Box>
              )}
              sx={{ fontSize: 14 }}
            >
              <MenuItem value="ALL">All areas</MenuItem>
              {areas.map((area) => <MenuItem key={area} value={area}>{area}</MenuItem>)}
            </Select>
          </FormControl>
          <Button
            variant="outlined"
            startIcon={<RefreshIcon />}
            disabled={isFetching}
            onClick={() => void refetch()}
            sx={{ whiteSpace: 'nowrap' }}
          >
            {isFetching ? 'Updating' : 'Refresh'}
          </Button>
        </Box>

        <Box sx={{ mt: 1.5, display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 1.25 }}>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={preferences.status}
            onChange={(_, value: StatusFilter | null) => value && setPreferences((current) => ({ ...current, status: value }))}
            aria-label="Camera status"
          >
            <ToggleButton value="ALL">All {scopedFeeds.length}</ToggleButton>
            <ToggleButton value="LIVE">Online {scopedOnlineCount}</ToggleButton>
            <ToggleButton value="OFFLINE">No signal {scopedOfflineCount}</ToggleButton>
          </ToggleButtonGroup>
          <Typography variant="caption" color="text.secondary">Showing {filteredFeeds.length} of {scopedFeeds.length} cameras</Typography>
        </Box>
      </Paper>

      {feeds.length === 0 ? <CameraEmptyState /> : filteredFeeds.length === 0 ? (
        <Paper variant="outlined" sx={{ py: 6, px: 3, textAlign: 'center', borderStyle: 'dashed' }}>
          <Typography variant="h6">No cameras match these filters</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.75 }}>Try another search or clear the current camera filters.</Typography>
          <Button variant="outlined" sx={{ mt: 2 }} onClick={() => setPreferences(defaultPreferences)}>Clear filters</Button>
        </Paper>
      ) : (
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, minmax(0, 1fr))', lg: 'repeat(3, minmax(0, 1fr))', xl: 'repeat(4, minmax(0, 1fr))' }, gridAutoFlow: 'dense', gap: 1.25 }}>
          {filteredFeeds.map((feed) => {
            // Pictures stay viewable while the AI is down; the badge says why.
            const online = feed.camera.status === 'online';
            return (
              <Card key={feed.camera.id} className="live-feed" sx={{ overflow: 'hidden', '&:hover': { borderColor: 'primary.main' }, transition: 'border-color 160ms ease' }}>
                <CardActionArea
                  onClick={() => setSelectedFeedId(feed.camera.id)}
                  sx={{ touchAction: 'pan-y', '&:hover': { bgcolor: 'rgba(216,163,93,.025)' } }}
                >
                  <Box sx={{ position: 'relative', aspectRatio: '16 / 9', bgcolor: '#050606', overflow: 'hidden' }}>
                    {feed.feed?.posterUrl ? (
                      <Box component="img" src={feed.feed.posterUrl} alt={`${feed.camera.name} camera preview`} loading="lazy" decoding="async" sx={{ width: '100%', height: '100%', objectFit: 'cover', filter: online ? 'brightness(.86) contrast(1.1)' : 'grayscale(1) brightness(.42)' }} />
                    ) : (
                      <Box sx={{ width: '100%', height: '100%', display: 'grid', placeItems: 'center', color: '#5F6664' }}>
                        <VideocamOffOutlinedIcon sx={{ fontSize: 34 }} />
                      </Box>
                    )}
                    <Box sx={{ position: 'absolute', top: 10, left: 10, display: 'flex', alignItems: 'center', gap: 0.6, bgcolor: 'rgba(9,11,12,.82)', color: online ? '#9BD2AC' : '#C6C9C7', px: 1, py: 0.45, borderRadius: 0.5, fontSize: 12, fontWeight: 700, letterSpacing: '0.08em' }}>
                      <FiberManualRecordIcon sx={{ fontSize: 9 }} />
                      {online ? feedLabel(feed.feed, true) : cameraStatusBadge[feed.camera.status]}
                    </Box>
                    <Box sx={{ position: 'absolute', right: 10, bottom: 9, display: 'flex', alignItems: 'center', gap: 0.65, px: 0.8, py: 0.35, bgcolor: 'rgba(9,11,12,.78)', color: '#D5D8D6', fontSize: 12, fontWeight: 600, letterSpacing: '0.08em', fontVariantNumeric: 'tabular-nums' }}>
                      <OpenInFullIcon sx={{ fontSize: 12 }} />
                      CAM {String(feed.channelNumber).padStart(2, '0')}
                    </Box>
                  </Box>
                  <Box sx={{ px: 1.75, py: 1.4, textAlign: 'left' }}>
                    <Typography fontWeight={600} noWrap>{feed.camera.name}</Typography>
                    <Typography variant="caption" color="text.secondary" noWrap display="block" sx={{ mt: 0.25 }}>{feed.area} · {feed.coverage}</Typography>
                  </Box>
                </CardActionArea>
              </Card>
            );
          })}
        </Box>
      )}

      <Dialog
        open={Boolean(selectedFeed)}
        onClose={() => setSelectedFeedId(null)}
        maxWidth="lg"
        fullWidth
        // Phones get the whole screen (and the whole width when turned sideways).
        fullScreen={phone}
        PaperProps={{
          ref: viewerRef,
          onKeyDown: (event: React.KeyboardEvent) => {
            if (event.key === 'ArrowRight') { event.preventDefault(); stepCamera(1); }
            if (event.key === 'ArrowLeft') { event.preventDefault(); stepCamera(-1); }
          },
          // MUI forwards a Dialog-level aria-label to the modal root, not to the
          // element that carries role="dialog", so it is set on the paper here.
          'aria-label': selectedFeed ? `Camera view for ${selectedFeed.camera.name}` : undefined,
          sx: { overflow: 'hidden', bgcolor: '#050606', backgroundImage: 'none', border: phone ? 0 : '1px solid #303735', display: 'flex', flexDirection: 'column', '&:fullscreen': { maxWidth: 'none', maxHeight: 'none', m: 0, borderRadius: 0 } },
        }}
      >
        {selectedFeed && (
          <Box
            onTouchStart={(event) => { const t = event.touches[0]; touchRef.current = { x: t.clientX, y: t.clientY }; }}
            onTouchEnd={(event) => {
              // A horizontal swipe moves to the next or previous camera.
              const start = touchRef.current; touchRef.current = null;
              if (!start) return;
              const t = event.changedTouches[0];
              const dx = t.clientX - start.x; const dy = t.clientY - start.y;
              if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) stepCamera(dx < 0 ? 1 : -1);
            }}
            sx={{
              position: 'relative', bgcolor: '#020303', width: '100%',
              ...(phone || isFullscreen
                ? { flex: 1, minHeight: 0 }
                : { aspectRatio: '16 / 9', maxHeight: 'calc(100dvh - 170px)' }),
            }}
          >
            {selectedFeedOnline && selectedFeed.feed ? (
              /* The frame keeps the video's own aspect ratio, so the zones --
                 fractions of the frame -- land on the picture exactly. */
              <Box sx={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', containerType: 'size' }}>
                <Box
                  sx={{
                    position: 'relative',
                    aspectRatio: `${frame.width} / ${frame.height}`,
                    // Largest frame of the video's shape that fits the 16:9 stage.
                    width: `min(100cqw, 100cqh * ${frame.width} / ${frame.height})`,
                  }}
                >
                  <FeedVideo key={selectedFeed.camera.id} feed={selectedFeed.feed} onSize={setVideoSize} onError={setPlayError} />
                  {showZones && selectedZones.length > 0 && <ZoneOverlay zones={selectedZones} />}
                </Box>
                {playError && (
                  <Typography sx={{ position: 'absolute', bottom: 14, left: 18, right: 18, color: '#E6B3AE', fontSize: 14 }}>{playError}</Typography>
                )}
              </Box>
            ) : (
              <Box sx={{ width: '100%', height: '100%', display: 'grid', placeItems: 'center', color: '#8A918F', textAlign: 'center', px: 3 }}>
                <Box>
                  <VideocamOffOutlinedIcon sx={{ fontSize: 44 }} />
                  <Typography sx={{ mt: 1, color: '#E4E7E5', fontWeight: 600 }}>{cameraNotice(selectedFeed.camera).title}</Typography>
                  <Typography variant="body2" sx={{ mt: 0.75, color: '#AEB5B2', maxWidth: 520, mx: 'auto', lineHeight: 1.6 }}>
                    {cameraNotice(selectedFeed.camera).detail}
                  </Typography>
                </Box>
              </Box>
            )}
            <Box sx={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: 'linear-gradient(180deg, rgba(0,0,0,.52), transparent 22%)' }} />
            <Box sx={{ position: 'absolute', top: 16, left: 18, right: 64, display: 'flex', alignItems: 'center', gap: 0.8, color: selectedFeed.camera.status === 'online' ? '#A8DAB7' : '#F0B4A8', fontSize: 12, fontWeight: 700, letterSpacing: '0.08em' }}>
              <FiberManualRecordIcon sx={{ fontSize: 10 }} />
              <Box component="span" sx={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {selectedFeed.camera.status === 'online' ? feedLabel(selectedFeed.feed, false) : statusHeadline(selectedFeed.camera)}
              </Box>
            </Box>
            <Box sx={{ position: 'absolute', top: 6, right: 8, display: 'flex', gap: 0.5 }}>
              {fullscreenSupported && (
                <IconButton onClick={toggleFullscreen} aria-label={isFullscreen ? 'Exit full screen' : 'Full screen'} sx={overlayButton}>
                  {isFullscreen ? <FullscreenExitIcon /> : <FullscreenIcon />}
                </IconButton>
              )}
              <IconButton onClick={() => setSelectedFeedId(null)} aria-label="Close live view" sx={overlayButton}>
                <CloseIcon />
              </IconButton>
            </Box>
            {filteredFeeds.length > 1 && (
              <>
                <IconButton onClick={() => stepCamera(-1)} aria-label="Previous camera" sx={{ ...overlayButton, position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)' }}>
                  <ChevronLeftRoundedIcon />
                </IconButton>
                <IconButton onClick={() => stepCamera(1)} aria-label="Next camera" sx={{ ...overlayButton, position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)' }}>
                  <ChevronRightRoundedIcon />
                </IconButton>
              </>
            )}
          </Box>
        )}
        {selectedFeed && (
          /* The recorded footage already burns the camera name into the
             bottom-left corner, so the caption sits under the frame instead of
             on top of it, and repeats only what the footage does not carry. */
          <Box sx={{ px: 2.25, py: 1.2, bgcolor: '#050606', borderTop: '1px solid #232927', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2, flexWrap: 'wrap' }}>
            <Typography noWrap sx={{ color: 'rgba(255,255,255,.86)', fontSize: 14, minWidth: 0, flex: 1 }}>
              {selectedFeed.camera.name}
              {selectedIndex >= 0 && filteredFeeds.length > 1 && <Box component="span" sx={{ color: 'rgba(255,255,255,.6)' }}>{` · ${selectedIndex + 1} of ${filteredFeeds.length}`}</Box>}
              <Box component="span" sx={{ display: { xs: 'none', sm: 'inline' }, color: 'rgba(255,255,255,.6)' }}>{` · ${selectedFeed.coverage}`}</Box>
            </Typography>
            {selectedZones.length > 0 && !selectedFeed.feed && (
              <Typography sx={{ color: 'rgba(255,255,255,.72)', fontSize: 14 }}>{selectedZones.length} AI zones configured</Typography>
            )}
            {selectedZones.length > 0 && selectedFeed.feed && (
              <FormControlLabel
                control={<Switch size="small" checked={showZones} onChange={(event) => setShowZones(event.target.checked)} />}
                label={`AI zones (${selectedZones.length})`}
                sx={{ color: 'rgba(255,255,255,.78)', mr: 0, '& .MuiFormControlLabel-label': { fontSize: 14 } }}
              />
            )}
          </Box>
        )}
      </Dialog>
    </Stack>
  );
}
