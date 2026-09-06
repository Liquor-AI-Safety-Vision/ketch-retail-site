import { ApiError, type DashboardApi } from './api.ts';
import { expandPresentationAlerts } from './presentationAlerts.ts';
import type { Alert, AlertDetail, Camera, Store } from './types';

// Public, synthetic records reference only media already shipped with this site.
// No database snapshot, owner identity, token, camera password or network client.
export const demoStore: Store = {
  id: 'public-demo-store', name: 'Liquor Store', code: 'public-demo',
  address: null, timezone: 'America/Chicago', role: 'OPERATOR',
};

const cameraDefinitions: [string, string, string][] = [
  ['record-cold-drink-dispenser-152956', 'Cold drink dispenser (Stream 1)', 'Cold drinks'],
  ['record-kitchen-foodbox-153050', 'Kitchen foodbox', 'Kitchen'],
  ['record-foodbox-153112', 'Foodbox', 'Kitchen'],
  ['record-whole-store-corner-view-153134', 'Whole store corner view', 'Whole store'],
  ['record-whole-store-153158', 'Whole store (Stream 1)', 'Whole store'],
  ['record-counter-top-view-153244', 'Counter top view (Stream 1)', 'Counter'],
  ['record-cold-drink-dispenser-153308', 'Cold drink dispenser (Stream 2)', 'Cold drinks'],
  ['record-cold-drink-fridge-153353', 'Cold drink fridge', 'Cold drinks'],
  ['record-whole-store-top-view-153423', 'Whole store top view (Stream 1)', 'Whole store'],
  ['record-cold-storage-153534', 'Cold storage', 'Storage'],
  ['record-kitchen-153607', 'Kitchen', 'Kitchen'],
  ['record-entrance-top-view-153712', 'Entrance top view', 'Entrance'],
  ['record-store-top-corner-view-153819', 'Store top corner view', 'Whole store'],
  ['record-counter-153921', 'Counter', 'Counter'],
  ['record-counter-top-view-154014', 'Counter top view (Stream 2)', 'Counter'],
  ['record-storage-154138', 'Storage', 'Storage'],
  ['record-control-room-154343', 'Control room', 'Control room'],
  ['record-whole-store-top-view-155405', 'Whole store top view (Stream 2)', 'Whole store'],
];

const sampleDate = '2026-08-21T15:30:00.000Z';
const cameras: Camera[] = cameraDefinitions.map(([ref, name, location]) => ({
  id: `demo-${ref}`, name, location, protocol: 'RTSP', streamGatewayRef: ref,
  status: ref === 'record-kitchen-153607' || ref === 'record-whole-store-top-view-155405' ? 'OFFLINE' : 'ONLINE',
  isEnabled: true, createdAt: sampleDate, updatedAt: sampleDate,
}));

function sampleAlert(id: string, type: string, status: Alert['status'], cameraRef: string, media: string): Alert {
  const camera = cameras.find((item) => item.streamGatewayRef === cameraRef)!;
  return {
    id: `demo-alert-${id}`, sourceEventId: id, correlationId: `demo-incident-${id}`,
    type, severity: 'MEDIUM', status, subjectPersonCategory: 'EMPLOYEE', confidence: null,
    detectedAt: sampleDate, acknowledgedAt: status === 'RESOLVED' ? sampleDate : null,
    resolutionNote: null, cameraId: camera.id, cameraName: camera.name, zoneId: null, zoneName: null,
    hasVideoEvidence: true,
    metadata: {
      sampleOnly: true, presentationOnly: true, sourceSystem: 'cctv-upload-fixture',
      videoUrl: `${media}.mp4`, thumbnailUrl: `${media}.jpg`,
      presentationVideoUrl: `${media}.mp4`, presentationThumbnailUrl: `${media}.jpg`,
    },
  };
}

const baseAlerts: Alert[] = [
  sampleAlert('drawer', 'CASH_DRAWER_WITHOUT_CUSTOMER', 'NEW', 'record-counter-153921', '/demo-source/alerts/counter-generated/cvlp_h3_00043_Left'),
  sampleAlert('cash-review', 'SUSPICIOUS_CASH_HANDLING', 'NEW', 'record-counter-153921', '/demo-source/alerts/counter-generated/cvlp_h3_00013_Right'),
  sampleAlert('cash-confirmed', 'SUSPICIOUS_CASH_HANDLING', 'RESOLVED', 'record-counter-153921', '/demo-source/alerts/counter-generated/cvlp_h3_00045_Left'),
  sampleAlert('pdsi-ui-fixture-017', 'UNAUTHORIZED_STOCKROOM_ACCESS', 'RESOLVED', 'record-cold-storage-153534', '/demo-source/alerts/storage/storage_1'),
  sampleAlert('pdsi-ui-fixture-013', 'UNAUTHORIZED_STOCKROOM_ACCESS', 'RESOLVED', 'record-cold-storage-153534', '/demo-source/alerts/storage/storage_1'),
  sampleAlert('pdsi-ui-fixture-005', 'UNAUTHORIZED_STOCKROOM_ACCESS', 'RESOLVED', 'record-storage-154138', '/demo-source/alerts/storage/storage_2'),
];

function requireDemoStore(storeId: string) {
  if (storeId !== demoStore.id) throw new ApiError('Store not available in the public demo.', 404);
}

function allAlerts() {
  return expandPresentationAlerts(structuredClone(baseAlerts), 'ALL', { readOnly: true });
}

export const demoApi: DashboardApi = {
  async getCameras(storeId) {
    requireDemoStore(storeId);
    return structuredClone(cameras);
  },
  async getAlerts(storeId, filters = {}) {
    requireDemoStore(storeId);
    const items = allAlerts().filter((alert) => (
      (!filters.status || alert.status === filters.status)
      && (!filters.type || alert.type === filters.type)
      && (!filters.severity || alert.severity === filters.severity)
      && (!filters.cameraId || alert.cameraId === filters.cameraId)
      && (!filters.subjectPersonCategory || alert.subjectPersonCategory === filters.subjectPersonCategory)
    ));
    const start = filters.cursor ? Math.max(0, items.findIndex((item) => item.id === filters.cursor) + 1) : 0;
    const limit = Math.max(1, Math.min(100, filters.limit ?? 100));
    const page = items.slice(start, start + limit);
    return { items: page, nextCursor: start + limit < items.length ? page.at(-1)!.id : null };
  },
  async getAlert(storeId, alertId): Promise<AlertDetail> {
    requireDemoStore(storeId);
    const alert = allAlerts().find((item) => item.id === alertId);
    if (!alert) throw new ApiError('This alert requires an owner sign-in or is not part of the demo.', 404);
    return { ...alert, acknowledgedById: null, acknowledgedByName: null, evidence: [] };
  },
  async getEvidencePlaybackUrl() {
    throw new ApiError('Private evidence requires an owner sign-in.', 403);
  },
  async actionAlert() {
    throw new ApiError('The public demo is read-only. Sign in to manage real alerts.', 403);
  },
};
