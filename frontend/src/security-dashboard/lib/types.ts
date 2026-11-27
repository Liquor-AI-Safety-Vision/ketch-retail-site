// Shapes returned by the Go API (backend/), which reads the AI service's
// tables. Vocabulary is the AI schema's: lowercase roles, statuses,
// severities and event types. Stores, cameras and zones are read-only here --
// the AI service writes them from its manifest.

export type StoreRole = 'owner' | 'manager' | 'operator';
export type AlertStatus = 'open' | 'acknowledged' | 'confirmed' | 'dismissed';
export type AlertSeverity = 'warning' | 'high' | 'critical';
export type AlertAction = 'acknowledge' | 'confirm' | 'dismiss';

// Event types the AI pipelines emit (ai/src/pipelines). weapon_detected is
// reserved for the weapon module that is not built yet.
export type AlertEventType =
  | 'suspected_cash_concealment'
  | 'unknown_entry'
  | 'staff_outside_schedule'
  | 'watchlist_match'
  | 'weapon_detected';

export type PipelineType =
  | 'counter_cash_theft_v1'
  | 'storage_intrusion_v1'
  | 'cold_storage_intrusion_v1'
  | 'entrance_face_capture_v1'
  | 'whole_store_tracking_v1';

export type CameraType = 'counter' | 'storage' | 'cold_storage' | 'entrance' | 'whole_store' | 'cold_drinks' | string;

export interface StoreMembership {
  storeId: string;
  storeName: string;
  storeCode: string;
  role: StoreRole;
}

export interface CurrentUser {
  id: string;
  email: string;
  displayName: string;
  stores: StoreMembership[];
}

export interface AuthSession {
  accessToken: string;
  user: CurrentUser;
}

export interface Store {
  id: string;
  name: string;
  code: string;
  timezone: string;
  role?: StoreRole;
}

export interface Camera {
  id: string;
  storeId: string;
  name: string;
  cameraType: CameraType;
  sourceType: string;
  isActive: boolean;
  pipelines: string[];
}

export interface CameraZone {
  pipelineId: string;
  name: string;
  type: string;
  /** Polygon vertices as fractions of frame width/height. */
  pointsNorm: number[][];
}

export interface Alert {
  id: string;
  storeId: string;
  cameraId: string;
  cameraName: string | null;
  pipelineType: string;
  eventType: AlertEventType | string;
  severity: AlertSeverity;
  status: AlertStatus;
  detectedAt: string;
  closedAt: string | null;
  identityPersonId: string | null;
  identityLabel: string | null;
  identityScore: number | null;
  reasonCodes: string[];
  metadata: Record<string, unknown>;
  reviewedById: string | null;
  reviewedByName: string | null;
  reviewedAt: string | null;
  reviewNote: string | null;
  hasVideoEvidence: boolean;
  hasSnapshot: boolean;
}

export type AlertMediaKind = 'clip' | 'snapshot' | string;

export interface AlertMedia {
  id: string;
  kind: AlertMediaKind;
  mimeType: string | null;
  startedAt: string | null;
  endedAt: string | null;
  durationSeconds: number | null;
}

export interface AlertDetail extends Alert {
  media: AlertMedia[];
}

export interface AlertPage {
  items: Alert[];
  nextCursor: string | null;
}

export interface AlertFilters {
  status?: AlertStatus;
  severity?: AlertSeverity;
  eventType?: string;
  pipelineType?: string;
  cameraId?: string;
  from?: string;
  to?: string;
  cursor?: string;
  limit?: number;
}

export interface AlertActionResult {
  id: string;
  status: AlertStatus;
  reviewedAt: string;
  reviewNote: string | null;
}

export interface PlaybackUrl {
  url: string;
  expiresInSeconds: number;
}

export interface CameraFeed {
  /** Looping or live video the browser can play directly. */
  videoUrl: string;
  posterUrl: string | null;
  /** true for footage that loops (public demo, test mode), false for live. */
  isRecording: boolean;
  /** 'hls' for a live stream (videoUrl is an .m3u8 playlist), else a plain file. */
  kind?: 'file' | 'hls';
  /** Frame size, when known, so zone overlays line up with the picture. */
  width?: number;
  height?: number;
}

export interface LiveSession {
  mode: 'test' | 'production';
  expiresInSeconds: number;
  streams: Record<string, string>;
}
