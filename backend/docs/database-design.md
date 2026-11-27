# Database design

One PostgreSQL database on Neon, shared by the AI service and this API. Two sets of tables:

- **The AI schema** — `ai/sql/postgres_schema.sql`, the source of truth. The AI service creates
  and upgrades these tables every time it starts, and writes stores, cameras, alerts and their
  media. This API applies the same file (an embedded copy, `internal/migrations/ai_schema.sql`,
  kept identical by a test) before its own migrations, so an empty database can be brought up
  from either side.
- **The API's tables** — `internal/migrations/sql/*.sql`, applied in filename order by
  `cmd/migrate` (and by `deploy/bin/run-api.sh` on every start), recorded in
  `go_schema_migrations`.

`DATABASE_URL` (pooled) serves runtime traffic; `DIRECT_URL` (direct endpoint) runs migrations.

## AI schema (written by `ai/`)

| Table | Rows | Written by the API? |
| --- | --- | --- |
| `stores` | one per manifest store | no |
| `cameras` | one per manifest camera: type, `metadata_json` (pipelines and zones, for display), `last_frame_at` (AI heartbeat, ~30 s), `source_uri` (never returned by the API) | no |
| `pipeline_runs` | one per camera per AI start | no |
| `alerts` | one per event: `pipeline_type`, `event_type`, `severity`, `status`, `opened_at` (UTC isoformat text), identity fields, `reason_codes_json` | only the review fields: `status`, `reviewed_by`, `reviewed_at`, `review_note` |
| `alert_media` | snapshot and clip per alert; `path` is the R2 object key `alerts/<alert_id>/<file>` | no |
| `persons`, `face_embeddings` | staff and watchlist identities | no |
| `owners`, `owner_stores` | dashboard accounts and their role per store (`owner`/`manager`/`operator`) | yes |

Every `CREATE TABLE` is `IF NOT EXISTS`, so a database created by an older copy keeps its old
columns; the `ALTER TABLE … ADD COLUMN IF NOT EXISTS` block at the end of the file brings it up
to date (`internal/migrations/migrate_test.go` upgrades from a real older copy).

Vocabulary is lowercase throughout: statuses `open → acknowledged | confirmed | dismissed`,
severities `warning | high | critical`, event types `suspected_cash_concealment`,
`unknown_entry`, `staff_outside_schedule`, `watchlist_match` (and `weapon_detected`, reserved).

## API tables (`internal/migrations/sql`)

| Table | Purpose |
| --- | --- |
| `refresh_sessions` | hashed, individually revocable refresh tokens per owner |
| `alert_status_history` | append-only before/after snapshot of every review decision (a trigger rejects updates and deletes) |
| `notification_rules`, `notification_endpoints`, `notification_rule_channels` | per-store routing: minimum severity and event types → Telegram/WhatsApp destinations |
| `notification_alert_scans` | one row per alert the dispatcher has considered: the claim that keeps several API instances from notifying twice |
| `notification_deliveries`, `notification_attempts` | the delivery queue (leases, retries, fallbacks) and an immutable record of every provider request |
| `notification_provider_events` | WhatsApp delivery receipts, idempotent by provider message ID |
| `notification_video_links` | SHA-256 digests of expiring review-link tokens and their exact store/alert/media scope |

## Integrity rules

- Every API read and write is scoped by `store_id`, and a caller reaches a store only through
  `owner_stores`; another store's ids answer 404.
- Notification tables reference stores and alerts with composite `(id, storeId)` keys, so a
  delivery cannot point at another store's alert. Alerts with deliveries cannot be hard-deleted.
- Credentials are never stored: `credentialRef` names a server-side secret (`env://…`), bound
  per store/provider by `NOTIFICATION_CREDENTIAL_BINDINGS`.
- Delivery payloads hold owner-safe context and the media id, never a signed URL that could
  expire before a retry; review links are minted at send time.
- Deduplication is per alert, rule and endpoint; emergency rules have no cooldown.

The notification lifecycle is in [notification-design.md](notification-design.md).
