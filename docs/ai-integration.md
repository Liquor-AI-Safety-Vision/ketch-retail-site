# Connect an AI service to the alert API

The Go API accepts AI events. It does not run the live detection model, pull RTSP
streams or upload evidence files for the AI service. The external sender must
produce the event and place a video at the evidence origin before ingestion.
The streaming gateway remains a separate planned integration described in
[streaming-design.md](streaming-design.md).

## Prerequisites

1. Complete [local installation](../README.md#installation), including migration,
   owner/store registration and optional camera seed.
2. Generate a development `AI_INGEST_TOKEN` of at least 32 characters. Set it in
   `apps/api-go/.env` and in the AI sender, then restart the API.
3. Keep `NOTIFICATION_WORKER_ENABLED=false` for the first ingestion test. No
   Telegram/WhatsApp keys are needed. Use a separate development database.
4. Use actual UUIDs from your database/API. IDs starting `presentation-` or camera
   names displayed by the public demo are not production database IDs.

## Get store and camera IDs

PowerShell example against localhost, using the owner created during setup:

```powershell
$ownerPassword = Read-Host 'Local owner password' -AsSecureString
$loginBody = @{
  email = 'owner@example.test'
  password = [System.Net.NetworkCredential]::new('', $ownerPassword).Password
} | ConvertTo-Json
$session = Invoke-RestMethod -Method Post -Uri 'http://localhost:3000/api/v1/auth/login' -Headers @{ Origin = 'http://localhost:5173' } -ContentType 'application/json' -Body $loginBody
$ownerHeaders = @{ Authorization = 'Bearer ' + $session.accessToken }
$stores = Invoke-RestMethod -Uri 'http://localhost:3000/api/v1/stores' -Headers $ownerHeaders
$stores | Select-Object id, name, code
$storeId = ($stores | Where-Object code -eq 'liquor-store-demo').id
$cameras = Invoke-RestMethod -Uri "http://localhost:3000/api/v1/stores/$storeId/cameras" -Headers $ownerHeaders
$cameras | Select-Object id, name, isEnabled
$cameraId = ($cameras | Where-Object isEnabled | Select-Object -First 1).id
```

Change the store filter to your own code when not using the sample store. If
`$cameraId` is empty, seed an existing local store or create a camera through the
owner API. An optional `zoneId` must belong to that camera and be enabled.

## Submit one local event

Enter the same development token configured on the API. The request contains a
storage **key**, not a URL, upload body or camera credential. The illustrative
`storageKey` below must be backed by an actual video before playback can work.

```powershell
$aiToken = Read-Host 'Development AI_INGEST_TOKEN from your local .env' -AsSecureString
$aiHeaders = @{ Authorization = 'Bearer ' + [System.Net.NetworkCredential]::new('', $aiToken).Password }
$sourceEventId = 'local-ai-' + [guid]::NewGuid().ToString()
$now = [DateTimeOffset]::UtcNow
$payload = @{
  sourceEventId = $sourceEventId
  correlationId = 'local-incident-' + [guid]::NewGuid().ToString()
  storeId = $storeId
  cameraId = $cameraId
  type = 'SUSPICIOUS_PRODUCT_CONCEALMENT'
  severity = 'HIGH'
  subjectPersonCategory = 'UNKNOWN'
  confidence = 0.90
  detectedAt = $now.ToString('o')
  observedStartAt = $now.AddSeconds(-10).ToString('o')
  observedEndAt = $now.ToString('o')
  metadata = @{ model = 'local-integration'; modelVersion = '1' }
  evidence = @(@{
    storageKey = "ai-local/$sourceEventId.mp4"
    mimeType = 'video/mp4'
    durationSeconds = 10
    startsAt = $now.AddSeconds(-10).ToString('o')
    endsAt = $now.ToString('o')
  })
} | ConvertTo-Json -Depth 6
$created = Invoke-RestMethod -Method Post -Uri 'http://localhost:3000/api/v1/internal/ai/alerts' -Headers $aiHeaders -ContentType 'application/json' -Body $payload
$created | Select-Object alertId, sourceEventId, created
```

The first request returns HTTP `201` with `created: true` and a backend-generated
UUID `alertId`. `deliveries` can be empty if no notification rules match; a
successful ingestion does not require a provider send.

Repeat the **same** request with the same `sourceEventId` to verify idempotency:

```powershell
$repeated = Invoke-RestMethod -Method Post -Uri 'http://localhost:3000/api/v1/internal/ai/alerts' -Headers $aiHeaders -ContentType 'application/json' -Body $payload
$repeated | Select-Object alertId, sourceEventId, created
$detail = Invoke-RestMethod -Uri "http://localhost:3000/api/v1/stores/$storeId/alerts/$($created.alertId)" -Headers $ownerHeaders
$detail | Select-Object id, sourceEventId, type, hasVideoEvidence
```

The repeat returns `200`, `created: false` and the same `alertId`. It does not
replace the existing event/evidence. `sourceEventId` is globally unique, so prefix
it with the source/store/camera namespace. A retry must reuse the original ID;
an independent event must use a new one. Reuse a `correlationId` only for multiple
camera observations of the same physical incident.

Check the authenticated detail response using the returned UUID. The default
public dashboard shows sample fixtures and cannot prove the real event arrived.
The public demo also has presentation-only clips; use the real UUID deep
link `/#/alerts?alertId=<alertId>` and the API response when checking integration.

## Accepted event values

| Field | Accepted values / constraints |
|---|---|
| `sourceEventId` | Required, 1–200 characters; globally unique and stable across retries. |
| `correlationId` | Optional incident identifier, at most 200 characters. |
| `storeId`, `cameraId` | Existing UUIDs; camera must be enabled and belong to the store. |
| `zoneId` | Optional UUID of an enabled zone belonging to the camera. |
| `type` | `CASH_DRAWER_WITHOUT_CUSTOMER`, `SUSPICIOUS_CASH_HANDLING`, `POS_VOID_OR_REFUND`, `UNAUTHORIZED_STOCKROOM_ACCESS`, `HIGH_VALUE_ZONE_DWELL`, `WEAPON_DETECTED`, `SUSPICIOUS_PRODUCT_CONCEALMENT`. |
| `severity` | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`. |
| `subjectPersonCategory` | `EMPLOYEE`, `CUSTOMER`, `UNKNOWN`; defaults to `UNKNOWN`. |
| `confidence` | Optional number from 0 to 1. |
| `detectedAt` | Required RFC3339 timestamp with timezone. |
| `observedStartAt`, `observedEndAt` | Optional RFC3339 timestamps; start must not be after end. |
| `metadata` | Optional JSON object, at most 64 KiB. |
| `evidence` | Required array of 1–5 video clips. |
| Each evidence item | Unique relative `storageKey` (max 500 characters), `video/mp4` or `video/webm`, `durationSeconds` 1–600, and `startsAt < endsAt`. |

`REGULAR_SHOPLIFTER` is currently a demo presentation case, not an accepted backend
alert enum. Do not send it until a backend/schema change explicitly supports it.
The backend likewise does not add a new enum just because the AI emits a new label.

## Evidence and notifications

`storageKey` is relative to `EVIDENCE_ORIGIN_BASE_URL`. For example,
`ai-local/event-123.mp4` must be served at
`<EVIDENCE_ORIGIN_BASE_URL>/ai-local/event-123.mp4`. Ingestion stores metadata; it
does not verify that file exists or upload it. Keys must not start with `/`,
contain `..` path segments, backslashes or a URL scheme. Evidence keys are globally
unique; generate a separate key for each independent clip.

Owner playback requires both `PUBLIC_API_BASE_URL` and
`EVIDENCE_ORIGIN_BASE_URL`. When those are configured, the owner can request an
expiring link using the evidence UUID from the detail response:

`GET /api/v1/stores/{storeId}/alerts/{alertId}/evidence/{evidenceId}/playback-url`

Without secure review configuration that endpoint returns `503`, even though
ingestion succeeded. With an unreachable/missing file at the evidence origin,
the later video fetch fails. Public demo videos are separate static assets.

Matching alert metadata, evidence records and notification outbox rows are
committed atomically. The worker sends asynchronously only when enabled and when
endpoints, rules, channels and credential bindings are configured. The current
emergency setup uses `WEAPON_DETECTED` + `CRITICAL`, Telegram primary and WhatsApp
fallback. A sample normal event does not trigger that rule.

## Common responses

| Status | Meaning |
|---|---|
| `201` | New event/evidence stored; `created: true`. |
| `200` | Previously ingested `sourceEventId`; `created: false`. |
| `400` | Invalid field, unsupported enum or evidence metadata. |
| `401` | Missing/wrong AI bearer token. Owner JWT is not the ingestion token. |
| `404` | AI route disabled by blank token, or store/camera/zone not found/enabled. |
| `409` | Event ID belongs to another store or evidence storage key is already used. |
| `429` | Per-client ingestion rate limit (currently 120/minute); honor `Retry-After`. |

For deployment and provider-specific checks see
[environment.md](environment.md) and the
[production runbook](emergency-notification-production.md). Start with localhost;
a production event can enqueue real outbound notifications.
