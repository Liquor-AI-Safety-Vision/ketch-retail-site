# REST API contract

The authoritative, always-current reference is [`internal/server/openapi.json`](../internal/server/openapi.json)
(served at `/docs` when `SWAGGER_ENABLED=true`); `openapi_sync_test.go` fails if it and the mounted
routes ever differ. This page is a generated overview of it.

- Prefix `/api/v1`, JSON bodies, ISO-8601 timestamps.
- Authenticated routes take `Authorization: Bearer <access token>` and an `active` owner account.
  `401` = not signed in / account unavailable, `403` = store role too low, `404` = outside the caller's stores.
- In production, state-changing browser requests must carry an `Origin` equal to `WEB_ORIGIN`.
- Ids are the AI schema's text ids (`store_test_01`, `st01_counter`, alert UUIDs). Vocabulary is lowercase:
  roles `owner > manager > operator`; alert status `open → acknowledged | confirmed | dismissed`;
  severity `warning | high | critical`.
- Stores, cameras and zones are read-only: the AI service writes them from its manifest.
- Alert lists page with an opaque `nextCursor`; `total` counts every match. `status`, `severity` and
  `eventType` accept comma-separated lists. `/alerts/summary` gives counts by status and event type.
- Review decisions: anyone with access may acknowledge or dismiss an open alert; `confirm` and any change
  to a confirmed/dismissed alert need manager or owner. Each decision is serialized and recorded in
  `alert_status_history`.
- Media is never served from R2 directly: `…/media/{mediaId}/playback-url` returns a short-lived
  `/api/v1/notification-review/{token}` link that streams the object (with Range support).
- Live view: `POST /stores/{storeId}/live-session` sets a cookie scoped to `/live/`; the reverse proxy
  checks every HLS request with `GET /live/authorize` (see `internal/server/live.go`).

## Auth

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/auth/login` | Log in |
| POST | `/auth/logout` | Revoke refresh session |
| GET | `/auth/me` | Current user and stores |
| POST | `/auth/refresh` | Rotate refresh session |
| POST | `/auth/register` | Create an owner for an existing store (only when REGISTER_ENABLED) |

## Users

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/users/me` | Get owner profile |
| PATCH | `/users/me` | Update owner profile |

## Stores

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/stores` | List accessible stores |
| GET | `/stores/{storeId}` | Get store |
| GET | `/stores/{storeId}/members` | List store members |
| POST | `/stores/{storeId}/members` | Add existing user to store |
| DELETE | `/stores/{storeId}/members/{memberId}` | Remove member |
| PATCH | `/stores/{storeId}/members/{memberId}` | Update member role |

## Cameras

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/stores/{storeId}/cameras` | List cameras |
| GET | `/stores/{storeId}/cameras/{cameraId}` | Get camera |
| GET | `/stores/{storeId}/cameras/{cameraId}/zones` | Zones the camera's AI pipelines use (display only) |

## Alerts

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/notification-review/{token}` | Stream evidence through an expiring opaque review token |
| GET | `/stores/{storeId}/alerts` | List and filter alerts |
| GET | `/stores/{storeId}/alerts/summary` | Count alerts by status and event type |
| GET | `/stores/{storeId}/alerts/{alertId}` | Get alert detail and evidence |
| POST | `/stores/{storeId}/alerts/{alertId}/acknowledge` | Acknowledge alert |
| POST | `/stores/{storeId}/alerts/{alertId}/confirm` | Confirm alert as a real incident |
| POST | `/stores/{storeId}/alerts/{alertId}/dismiss` | Dismiss alert |
| GET | `/stores/{storeId}/alerts/{alertId}/media/{mediaId}/playback-url` | Create a short-lived playback URL for one clip or snapshot |

## Live

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/live/authorize` | Reverse-proxy check for one /live/ request (forward_auth) |
| POST | `/stores/{storeId}/live-session` | Start a live-view session for this store |

## Notifications

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/stores/{storeId}/notification-deliveries` | List notification delivery history (OWNER only) |
| GET | `/stores/{storeId}/notification-deliveries/{deliveryId}` | Get delivery, attempt log and provider receipts (OWNER only) |
| GET | `/stores/{storeId}/notification-endpoints` | List notification endpoints (OWNER only) |
| POST | `/stores/{storeId}/notification-endpoints` | Create notification endpoint (OWNER only) |
| DELETE | `/stores/{storeId}/notification-endpoints/{endpointId}` | Delete endpoint without delivery history (OWNER only) |
| PATCH | `/stores/{storeId}/notification-endpoints/{endpointId}` | Update notification endpoint (OWNER only) |
| POST | `/stores/{storeId}/notification-endpoints/{endpointId}/test` | Enqueue a TEST-kind delivery (OWNER only) |
| GET | `/stores/{storeId}/notification-rules` | List notification rules (OWNER only) |
| POST | `/stores/{storeId}/notification-rules` | Create notification rule (OWNER only) |
| DELETE | `/stores/{storeId}/notification-rules/{ruleId}` | Delete rule without delivery history (OWNER only) |
| PATCH | `/stores/{storeId}/notification-rules/{ruleId}` | Update notification rule (OWNER only) |
| GET | `/stores/{storeId}/notification-rules/{ruleId}/channels` | List rule channels ordered by priority (OWNER only) |
| POST | `/stores/{storeId}/notification-rules/{ruleId}/channels` | Add a channel route to a rule (OWNER only) |
| DELETE | `/stores/{storeId}/notification-rules/{ruleId}/channels/{channelId}` | Delete channel without delivery history (OWNER only) |
| PATCH | `/stores/{storeId}/notification-rules/{ruleId}/channels/{channelId}` | Update channel priority, delay or enabled flag (OWNER only) |
| GET | `/webhooks/whatsapp` | Verify the Meta WhatsApp webhook |
| POST | `/webhooks/whatsapp` | Receive signed WhatsApp delivery receipts |

## Health

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Health check |

## Notification behaviour

Delivery, retry, fallback and WhatsApp template rules are in [notification-design.md](notification-design.md)
and [whatsapp-emergency-template.md](whatsapp-emergency-template.md). Notification configuration routes
require the store `owner`; endpoint responses mask destinations and only report whether a credential is
configured. The API turns new AI alerts into deliveries itself (`internal/notifications/dispatcher.go`);
there is no ingestion endpoint.
