# Environment variables

Start with [the installation steps](../README.md#installation). Copy
`apps/api-go/.env.example` to `apps/api-go/.env` and the root `.env.example` to
`.env`. Actual `.env` files are ignored by Git.
The Go processes load `.env` from their working directory (`apps/api-go`);
existing process environment variables take precedence. Restart after editing.

If using the privately supplied ZIP instead, map `api-go.env` to
`apps/api-go/.env` and `ketch-retail-site.env` to `.env` at the repo root. A
`web.env` file, if present, belongs to the retired standalone dashboard and is
not used. Read its `VARIABLES.md` for values
that are placeholders or unavailable locally. A missing entry uses the code's
default, not necessarily the current production setting. Compare with the tracked
examples and replace database credentials with a development database before
migration or seed. Set a fresh local JWT key if the supplied value is a placeholder.

For a first local run, fill the two development database URLs and generate a
JWT signing key. Telegram, WhatsApp and Cloudflare credentials are unnecessary.
Enable registration only long enough to bootstrap a local owner/store, then
disable it. AI ingestion and outbound notifications are separate opt-in features.

## Core API and database

| Variable | Local setup / source | Private? |
|---|---|---|
| `NODE_ENV` | `development` locally; `production` on a hosted API. This Go app also uses this name. | No |
| `PORT` | `3000` locally. Render supplies its port at runtime. | No |
| `WEB_ORIGIN` | Exact browser origin, initially `http://localhost:5173`. `127.0.0.1` is a different origin; choose one consistently. | No |
| `DATABASE_URL` | Runtime connection to your development PostgreSQL database; Neon pooled connection if using a development branch. | **Yes**, includes credentials |
| `DIRECT_URL` | Direct/unpooled connection to the **same** database for migrations; may equal the local runtime URL. If blank, migration falls back to `DATABASE_URL`. | **Yes** |
| `JWT_ACCESS_SECRET` | Generate a separate random key for each environment; at least 32 characters in production. | **Yes** |
| `JWT_ACCESS_TTL` | Positive Go duration, default `15m`. | No |
| `JWT_ISSUER` | `liquor-store-security-api`; must stay consistent for issuing/verifying tokens. | No |
| `JWT_AUDIENCE` | `liquor-store-owner-dashboard`; same consistency requirement. | No |
| `REFRESH_TOKEN_TTL_DAYS` | Positive integer, default `30`. | No |
| `REGISTER_ENABLED` | `false`; temporarily `true` to bootstrap the local owner/store. | No |
| `MEMBER_MANAGEMENT_ENABLED` | `false`; enable when developing store-member management. | No |
| `SWAGGER_ENABLED` | `false` in the example; set `true` for local `/docs` and `/docs-json`. | No |
| `TRUST_PROXY` | `false` locally. Only enable behind verified trusted ingress. | No |
| `TRUSTED_PROXY_CIDRS` | Blank locally. Explicit comma-separated CIDRs if trusting a proxy; do not guess or copy another environment's ranges. | No |
| `SEED_STORE_CODE` | `liquor-store-demo`; must identify an existing store before seeding. Read by the seed command. | No |

Generate a key, then paste it into your ignored `.env`:

```sh
node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))"
```

Run this independently for `JWT_ACCESS_SECRET` and, when needed,
`AI_INGEST_TOKEN` and `WHATSAPP_WEBHOOK_VERIFY_TOKEN`. These are different secrets.
Do not regenerate an existing shared environment's keys just to run locally.

## AI ingestion

| Variable | Local setup / source | Private? |
|---|---|---|
| `AI_INGEST_TOKEN` | Blank disables the ingestion route. To connect AI, generate at least 32 characters and set the same value in the API and the external AI sender. | **Yes** |

The AI service calls `POST /api/v1/internal/ai/alerts` with
`Authorization: Bearer <AI_INGEST_TOKEN>`. This token is independent of owner
login/JWT and provider tokens. Never put it in a `VITE_*` variable. See the
[request example and evidence contract](ai-integration.md).

## Delivery worker and evidence playback

| Variable | Local setup / source | Private? |
|---|---|---|
| `NOTIFICATION_WORKER_ENABLED` | `false` for a local review or initial AI integration. `true` starts outbound delivery. | No |
| `NOTIFICATION_CREDENTIAL_BINDINGS` | `[]` initially. Exact store/provider/account/credential-ref allowlist, matched against endpoints in the database. Required and nonempty with the worker enabled. | No token values; contains internal IDs |
| `NOTIFICATION_POLL_INTERVAL` | Positive duration, default `2s`. | No |
| `NOTIFICATION_LEASE_DURATION` | Default `45s`; must exceed `10s` with the worker enabled. | No |
| `NOTIFICATION_BATCH_SIZE` | Integer `1`–`100`, default `10`. | No |
| `NOTIFICATION_TEST_STORE_ID` | UUID of a controlled test store; used by opted-in provider/production tests, not required by normal API startup. | Internal ID |
| `PUBLIC_API_BASE_URL` | `http://localhost:3000` locally; externally reachable HTTPS API origin for real notifications. | No |
| `SECURE_VIDEO_LINK_TTL` | Positive duration, default `15m`. | No |
| `EVIDENCE_ORIGIN_BASE_URL` | Blank for initial metadata-only testing. For playback, HTTP(S) origin serving the actual `storageKey` paths; API must reach it. | No |
| `EVIDENCE_ORIGIN_AUTH_TOKEN` | Optional bearer credential if that evidence origin requires authentication. | **Yes** |

Example binding shape (replace the UUID with your development store ID):

```json
[{"storeId":"11111111-1111-4111-8111-111111111111","provider":"TELEGRAM","providerAccountRef":"","credentialRef":"env://TELEGRAM_BOT_TOKEN"}]
```

Tokens stay in environment variables. Database endpoints contain refs such as
`env://TELEGRAM_BOT_TOKEN`, never raw tokens. Current runtime supports the
allowlisted `env://` refs; `render-secret://` is not a working resolver.
Enabling the worker also requires both evidence origins and matching database
endpoints, rules and channels. Filling provider variables alone does not configure
routing. For optional delivery tests use a dedicated test recipient and the
[notification runbook](emergency-notification-production.md), not a copied
production database. Keep outbound delivery off while connecting AI initially.

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

## Frontends and deployment-only variables

| Location / variable | Value or source | Private? |
|---|---|---|
| Frontend: `VITE_API_BASE_URL` | `/api/v1` locally; Vite proxies `/api` to `http://localhost:3000`. Use an absolute API URL only for an intentional cross-origin setup. | No; included in browser code |
| Ketch Pages Function: `API_ORIGIN` | Backend origin, default `https://liquor-store-api-7tq2.onrender.com`; configured on Cloudflare, not in Vite `.env`. | No |
| Ketch Pages Function: `API_TRUSTED_ORIGIN` | Origin forwarded to the API. Must match that deployment's `WEB_ORIGIN`. | No |
| Ketch GitHub Actions: `CLOUDFLARE_API_TOKEN` | Existing Pages deployment credential in GitHub Actions secrets. Not needed for local development. | **Yes** |
| Ketch GitHub Actions: `CLOUDFLARE_ACCOUNT_ID` | Account containing Pages project `ketch-enterprise`. | Account ID |

For the frontend and its deployment, also read the [root README](../README.md).
The Vite dev server uses port 5173; the Go API uses port 3000.

## What to share separately

Share repository access and these examples first. A colleague can create a local
database, local owner account, JWT key and AI token without production credentials.
For an approved shared environment, hand over only the values needed for that
environment through a private channel/password manager: database connection
strings, signing/AI tokens, provider tokens, webhook secrets, evidence-origin
credentials and any test destinations. Specify the environment alongside each
value. Keep actual values out of README, Git, screenshots and `VITE_*` variables.
