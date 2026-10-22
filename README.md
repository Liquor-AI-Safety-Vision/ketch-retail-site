# Liquor Store Security

Video loss prevention for a single retail store. Turns CCTV recordings into
short reviewable clips, sends urgent ones to the owner over Telegram or
WhatsApp, and records the owner's decision on each alert.

The system reports events for a human to judge. It does not classify anyone as
a criminal and does not contact emergency services.

## Screenshots

| Overview | Alerts | Cameras |
|---|---|---|
| ![Overview](docs/images/overview.png) | ![Alerts](docs/images/alerts.png) | ![Cameras](docs/images/cameras.png) |

## Features

- Owner dashboard with alert triage, camera grid and video review, in light and dark themes.
- Alert ingestion API for an external AI service, idempotent per source event.
- Emergency notifications over Telegram with WhatsApp fallback.
- Delivery worker with leases, retry, backoff, deduplication and an append-only attempt log.
- Expiring signed links for evidence playback; only the token hash is stored.
- Store-scoped RBAC, Argon2id password hashing and rotating refresh tokens.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript, Vite, Material UI, TanStack Query |
| Backend | Go 1.26.8, `net/http`, pgx, JWT, Argon2id |
| Database | PostgreSQL with embedded SQL migrations |
| Providers | Telegram Bot API, WhatsApp Cloud API |
| Tracking | YOLO26n and ByteTrack, generated offline |

## Project structure

```
apps/
  api-go/          Go API, worker and migrations
  web/             React dashboard
docs/              Design documents, security audit, runbooks
scripts/           Go toolchain helpers, tracking generator
```

## Repositories and hosting

| Repository | What to run |
|---|---|
| [thinhEden/LS-backend](https://github.com/thinhEden/LS-backend) (this repo, formerly `qthinhbk/Liquor-store`) | Go API, database migrations and the standalone dashboard in `apps/web` |
| [gaztrabisme/ketch-retail-site](https://github.com/gaztrabisme/ketch-retail-site) | Ketch landing page and its integrated dashboard |

For backend development and AI integration, start with this repo. Run only one
dashboard at a time on port 5173. The standalone dashboard uses `/dashboard`,
`/alerts`, `/cameras` and `/login`; the Ketch site uses hash routes such as
`/#/alerts` and `/#/login`.

The current deployment layout is frontend on Cloudflare Pages
(`https://ketchenterprise.net`), Go API on Render
(`https://liquor-store-api-7tq2.onrender.com`) and PostgreSQL on Neon. Local
development does not require those hosting accounts. No Cloudflare Tunnel is
configured in this repo; account-level routes must be checked in Cloudflare.

## Requirements

- Node.js 22.12+ (or a newer supported release) and pnpm 11.9.0, pinned by
  `packageManager`.
- Go 1.26.8 or newer, enforced by `go.mod`. The helpers in `scripts/` accept a
  portable SDK at `.tools/go-1.26.8`.
- PostgreSQL 14 or newer.
- Git LFS. Sample clips are stored as LFS objects; without it a clone receives
  pointer files and video playback fails.

## Installation

The `pnpm` backend scripts use Windows PowerShell. On macOS/Linux, use the direct
Go commands below and run `pnpm dev:web` in another terminal.

### 1. Install dependencies and configure a development database

```powershell
git clone https://github.com/thinhEden/LS-backend.git
cd LS-backend
git lfs install
git lfs pull
pnpm install
Copy-Item apps/api-go/.env.example apps/api-go/.env
Copy-Item apps/web/.env.example apps/web/.env
```

For an existing checkout, keep its `.env` files and compare them with the examples
instead of overwriting them. If you received the private environment ZIP, copy
`api-go.env` to `apps/api-go/.env` and `web.env` to `apps/web/.env`, then compare
with `.env.example` for optional settings. Its `ketch-retail-site.env` belongs at
the root of the separate Ketch frontend repo, not here. Read the ZIP's
`VARIABLES.md`: its backend values are a local snapshot, the JWT signing value is
a placeholder, and shared database/provider access is not an isolated local setup.

Set these values in `apps/api-go/.env` before running any database command:

- `DATABASE_URL`: a **development** PostgreSQL database. For local PostgreSQL, use
  `postgresql://USER:PASSWORD@localhost:5432/liquor_store_dev?sslmode=disable`.
  For a separate Neon development branch, use its pooled URL with `sslmode=require`.
- `DIRECT_URL`: the same local URL, or the direct/unpooled URL of that Neon branch.
  Both URLs must point at the same development database.
- `JWT_ACCESS_SECRET`: generate your own key with
  `node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))"`.
- Temporarily set `REGISTER_ENABLED=true` to create the first local owner/store.

Keep `NOTIFICATION_WORKER_ENABLED=false` and provider credentials blank for the
initial run. An empty development database needs a role allowed to create the
`citext` extension. See [the environment reference](docs/environment.md) for every
variable, where to obtain it and which values should be shared privately.

### 2. Migrate, then create the first local owner and store

```powershell
pnpm db:migrate
pnpm dev
```

In another PowerShell terminal, register against the **local** API. Choose a local
password between 12 and 128 characters; this is not an existing production login.

```powershell
$localPassword = Read-Host 'Choose a local owner password (12-128 characters)' -AsSecureString
$registrationBody = @{
  email = 'owner@example.test'
  password = [System.Net.NetworkCredential]::new('', $localPassword).Password
  displayName = 'Local Owner'
  storeName = 'Local Demo Store'
  storeCode = 'liquor-store-demo'
} | ConvertTo-Json
$session = Invoke-RestMethod -Method Post -Uri 'http://localhost:3000/api/v1/auth/register' -Headers @{ Origin = 'http://localhost:5173' } -ContentType 'application/json' -Body $registrationBody
$session.user.stores | Select-Object storeId, role
```

Then run `pnpm db:seed` from the repo root. Seed expects the store code
`liquor-store-demo` to exist; it does **not** create an owner or store. Set
`SEED_STORE_CODE` when using a different store code. It updates fixture rows, so
use it only against the development database. Set `REGISTER_ENABLED=false` and
restart the API after the local account has been created.

On macOS/Linux, copy examples with `cp`, then run `go run ./cmd/migrate`,
`go run ./cmd/api` and, after registration, `go run ./cmd/seed` from `apps/api-go`.
Run `pnpm dev:web` from the repo root in a separate terminal.

### 3. Open the dashboard and check the API

| Service | URL |
|---|---|
| Dashboard | `http://localhost:5173` |
| API | `http://localhost:3000/api/v1` |
| API health | `http://localhost:3000/api/v1/health` |
| Swagger UI (optional) | `http://localhost:3000/docs`, only with `SWAGGER_ENABLED=true` |

Swagger is disabled in the example configuration. Keep it disabled in production.
Open `/login` and sign in with the local account to view database-backed alerts;
the default public demo is client-side sample data and does not verify an AI event.

## Configuration

Set values in `apps/api-go/.env`. The file is git-ignored and must stay that way.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Pooled connection used at runtime |
| `DIRECT_URL` | Unpooled connection used for migrations |
| `JWT_ACCESS_SECRET` | Access token signing key |
| `WEB_ORIGIN` | Only origin accepted on state-changing requests |
| `TRUST_PROXY`, `TRUSTED_PROXY_CIDRS` | Forwarded headers are read only from these proxies |
| `AI_INGEST_TOKEN` | Bearer token for the alert ingestion route |
| `NOTIFICATION_WORKER_ENABLED` | Starts the delivery worker |
| `NOTIFICATION_CREDENTIAL_BINDINGS` | Store, provider and account each endpoint may use |
| `TELEGRAM_*`, `WHATSAPP_*` | Provider credentials and destinations |

`AI_INGEST_TOKEN` is blank and `NOTIFICATION_WORKER_ENABLED=false` in the example.
The AI route is not mounted until a token is set; the notification worker stays
off. See [all environment variables](docs/environment.md) and the
[AI integration walkthrough](docs/ai-integration.md).

## Scripts

| Command | Action |
|---|---|
| `pnpm dev` | Runs API and dashboard together |
| `pnpm build` | Builds both applications |
| `pnpm check` | Frontend typecheck and Go tests (database/provider E2E tests are opt-in) |
| `pnpm db:migrate` | Applies migrations through `DIRECT_URL` |
| `pnpm db:seed` | Loads sample data, safe to repeat |

## API

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/v1/auth/login` | Owner sign-in |
| `POST` | `/api/v1/auth/refresh` | Rotate refresh token |
| `GET` | `/api/v1/stores/{storeId}/alerts` | Alert queue |
| `POST` | `/api/v1/stores/{storeId}/alerts/{alertId}/resolve` | Confirm a real incident |
| `POST` | `/api/v1/stores/{storeId}/alerts/{alertId}/dismiss` | Mark a false alarm |
| `GET` | `/api/v1/stores/{storeId}/alerts/{alertId}/evidence/{evidenceId}/playback-url` | Signed playback URL |
| `GET` | `/api/v1/stores/{storeId}/cameras` | Camera list |
| `GET` | `/api/v1/stores/{storeId}/notification-deliveries` | Delivery audit |
| `POST` | `/api/v1/internal/ai/alerts` | Alert ingestion |
| `POST` | `/api/v1/webhooks/whatsapp` | Signed delivery receipts |
| `GET` | `/api/v1/notification-review/{token}` | Expiring evidence link |

Full contract in [docs/api-contract.md](docs/api-contract.md).

## Sample data

`pnpm db:seed` loads 18 camera feeds, 6 detection zones and 20 video-backed
alerts, with 12 alerts left in the review queue. Kitchen and Whole store top
view (Stream 2) start offline. Rows are matched by stable reference, so
repeated runs update rather than duplicate. `SEED_STORE_CODE` targets a
different store.

The dashboard opens on this sample data without a sign-in. Signing in at
`/login` switches it to the live API.

Bounding boxes arrive two ways. Most clips carry per-frame coordinates that the
browser draws during playback; regenerate them with
`scripts/generate-alert-tracks.py` after installing
`scripts/requirements-tracking.txt`. Emergency clips already have boxes rendered
into the frames, carry no tracking data, and are played untouched.

## Documentation

| Document | Contents |
|---|---|
| [Environment reference](docs/environment.md) | Required, generated and privately shared configuration |
| [AI integration](docs/ai-integration.md) | Local ingestion request, IDs, video evidence and retry semantics |
| [Database design](docs/database-design.md) | Schema, relationships, retention |
| [API contract](docs/api-contract.md) | Endpoints, payloads, error shapes |
| [Authentication](docs/auth-design.md) | Sessions, refresh rotation, RBAC |
| [Notification design](docs/notification-design.md) | Outbox, worker, retry, fallback |
| [WhatsApp template](docs/whatsapp-emergency-template.md) | Template and parameters |
| [Notification runbook](docs/emergency-notification-production.md) | Production procedure and diagnosis |
| [Security hardening](docs/security-hardening.md) | Deployment checklist |
| [Security audit](docs/security-audit-2026-09-05.md) | Findings, 05/09/2026 |
| [Security fixes](docs/security-fixes-2026-09-05.md) | Patches and measured rollout |
| [Streaming design](docs/streaming-design.md) | Planned near-real-time ingest |

## Limitations

- WhatsApp delivery returns Meta error `131042`. The account's `health_status`
  attributes it to business verification rather than billing. See the runbook.
- Live RTSP ingest is designed but not implemented. The dashboard plays recorded
  footage only.
- Detection accuracy is not guaranteed. Every alert requires human review.
