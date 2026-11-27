import CloseIcon from '@mui/icons-material/Close';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import OpenInFullIcon from '@mui/icons-material/OpenInFull';
import RefreshIcon from '@mui/icons-material/Refresh';
import SearchIcon from '@mui/icons-material/Search';
import VideocamOffOutlinedIcon from '@mui/icons-material/VideocamOffOutlined';
import { Box, Button, Card, CardActionArea, Chip, Dialog, FormControl, FormControlLabel, IconButton, InputAdornment, InputLabel, MenuItem, Paper, Select, Stack, Switch, TextField, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material';
import { useQueries, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { CameraEmptyState, InlineError, ScreenLoader, cameraAreaLabel, cameraCoverage } from '../components/common';
import { getErrorMessage } from '../lib/api';
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
            window.setTimeout(() => hls.startLoad(), 3000);
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
    return () => { destroyed = true; cleanup(); };
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
  } = useQuery({
    queryKey: ['cameras', store?.id],
    queryFn: () => api.getCameras(store!.id),
    enabled: Boolean(store),
    staleTime: 30_000,
    gcTime: 10 * 60_000,
    refetchInterval: 60_000,
  });

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
  const onlineCount = feeds.filter(({ camera }) => camera.isActive).length;
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

  const scopedOnlineCount = scopedFeeds.filter(({ camera }) => camera.isActive).length;
  const scopedOfflineCount = scopedFeeds.length - scopedOnlineCount;
  const filteredFeeds = useMemo(() => scopedFeeds.filter(({ camera }) => {
    const online = camera.isActive;
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

  if (!store || isLoading) return <ScreenLoader label="Loading cameras…" />;

  return (
    <Stack spacing={3} sx={{ overflowX: 'hidden' }}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'end', gap: 2 }}>
        <Box sx={{ maxWidth: 760 }}>
          <Typography component="h1" variant="h4">Live camera</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.75 }}>
            Select a camera to open its feed and the zones its AI pipelines watch.
          </Typography>
        </Box>
        <Chip
          color={onlineCount === feeds.length && feeds.length > 0 ? 'success' : 'warning'}
          label={`${onlineCount} of ${feeds.length} active`}
        />
      </Box>

      {error && <InlineError message={getErrorMessage(error)} />}

      <Paper
        variant="outlined"
        sx={{
          position: 'sticky',
          top: { xs: 66, md: 12 },
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
            <InputLabel id="camera-area-label">Area</InputLabel>
            <Select
              labelId="camera-area-label"
              label="Area"
              value={selectedArea}
              onChange={(event) => setPreferences((current) => ({ ...current, area: event.target.value }))}
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
            <ToggleButton value="LIVE">Active {scopedOnlineCount}</ToggleButton>
            <ToggleButton value="OFFLINE">Inactive {scopedOfflineCount}</ToggleButton>
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
            const online = feed.camera.isActive;
            return (
              <Card key={feed.camera.id} className="live-feed" sx={{ overflow: 'hidden', '&:hover': { borderColor: 'primary.main' }, transition: 'border-color 160ms ease' }}>
                <CardActionArea
                  onClick={() => setSelectedFeedId(feed.camera.id)}
                  aria-label={`Open camera view for ${feed.camera.name}`}
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
                    <Box sx={{ position: 'absolute', top: 10, left: 10, display: 'flex', alignItems: 'center', gap: 0.6, bgcolor: 'rgba(9,11,12,.82)', color: online ? '#9BD2AC' : '#C6C9C7', px: 1, py: 0.45, borderRadius: 0.5, fontSize: 10, fontWeight: 750, letterSpacing: '.08em' }}>
                      <FiberManualRecordIcon sx={{ fontSize: 9 }} />
                      {!online ? 'INACTIVE' : feedLabel(feed.feed, true)}
                    </Box>
                    <Box sx={{ position: 'absolute', right: 10, bottom: 9, display: 'flex', alignItems: 'center', gap: 0.65, px: 0.8, py: 0.35, bgcolor: 'rgba(9,11,12,.78)', color: '#D5D8D6', fontSize: 10, fontFamily: 'monospace' }}>
                      <OpenInFullIcon sx={{ fontSize: 12 }} />
                      CAM {String(feed.channelNumber).padStart(2, '0')}
                    </Box>
                  </Box>
                  <Box sx={{ px: 1.75, py: 1.4, textAlign: 'left' }}>
                    <Typography fontWeight={650} noWrap>{feed.camera.name}</Typography>
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
        PaperProps={{
          // MUI forwards a Dialog-level aria-label to the modal root, not to the
          // element that carries role="dialog", so it is set on the paper here.
          'aria-label': selectedFeed ? `Camera view for ${selectedFeed.camera.name}` : undefined,
          sx: { overflow: 'hidden', bgcolor: '#050606', backgroundImage: 'none', border: '1px solid #303735' },
        }}
      >
        {selectedFeed && (
          <Box sx={{ position: 'relative', bgcolor: '#020303', aspectRatio: '16 / 9', maxHeight: 'calc(100vh - 170px)', width: '100%' }}>
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
                  <Typography sx={{ position: 'absolute', bottom: 14, left: 18, right: 18, color: '#E6B3AE', fontSize: 13 }}>{playError}</Typography>
                )}
              </Box>
            ) : (
              <Box sx={{ width: '100%', height: '100%', display: 'grid', placeItems: 'center', color: '#8A918F', textAlign: 'center', px: 3 }}>
                <Box>
                  <VideocamOffOutlinedIcon sx={{ fontSize: 44 }} />
                  <Typography sx={{ mt: 1, color: 'inherit' }}>
                    {selectedFeedOnline ? 'Live view is not enabled for this store yet.' : 'This camera is inactive.'}
                  </Typography>
                  <Typography variant="body2" sx={{ mt: 0.5, color: '#6A716F' }}>
                    The AI service is still analysing it; alerts and their clips appear under Alerts.
                  </Typography>
                </Box>
              </Box>
            )}
            <Box sx={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: 'linear-gradient(180deg, rgba(0,0,0,.52), transparent 22%)' }} />
            <Box sx={{ position: 'absolute', top: 16, left: 18, display: 'flex', alignItems: 'center', gap: 0.8, color: selectedFeedOnline ? '#A8DAB7' : '#C6C9C7', fontSize: 11, fontWeight: 800, letterSpacing: '.12em' }}>
              <FiberManualRecordIcon sx={{ fontSize: 10 }} /> {!selectedFeedOnline ? 'CAMERA INACTIVE' : feedLabel(selectedFeed.feed, false)}
            </Box>
            <IconButton onClick={() => setSelectedFeedId(null)} aria-label="Close live view" sx={{ position: 'absolute', top: 9, right: 10, color: '#FFF', bgcolor: 'rgba(0,0,0,.5)', '&:hover': { bgcolor: 'rgba(0,0,0,.78)' } }}>
              <CloseIcon />
            </IconButton>
          </Box>
        )}
        {selectedFeed && (
          /* The recorded footage already burns the camera name into the
             bottom-left corner, so the caption sits under the frame instead of
             on top of it, and repeats only what the footage does not carry. */
          <Box sx={{ px: 2.25, py: 1.2, bgcolor: '#050606', borderTop: '1px solid #232927', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2, flexWrap: 'wrap' }}>
            <Typography noWrap sx={{ color: 'rgba(255,255,255,.78)', fontSize: 13 }}>{selectedFeed.camera.name} · {selectedFeed.coverage}</Typography>
            {selectedZones.length > 0 && !selectedFeed.feed && (
              <Typography sx={{ color: 'rgba(255,255,255,.6)', fontSize: 13 }}>{selectedZones.length} AI zones configured</Typography>
            )}
            {selectedZones.length > 0 && selectedFeed.feed && (
              <FormControlLabel
                control={<Switch size="small" checked={showZones} onChange={(event) => setShowZones(event.target.checked)} />}
                label={`AI zones (${selectedZones.length})`}
                sx={{ color: 'rgba(255,255,255,.78)', mr: 0, '& .MuiFormControlLabel-label': { fontSize: 13 } }}
              />
            )}
          </Box>
        )}
      </Dialog>
    </Stack>
  );
}
