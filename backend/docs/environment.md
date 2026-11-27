# Environment variables

Each service keeps its own ignored `.env` beside its code, copied from the
tracked example in the same folder:

| File | Read by | Holds |
| --- | --- | --- |
| `ai/.env` | the AI service (`run_split_pipeline.py --storage-mode cloud-db`, `tools/seed_cloud_store.py`) | `DATABASE_URL`, `R2_*` |
| `backend/.env` | this API, from its working directory `backend/` | the same `DATABASE_URL` and `R2_*`, plus `DIRECT_URL`, JWT, providers |
| `frontend/.env` | Vite | `VITE_API_BASE_URL` only |

`ai/.env` and `backend/.env` must point at the **same** Neon database and R2
bucket: the AI service writes stores, cameras, alerts and clips there and this
API reads them. `DIRECT_URL` is the same Neon endpoint without `-pooler`.
Existing process environment variables take precedence. Restart after editing.
(The AI service still reads a legacy `<repo>/.env` as a fallback; `ai/.env` wins.)

The privately supplied ZIP (2026-10-22) predates the merge: its `api-go.env`
points at the old API's own Neon project, not the AI service's, so only its
JWT TTLs and Telegram/WhatsApp values carry over; `AI_INGEST_TOKEN` no longer
exists. Its `JWT_ACCESS_SECRET` is a placeholder: generate a fresh key.

For a first local run, fill the two database URLs (a database the AI service has
written stores and cameras to) and generate a JWT signing key. Telegram and
WhatsApp credentials are unnecessary. Create an account with `cmd/create-owner`
(or enable registration briefly: it links a new owner to an existing store).
Outbound notifications are a separate opt-in feature.

On the deployment server, `deploy/bin/run-api.sh` sets `PORT`, `NODE_ENV=production`,
`LIVE_VIEW_MODE`, `PUBLIC_API_BASE_URL`, `WEB_ORIGIN`, `TRUST_PROXY` and
`TRUSTED_PROXY_CIDRS` from `deploy/liquor.env`; `backend/.env` there holds only secrets.

## Core API and database

| Variable | Local setup / source | Private? |
|---|---|---|
| `NODE_ENV` | `development` locally; `production` on a hosted API. This Go app also uses this name. | No |
| `PORT` | `3000`. | No |
| `WEB_ORIGIN` | Exact browser origin, initially `http://localhost:5173`. `127.0.0.1` is a different origin; choose one consistently. In production it must be HTTPS, except a loopback origin reached through an SSH tunnel. | No |
| `DATABASE_URL` | Runtime connection to your development PostgreSQL database; Neon pooled connection if using a development branch. | **Yes**, includes credentials |
| `DIRECT_URL` | Direct/unpooled connection to the **same** database for migrations; may equal the local runtime URL. If blank, migration falls back to `DATABASE_URL`. | **Yes** |
| `JWT_ACCESS_SECRET` | Generate a separate random key for each environment; at least 32 characters in production. | **Yes** |
| `JWT_ACCESS_TTL` | Positive Go duration, default `15m`. | No |
| `JWT_ISSUER` | `liquor-store-security-api`; must stay consistent for issuing/verifying tokens. | No |
| `JWT_AUDIENCE` | `liquor-store-owner-dashboard`; same consistency requirement. | No |
| `REFRESH_TOKEN_TTL_DAYS` | Positive integer, default `30`. | No |
| `REGISTER_ENABLED` | `false`; temporarily `true` to let a new owner sign up for an existing store. Prefer `cmd/create-owner`. | No |
| `MEMBER_MANAGEMENT_ENABLED` | `false`; enable when developing store-member management. | No |
| `SWAGGER_ENABLED` | `false` in the example; set `true` for local `/docs` and `/docs-json`. | No |
| `TRUST_PROXY` | `false` locally. Only enable behind verified trusted ingress. | No |
| `TRUSTED_PROXY_CIDRS` | Blank locally. Explicit comma-separated CIDRs if trusting a proxy; do not guess or copy another environment's ranges. | No |
| `LIVE_VIEW_MODE` | Blank disables live view. `test` or `production` (the deployment's MODE) enables `/live-session` and the reverse proxy's `/live/authorize` check. | No |

Generate a key, then paste it into your ignored `.env`:

```sh
node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))"
```

Run this independently for `JWT_ACCESS_SECRET` and, when needed,
`WHATSAPP_WEBHOOK_VERIFY_TOKEN`. These are different secrets.
Do not regenerate an existing shared environment's keys just to run locally.

## Shared with the AI service

| Variable | Local setup / source | Private? |
|---|---|---|
| `R2_ENDPOINT`, `R2_BUCKET` | The same Cloudflare R2 bucket the AI service uploads clips and snapshots to (`ai/.env`). | Endpoint includes the account id |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Read access is enough. Required for media playback and the notification worker. | **Yes** |

There is no ingestion endpoint or AI token: the AI service writes alerts straight into
the shared database and R2, and the API reads them (`internal/notifications/dispatcher.go`
turns new alerts into notifications).

## Delivery worker and media playback

| Variable | Local setup / source | Private? |
|---|---|---|
| `NOTIFICATION_WORKER_ENABLED` | `false` for a local review or initial AI integration. `true` starts outbound delivery. | No |
| `NOTIFICATION_CREDENTIAL_BINDINGS` | `[]` initially. Exact store/provider/account/credential-ref allowlist, matched against endpoints in the database. Required and nonempty with the worker enabled. | No token values; contains internal IDs |
| `NOTIFICATION_POLL_INTERVAL` | Positive duration, default `2s`. | No |
| `NOTIFICATION_LEASE_DURATION` | Default `45s`; must exceed `10s` with the worker enabled. | No |
| `NOTIFICATION_BATCH_SIZE` | Integer `1`–`100`, default `10`. | No |
| `NOTIFICATION_TEST_STORE_ID` | store_id of a controlled test store; used by opted-in provider/production tests, not required by normal API startup. | Internal ID |
| `NOTIFICATION_MAX_ALERT_AGE` | Default `15m`. Older alerts are never notified (e.g. everything already in the database when the worker first starts). | No |
| `NOTIFICATION_CLIP_WAIT` | Default `90s`. How long an alert waits for its clip before being sent without one. | No |
| `PUBLIC_API_BASE_URL` | Origin that browsers and notification recipients reach the API on; playback and review links are built from it. HTTPS in production (loopback excepted). | No |
| `SECURE_VIDEO_LINK_TTL` | Positive duration, default `15m`. | No |

Example binding shape:

```json
[{"storeId":"store_test_01","provider":"TELEGRAM","providerAccountRef":"","credentialRef":"env://TELEGRAM_BOT_TOKEN"}]
```

Tokens stay in environment variables. Database endpoints contain refs such as
`env://TELEGRAM_BOT_TOKEN`, never raw tokens. Current runtime supports the
allowlisted `env://` refs.
Enabling the worker also requires `PUBLIC_API_BASE_URL`, the `R2_*` variables and
matching database endpoints, rules and channels. Filling provider variables alone does not configure
routing. For optional delivery tests use a dedicated test recipient and the
[notification runbook](emergency-notification-production.md), not a copied
production database.

## Telegram and WhatsApp (optional)

| Variable | Source / purpose | Private? |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Test bot token from its owner/BotFather; runtime sender credential. | **Yes** |
| `TELEGRAM_CHAT_ID` | Destination of a test chat; read by provisioning/test commands, not a global runtime route. | Private destination |
| `WHATSAPP_ACCESS_TOKEN` | Meta system-user token for the intended account; runtime sender credential. | **Yes** |
| `WHATSAPP_PHONE_NUMBER_ID` | Meta sender's Phone Number ID; not its displayed phone number. Provisioning/test input. | Account ID |
| `WHATSAPP_WABA_ID` | Meta WhatsApp Business Account ID; provisioning/test input. | Account ID |
| `WHATSAPP_RECIPIENT_PHONE` | Opted-in test recipient in E.164 format; provisioning/test input. | Private destination |
| `WHATSAPP_TEST_VIDEO_URL` | Public HTTPS sample MP4 for opted-in provider tests. No local file paths or private footage. | No |
| `WHATSAPP_OPT_IN_CAPTURED_AT` | Actual recipient consent timestamp in RFC3339, used in endpoint provisioning. | Consent record |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | Generated token, identical in the backend and Meta webhook setup. | **Yes** |
| `WHATSAPP_APP_SECRET` | App Secret of the Meta app receiving delivery webhooks; validates HMAC signatures. | **Yes** |

`WHATSAPP_WEBHOOK_VERIFY_TOKEN` and `WHATSAPP_APP_SECRET` must either both be
blank or both be configured. Do not set only one: configuration validation rejects
it. Provider IDs and consent/destination fields become endpoint configuration in
the database; changing an environment value alone does not update an existing
endpoint. The last recorded production WhatsApp blocker was business verification;
see the runbook for the measured status rather than assuming delivery works.

## Frontend

| Variable | Value | Private? |
|---|---|---|
| `VITE_API_BASE_URL` (`frontend/.env`) | `/api/v1`. Vite proxies `/api` to `http://localhost:3000` in development; in deployment Caddy serves both on one origin. | No; included in browser code |

## What to share separately

Share repository access and these examples first. A colleague can create a local
database, local owner account and JWT key without production credentials.
For an approved shared environment, hand over only the values needed for that
environment through a private channel/password manager: database connection
strings, signing keys, R2 keys, provider tokens, webhook secrets and any test
destinations. Specify the environment alongside each
value. Keep actual values out of README, Git, screenshots and `VITE_*` variables.
