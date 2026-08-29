import type { Alert } from './types';

export interface StorageAlertSession {
  sessionId: '01' | '02' | '03';
  clipCount: number;
  personLabel: string;
  cameraName: 'Cold storage' | 'Storage';
}

const storageAlertSessions: Record<string, StorageAlertSession> = {
  'pdsi-ui-fixture-017': {
    sessionId: '01',
    clipCount: 7,
    personLabel: 'Employee + unknown',
    cameraName: 'Cold storage',
  },
  'pdsi-ui-fixture-013': {
    sessionId: '02',
    clipCount: 4,
    personLabel: 'Unknown',
    cameraName: 'Cold storage',
  },
  'pdsi-ui-fixture-005': {
    sessionId: '03',
    clipCount: 3,
    personLabel: 'Employee',
    cameraName: 'Storage',
  },
};

interface PresentationAlertDefinition {
  id: string;
  sourceEventId: string;
  correlationId: string;
  type: 'WEAPON_DETECTED' | 'SUSPICIOUS_PRODUCT_CONCEALMENT' | 'REGULAR_SHOPLIFTER';
  severity: Alert['severity'];
  defaultStatus: 'NEW' | 'RESOLVED';
  detectedAt: string;
  videoUrl: string;
  thumbnailUrl: string;
  decisionKey?: string;
  cameraId?: string;
  cameraName?: string;
  personLabel?: string;
}

const presentationAlertDefinitions: PresentationAlertDefinition[] = [
  {
    id: 'presentation-weapon-review-001',
    sourceEventId: 'whole-store-weapon-knife-001',
    correlationId: 'whole-store-weapon-track-001',
    type: 'WEAPON_DETECTED',
    severity: 'CRITICAL',
    defaultStatus: 'NEW',
    detectedAt: '2026-08-21T15:49:00.000Z',
    videoUrl: '/demo-source/alerts/weapon/weapon-review.mp4',
    thumbnailUrl: '/demo-source/alerts/weapon/weapon-review.jpg',
  },
  {
    id: 'presentation-weapon-review-002',
    sourceEventId: 'whole-store-weapon-gun-001',
    correlationId: 'whole-store-weapon-track-002',
    type: 'WEAPON_DETECTED',
    severity: 'CRITICAL',
    defaultStatus: 'NEW',
    detectedAt: '2026-08-21T15:47:00.000Z',
    videoUrl: '/demo-source/alerts/weapon/weapon-review-02.mp4',
    thumbnailUrl: '/demo-source/alerts/weapon/weapon-review-02.jpg',
  },
  {
    id: 'presentation-shoplifting-review-001',
    sourceEventId: 'whole-store-shoplifting-001',
    correlationId: 'whole-store-shoplifting-track-001',
    type: 'SUSPICIOUS_PRODUCT_CONCEALMENT',
    severity: 'HIGH',
    defaultStatus: 'NEW',
    detectedAt: '2026-08-21T15:45:00.000Z',
    videoUrl: '/demo-source/alerts/shoplifting/shoplifting-review.mp4',
    thumbnailUrl: '/demo-source/alerts/shoplifting/shoplifting-review.jpg',
    decisionKey: 'ketch:presentation-alert:shoplifting-review-v2',
  },
  {
    id: 'presentation-shoplifting-confirmed-001',
    sourceEventId: 'whole-store-shoplifting-002',
    correlationId: 'whole-store-shoplifting-track-002',
    type: 'SUSPICIOUS_PRODUCT_CONCEALMENT',
    severity: 'HIGH',
    defaultStatus: 'RESOLVED',
    detectedAt: '2026-08-21T15:43:00.000Z',
    videoUrl: '/demo-source/alerts/shoplifting/shoplifting-confirmed.mp4',
    thumbnailUrl: '/demo-source/alerts/shoplifting/shoplifting-confirmed.jpg',
  },
  {
    id: 'presentation-regular-shoplifter-review-001',
    sourceEventId: 'entrance-regular-shoplifter-001',
    correlationId: 'entrance-regular-shoplifter-track-001',
    type: 'REGULAR_SHOPLIFTER',
    severity: 'HIGH',
    defaultStatus: 'NEW',
    detectedAt: '2026-08-26T11:03:35.000Z',
    videoUrl: '/demo-source/alerts/regular-shoplifter/regular-shoplifter-alert.mp4',
    thumbnailUrl: '/demo-source/alerts/regular-shoplifter/regular-shoplifter-alert.jpg',
    decisionKey: 'ketch:presentation-alert:regular-shoplifter-review-v1',
    cameraId: 'presentation-entrance-top-view-camera',
    cameraName: 'Entrance top view',
    personLabel: 'Customer',
  },
];

const replacedPresentationTypes = new Set(presentationAlertDefinitions.map((definition) => definition.type));
const decisionKeyByAlertId = new Map(
  presentationAlertDefinitions
    .filter((definition) => definition.decisionKey)
    .map((definition) => [definition.id, definition.decisionKey!]),
);
const locallyDismissedConfirmedAlertsKey = 'ketch:locally-dismissed-confirmed-alerts-v1';

function locallyDismissedConfirmedAlertIds() {
  if (typeof window === 'undefined') return new Set<string>();
  try {
    const value = JSON.parse(window.localStorage.getItem(locallyDismissedConfirmedAlertsKey) ?? '[]');
    return new Set(Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []);
  } catch {
    return new Set<string>();
  }
}

function presentationAlertStatus(definition: PresentationAlertDefinition): Alert['status'] {
  if (!definition.decisionKey || typeof window === 'undefined') return definition.defaultStatus;
  const value = window.localStorage.getItem(definition.decisionKey);
  return value === 'RESOLVED' || value === 'DISMISSED' ? value : definition.defaultStatus;
}

function presentationAlert(definition: PresentationAlertDefinition): Alert {
  const status = presentationAlertStatus(definition);
  const cameraName = definition.cameraName ?? 'Whole store';
  const personLabel = definition.personLabel ?? 'Customer';
  return {
    id: definition.id,
    sourceEventId: definition.sourceEventId,
    correlationId: definition.correlationId,
    type: definition.type,
    severity: definition.severity,
    status,
    subjectPersonCategory: 'CUSTOMER',
    confidence: null,
    detectedAt: definition.detectedAt,
    acknowledgedAt: status === 'RESOLVED' ? definition.detectedAt : null,
    resolutionNote: status === 'RESOLVED' ? 'Owner review: confirmed as a real incident.' : null,
    metadata: {
      sampleOnly: true,
      sourceSystem: 'cctv-upload-fixture',
      presentationOnly: true,
      presentationVideoUrl: definition.videoUrl,
      presentationThumbnailUrl: definition.thumbnailUrl,
      videoUrl: definition.videoUrl,
      thumbnailUrl: definition.thumbnailUrl,
      presentationPersonLabel: personLabel,
      trackedAcrossCamera: cameraName,
    },
    cameraId: definition.cameraId ?? 'presentation-whole-store-camera',
    cameraName,
    zoneId: null,
    zoneName: null,
  };
}

function metadataString(alert: Alert, key: string) {
  const value = alert.metadata?.[key];
  return typeof value === 'string' ? value : null;
}

export function storageAlertSessionFor(sourceEventId: string) {
  return storageAlertSessions[sourceEventId] ?? null;
}

export function presentationBaseAlertId(alert: Alert) {
  return metadataString(alert, 'presentationBaseAlertId') ?? alert.id;
}

export function isPresentationOnlyAlert(alert: Alert) {
  return alert.metadata?.presentationOnly === true;
}

export function setPresentationAlertDecision(alert: Alert, status: 'RESOLVED' | 'DISMISSED') {
  if (typeof window === 'undefined') return;
  const decisionKey = decisionKeyByAlertId.get(alert.id);
  if (decisionKey) window.localStorage.setItem(decisionKey, status);
}

export function hideConfirmedAlertLocally(alert: Alert) {
  if (typeof window === 'undefined') return;
  const hiddenIds = locallyDismissedConfirmedAlertIds();
  hiddenIds.add(alert.id);
  window.localStorage.setItem(locallyDismissedConfirmedAlertsKey, JSON.stringify([...hiddenIds]));
}

export function expandPresentationAlerts(items: Alert[], statusScope: 'NEW' | 'RESOLVED' | 'ALL' = 'ALL') {
  const expanded = items.flatMap((alert) => {
    if (alert.type !== 'UNAUTHORIZED_STOCKROOM_ACCESS') return [alert];
    const session = storageAlertSessionFor(alert.sourceEventId);
    if (!session) return [alert];

    return Array.from({ length: session.clipCount }, (_, index) => {
      const clipNumber = index + 1;
      const clipToken = String(clipNumber).padStart(2, '0');
      const assetBase = `/demo-source/alerts/storage/segments/storage-session-${session.sessionId}-clip-${clipToken}`;
      return {
        ...alert,
        id: `${alert.id}:clip:${clipToken}`,
        sourceEventId: `${alert.sourceEventId}:clip:${clipToken}`,
        correlationId: alert.correlationId ?? alert.id,
        detectedAt: new Date(new Date(alert.detectedAt).getTime() + index * 15_000).toISOString(),
        cameraName: session.cameraName,
        metadata: {
          ...(alert.metadata ?? {}),
          presentationBaseAlertId: alert.id,
          presentationClipNumber: clipNumber,
          presentationClipCount: session.clipCount,
          presentationClipDurationSeconds: 15,
          presentationPersonLabel: session.personLabel,
          presentationVideoUrl: `${assetBase}.mp4`,
          presentationThumbnailUrl: `${assetBase}.jpg`,
        },
      };
    });
  });

  const retainedAlerts = expanded.filter((alert) => !replacedPresentationTypes.has(alert.type as PresentationAlertDefinition['type']));
  const presentationAlerts = presentationAlertDefinitions
    .map(presentationAlert)
    .filter((alert) => alert.status !== 'DISMISSED')
    .filter((alert) => statusScope === 'ALL' || alert.status === statusScope);

  const hiddenIds = locallyDismissedConfirmedAlertIds();
  return [...retainedAlerts, ...presentationAlerts].filter((alert) => !hiddenIds.has(alert.id));
}
