# Notifications in production: runbook

How a real alert becomes a Telegram (and optionally WhatsApp) message on the `deploy/`
server, and how to switch it on and prove it once without spamming anyone.

## The chain

1. The AI service writes the alert row into Neon, its snapshot, and ~post_s seconds later its
   clip into R2 (`ai/src/infra/cloud_alert_sink.py`).
2. The API's dispatcher (`internal/notifications/dispatcher.go`) claims each new alert once,
   waits up to `NOTIFICATION_CLIP_WAIT` for the clip, matches the store's rules and enqueues
   deliveries in the same transaction as the claim.
3. The worker sends the primary route, records an immutable attempt per provider request,
   retries with backoff, and activates the fallback route if the primary fails; unused
   fallbacks end `CANCELLED`.
4. The message's review link opens a short-lived `/api/v1/notification-review/{token}` page
   for that alert's clip; the dashboard deep link is `/#/alerts?alertId=<uuid>`.
5. WhatsApp delivery receipts arrive only through the HMAC-verified webhook
   `PUBLIC_URL/api/v1/webhooks/whatsapp` and show in the delivery audit.

No step contacts police or any emergency service.

## Switch it on

On the server, `backend/.env` (secrets) needs `TELEGRAM_BOT_TOKEN`, the `R2_*` variables and,
for WhatsApp, `WHATSAPP_ACCESS_TOKEN` plus both webhook secrets. Then:

```bash
# 1. routing for the store: Telegram primary, WhatsApp fallback if its variables are set,
#    one rule (default: every AI alert, severity warning and up)
cd backend
APPLY_PRODUCTION_NOTIFICATION_CONFIG=1 STORE_ID=store_test_01 \
TELEGRAM_CHAT_ID=<chat id> ../deploy/dist/liquor-configure-notifications

# 2. in backend/.env: allow exactly that store/provider/credential, and start the worker
NOTIFICATION_CREDENTIAL_BINDINGS=[{"storeId":"store_test_01","provider":"TELEGRAM","providerAccountRef":"","credentialRef":"env://TELEGRAM_BOT_TOKEN"}]
NOTIFICATION_WORKER_ENABLED=true

# 3. restart the API (it migrates first, then starts the dispatcher and worker)
deploy/liquorctl restart
```

`PUBLIC_API_BASE_URL` comes from `PUBLIC_URL` in `deploy/liquor.env`; links in messages are
built from it, so it must be an address the recipient can open (HTTPS on a real domain — an
SSH-tunnel `localhost` URL only works on the operator's own machine).

Only alerts newer than `NOTIFICATION_MAX_ALERT_AGE` (15 min) are ever notified, so switching
the worker on never replays the backlog already in the database.

**In test mode the AI produces real alerts continuously from the looped footage.** Turning the
worker on there sends a message for every one of them; use a dedicated test chat, or a rule
restricted with `NOTIFICATION_RULE_ALERT_TYPES` / `NOTIFICATION_RULE_MIN_SEVERITY`.

## Verify once

```bash
cd backend
RUN_PRODUCTION_READINESS=1 go test -run '^TestProductionNotificationDatabaseReadiness$' -count=1 -v ./internal/server
```

is read-only. One controlled alert through the deployed chain (written the way the AI writes it,
picked up by the deployed dispatcher, sent by its worker):

```bash
RUN_PRODUCTION_E2E=1 PRODUCTION_E2E_STORE_ID=store_test_01 PRODUCTION_E2E_CAMERA_ID=st01_counter \
go test -run '^TestProductionEmergencyNotificationE2E$' -count=1 -v ./internal/server
```

It prints no destination or provider identifiers and leaves the alert (marked `controlledTest`
in its metadata) and its attempts as audit records.

## WhatsApp status

WhatsApp uses the Meta-approved `emergency_security_alert` template
([whatsapp-emergency-template.md](whatsapp-emergency-template.md)). The last measurement
(06/09/2026) found sends blocked by Meta business verification (`health_status` entity
`BUSINESS`, error `141010`). Before relying on WhatsApp, check:

```bash
curl -H "Authorization: Bearer $WHATSAPP_ACCESS_TOKEN" \
  "https://graph.facebook.com/v25.0/$WHATSAPP_WABA_ID?fields=health_status"
```

and fix the entity it names. If Meta later reports error `131042`, do not resend blindly: it
covers several distinct eligibility problems.

## Rollback

1. `NOTIFICATION_WORKER_ENABLED=false` and `deploy/liquorctl restart`: no new sends; audit
   history stays.
2. Never delete deliveries, attempts or provider events: they are the incident audit trail.
