import CloseIcon from '@mui/icons-material/Close';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import OpenInFullIcon from '@mui/icons-material/OpenInFull';
import RefreshIcon from '@mui/icons-material/Refresh';
import SearchIcon from '@mui/icons-material/Search';
import { Box, Button, Card, CardActionArea, Chip, Dialog, FormControl, IconButton, InputAdornment, InputLabel, MenuItem, Paper, Select, Stack, TextField, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { EmptyState, InlineError, ScreenLoader } from '../components/common';
import { getErrorMessage } from '../lib/api';
import type { Camera } from '../lib/types';

interface LiveFeed {
  camera: Camera;
  imageUrl: string;
  videoUrl: string;
  displayName: string;
  displayLocation: string;
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

const originalTrackingFeeds: Record<string, { videoUrl: string; imageUrl: string; displayName: string; displayLocation: string }> = {
  'record-counter-153921': {
    videoUrl: '/demo-source/cameras/cctv-counter/cash_till_open_2.mp4',
    imageUrl: '/demo-source/cameras/cctv-counter/cash_till_open_2.jpg',
    displayName: 'Counter',
    displayLocation: 'Staff, customer and cash drawer tracking',
  },
  'record-cold-drink-dispenser-152956': {
    videoUrl: '/demo-source/cameras/whole-store/cold_drinks.mp4',
    imageUrl: '/demo-source/cameras/whole-store/cold_drinks.jpg',
    displayName: 'Cold drink dispenser',
    displayLocation: 'Customer tracking near cold drinks',
  },
  'record-whole-store-corner-view-153134': {
    videoUrl: '/demo-source/cameras/whole-store/whole_store_corner.mp4',
    imageUrl: '/demo-source/cameras/whole-store/whole_store_corner.jpg',
    displayName: 'Whole store corner view',
    displayLocation: 'Store-wide customer tracking',
  },
  'record-whole-store-153158': {
    videoUrl: '/demo-source/cameras/whole-store/whole_store_long.mp4',
    imageUrl: '/demo-source/cameras/whole-store/whole_store_long.jpg',
    displayName: 'Whole store (Stream 1)',
    displayLocation: 'Store-wide customer tracking',
  },
  'record-whole-store-top-view-153423': {
    videoUrl: '/demo-source/cameras/whole-store/whole_store_short.mp4',
    imageUrl: '/demo-source/cameras/whole-store/whole_store_short.jpg',
    displayName: 'Whole store (Stream 2)',
    displayLocation: 'Store-wide customer tracking',
  },
  'record-entrance-top-view-153712': {
    videoUrl: '/demo-source/cameras/entrance/regular-shoplifter-tracking.mp4',
    imageUrl: '/demo-source/cameras/entrance/regular-shoplifter-tracking.jpg',
    displayName: 'Entrance top view',
    displayLocation: 'Customer recognition and entrance tracking',
  },
};

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

function cameraArea(camera: Camera) {
  const value = `${camera.name} ${camera.location}`.toLocaleLowerCase();
  if (value.includes('counter')) return 'Counter';
  if (value.includes('whole store') || value.includes('store top') || value.includes('store corner')) return 'Whole store';
  if (value.includes('kitchen') || value.includes('foodbox')) return 'Kitchen';
  if (value.includes('storage')) return 'Storage';
  if (value.includes('entrance')) return 'Entrance';
  if (value.includes('cold drink')) return 'Cold drinks';
  if (value.includes('control room')) return 'Control room';
  return 'Other';
}

function isCameraOnline(camera: Camera) {
  return camera.isEnabled && camera.status === 'ONLINE';
}

function recordAsset(camera: Camera, extension: 'jpg' | 'mp4') {
  return `/live-cameras/${encodeURIComponent(camera.streamGatewayRef)}.${extension}`;
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

  const feeds = useMemo<LiveFeed[]>(() => cameras
    .filter((camera) => camera.streamGatewayRef.startsWith('record-'))
    .map((camera, index) => {
      const originalFeed = originalTrackingFeeds[camera.streamGatewayRef];
      return {
        camera,
        imageUrl: originalFeed?.imageUrl ?? recordAsset(camera, 'jpg'),
        videoUrl: originalFeed?.videoUrl ?? recordAsset(camera, 'mp4'),
        displayName: originalFeed?.displayName ?? camera.name,
        displayLocation: originalFeed?.displayLocation ?? camera.location,
        channelNumber: index + 1,
      };
    }), [cameras]);

  const onlineCount = feeds.filter(({ camera }) => isCameraOnline(camera)).length;
  const areas = useMemo<string[]>(() => Array.from(new Set<string>(feeds.map(({ camera }) => cameraArea(camera)))).sort((left, right) => left.localeCompare(right)), [feeds]);
  const selectedArea = preferences.area === 'ALL' || areas.includes(preferences.area) ? preferences.area : 'ALL';

  const scopedFeeds = useMemo(() => {
    const search = preferences.search.trim().toLocaleLowerCase();
    return feeds.filter(({ camera }) => {
      if (selectedArea !== 'ALL' && cameraArea(camera) !== selectedArea) return false;
      const originalFeed = originalTrackingFeeds[camera.streamGatewayRef];
      if (search && !`${originalFeed?.displayName ?? camera.name} ${originalFeed?.displayLocation ?? camera.location} ${cameraArea(camera)}`.toLocaleLowerCase().includes(search)) return false;
      return true;
    });
  }, [feeds, preferences.search, selectedArea]);

  const scopedOnlineCount = scopedFeeds.filter(({ camera }) => isCameraOnline(camera)).length;
  const scopedOfflineCount = scopedFeeds.length - scopedOnlineCount;
  const filteredFeeds = useMemo(() => scopedFeeds.filter(({ camera }) => {
    const online = isCameraOnline(camera);
    if (preferences.status === 'LIVE') return online;
    if (preferences.status === 'OFFLINE') return !online;
    return true;
  }), [preferences.status, scopedFeeds]);

  const selectedFeed = useMemo(() => feeds.find(({ camera }) => camera.id === selectedFeedId) ?? null, [feeds, selectedFeedId]);
  const selectedFeedOnline = Boolean(selectedFeed?.camera.isEnabled && selectedFeed.camera.status === 'ONLINE');

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
            Select a camera to open its live security feed.
          </Typography>
        </Box>
        <Chip
          color={onlineCount === feeds.length && feeds.length > 0 ? 'success' : 'warning'}
          label={`${onlineCount} of ${feeds.length} live`}
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
            <ToggleButton value="LIVE">Live {scopedOnlineCount}</ToggleButton>
            <ToggleButton value="OFFLINE">Offline {scopedOfflineCount}</ToggleButton>
          </ToggleButtonGroup>
          <Typography variant="caption" color="text.secondary">Showing {filteredFeeds.length} of {scopedFeeds.length} cameras</Typography>
        </Box>
      </Paper>

      {feeds.length === 0 ? <EmptyState title="No record feeds found" description="Run the demo seed to load camera records." /> : filteredFeeds.length === 0 ? (
        <Paper variant="outlined" sx={{ py: 6, px: 3, textAlign: 'center', borderStyle: 'dashed' }}>
          <Typography variant="h6">No cameras match these filters</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.75 }}>Try another search or clear the current camera filters.</Typography>
          <Button variant="outlined" sx={{ mt: 2 }} onClick={() => setPreferences(defaultPreferences)}>Clear filters</Button>
        </Paper>
      ) : (
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, minmax(0, 1fr))', lg: 'repeat(3, minmax(0, 1fr))', xl: 'repeat(4, minmax(0, 1fr))' }, gridAutoFlow: 'dense', gap: 1.25 }}>
          {filteredFeeds.map((feed) => {
            const online = isCameraOnline(feed.camera);
            return (
              <Card key={feed.camera.id} className="live-feed" sx={{ overflow: 'hidden', '&:hover': { borderColor: 'primary.main' }, transition: 'border-color 160ms ease' }}>
                <CardActionArea
                  onClick={() => setSelectedFeedId(feed.camera.id)}
                  aria-label={`Open live view for ${feed.displayName}`}
                  sx={{ touchAction: 'pan-y', '&:hover': { bgcolor: 'rgba(216,163,93,.025)' } }}
                >
                  <Box sx={{ position: 'relative', aspectRatio: '16 / 9', bgcolor: '#050606', overflow: 'hidden' }}>
                    <Box component="img" src={feed.imageUrl} alt={`${feed.displayName} camera preview`} loading="lazy" decoding="async" sx={{ width: '100%', height: '100%', objectFit: 'cover', filter: online ? 'brightness(.86) contrast(1.1)' : 'grayscale(1) brightness(.42)' }} />
                    <Box sx={{ position: 'absolute', top: 10, left: 10, display: 'flex', alignItems: 'center', gap: 0.6, bgcolor: 'rgba(9,11,12,.82)', color: online ? '#9BD2AC' : '#C6C9C7', px: 1, py: 0.45, borderRadius: 0.5, fontSize: 10, fontWeight: 750, letterSpacing: '.08em' }}>
                      <FiberManualRecordIcon sx={{ fontSize: 9 }} />
                      {online ? 'LIVE' : 'OFFLINE'}
                    </Box>
                    <Box sx={{ position: 'absolute', right: 10, bottom: 9, display: 'flex', alignItems: 'center', gap: 0.65, px: 0.8, py: 0.35, bgcolor: 'rgba(9,11,12,.78)', color: '#D5D8D6', fontSize: 10, fontFamily: 'monospace' }}>
                      <OpenInFullIcon sx={{ fontSize: 12 }} />
                      CAM {String(feed.channelNumber).padStart(2, '0')}
                    </Box>
                  </Box>
                  <Box sx={{ px: 1.75, py: 1.4, textAlign: 'left' }}>
                    <Typography fontWeight={650} noWrap>{feed.displayName}</Typography>
                    <Typography variant="caption" color="text.secondary" noWrap display="block" sx={{ mt: 0.25 }}>{feed.displayLocation}</Typography>
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
          'aria-label': selectedFeed ? `Live view for ${selectedFeed.displayName}` : undefined,
          sx: { overflow: 'hidden', bgcolor: '#050606', backgroundImage: 'none', border: '1px solid #303735' },
        }}
      >
        {selectedFeed && (
          <Box sx={{ position: 'relative', bgcolor: '#020303', aspectRatio: '16 / 9', maxHeight: 'calc(100vh - 170px)' }}>
            {selectedFeedOnline ? (
              <Box
                component="video"
                key={selectedFeed.camera.id}
                src={selectedFeed.videoUrl}
                poster={selectedFeed.imageUrl}
                autoPlay
                muted
                loop
                playsInline
                controls={false}
                disablePictureInPicture
                onContextMenu={(event) => event.preventDefault()}
                sx={{ display: 'block', width: '100%', height: '100%', objectFit: 'contain', bgcolor: '#000' }}
              />
            ) : (
              <Box
                component="img"
                src={selectedFeed.imageUrl}
                alt={`${selectedFeed.displayName} offline camera image`}
                sx={{ display: 'block', width: '100%', height: '100%', objectFit: 'contain', bgcolor: '#000', filter: 'grayscale(1) brightness(.42)' }}
              />
            )}
            <Box sx={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: 'linear-gradient(180deg, rgba(0,0,0,.52), transparent 22%)' }} />
            <Box sx={{ position: 'absolute', top: 16, left: 18, display: 'flex', alignItems: 'center', gap: 0.8, color: selectedFeedOnline ? '#A8DAB7' : '#C6C9C7', fontSize: 11, fontWeight: 800, letterSpacing: '.12em' }}>
              <FiberManualRecordIcon sx={{ fontSize: 10 }} /> {selectedFeedOnline ? 'LIVE PREVIEW' : 'CAMERA OFFLINE'}
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
          <Box sx={{ px: 2.25, py: 1.6, bgcolor: '#050606', borderTop: '1px solid #232927' }}>
            <Typography noWrap sx={{ color: 'rgba(255,255,255,.78)', fontSize: 13 }}>{selectedFeed.displayLocation}</Typography>
          </Box>
        )}
      </Dialog>
    </Stack>
  );
}
