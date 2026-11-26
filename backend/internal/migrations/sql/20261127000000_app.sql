-- Tables the Go API owns. Everything the AI service writes -- stores, cameras,
-- owners, owner_stores, alerts, alert_media, persons, face_embeddings -- is
-- defined by ai/sql/postgres_schema.sql, which the migrator applies first
-- (embedded as ai_schema.sql). Those tables use snake_case TEXT ids
-- ('store_test_01', 'owner_1a2b...', uuid4 strings for alerts); every
-- reference to them from here is TEXT to match.
--
-- This replaces the five migrations of the standalone Go repo. That repo's
-- database (users, stores with UUID ids, alert_evidence...) is not carried
-- over: the deployment's database is the AI service's.

CREATE TYPE "NotificationProvider" AS ENUM ('TELEGRAM', 'WHATSAPP');
CREATE TYPE "NotificationDeliveryKind" AS ENUM ('ALERT', 'TEST');
CREATE TYPE "NotificationDeliveryStatus" AS ENUM ('WAITING_FALLBACK', 'PENDING', 'PROCESSING', 'RETRY_SCHEDULED', 'SENT', 'FAILED', 'CANCELLED');
CREATE TYPE "NotificationAttemptStatus" AS ENUM ('SUCCEEDED', 'FAILED');
CREATE TYPE "NotificationProviderReceiptStatus" AS ENUM ('SENT', 'DELIVERED', 'READ', 'FAILED');

-- ---------------------------------------------------------------- sessions
CREATE TABLE "refresh_sessions" (
  "id" UUID PRIMARY KEY,
  "ownerId" TEXT NOT NULL REFERENCES owners(owner_id) ON DELETE CASCADE ON UPDATE CASCADE,
  "tokenHash" TEXT NOT NULL UNIQUE,
  "expiresAt" TIMESTAMPTZ NOT NULL,
  "revokedAt" TIMESTAMPTZ,
  "ipAddress" TEXT,
  "userAgent" TEXT,
  "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX "refresh_sessions_owner_expires_idx" ON "refresh_sessions"("ownerId", "expiresAt");

-- ------------------------------------------------------- review audit log
-- Immutable decision snapshots, kept independently of resource deletion:
-- no cascading foreign keys, so removing an alert, membership or account
-- does not erase who decided what. Status values are alerts.status values
-- ('open' | 'acknowledged' | 'confirmed' | 'dismissed').
CREATE TABLE "alert_status_history" (
  "id" UUID PRIMARY KEY,
  "storeId" TEXT NOT NULL,
  "alertId" TEXT NOT NULL,
  "actorId" TEXT NOT NULL,
  "actorRole" TEXT NOT NULL,
  "previousStatus" TEXT NOT NULL,
  "newStatus" TEXT NOT NULL,
  "previousActorId" TEXT,
  "previousReviewedAt" TEXT,
  "previousNote" TEXT,
  "note" TEXT,
  "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX "alert_status_history_alert_idx" ON "alert_status_history" ("storeId", "alertId", "createdAt");
CREATE FUNCTION reject_alert_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Alert status history is append-only' USING ERRCODE = '42501';
END;
$$;
CREATE TRIGGER "alert_status_history_immutable" BEFORE UPDATE OR DELETE ON "alert_status_history"
FOR EACH ROW EXECUTE FUNCTION reject_alert_history_mutation();
CREATE TRIGGER "alert_status_history_no_truncate" BEFORE TRUNCATE ON "alert_status_history"
FOR EACH STATEMENT EXECUTE FUNCTION reject_alert_history_mutation();

-- ----------------------------------------------------------- notifications
CREATE TABLE "notification_endpoints" (
  "id" UUID PRIMARY KEY,
  "storeId" TEXT NOT NULL,
  "provider" "NotificationProvider" NOT NULL,
  "label" TEXT NOT NULL,
  "providerAccountRef" TEXT,
  "destinationRef" TEXT NOT NULL,
  "credentialRef" TEXT NOT NULL,
  "config" JSONB NOT NULL DEFAULT '{}'::jsonb,
  "isEnabled" BOOLEAN NOT NULL DEFAULT true,
  "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "notification_endpoints_config_object_check" CHECK (jsonb_typeof("config") = 'object')
);

-- minimumSeverity and alertTypes use the AI service's vocabulary:
-- alerts.severity ('warning' < 'high' < 'critical'; ai/ writes the first two,
-- 'critical' is reserved for the weapon module) and
-- alerts.event_type ('suspected_cash_concealment', 'unknown_entry', ...).
-- An empty alertTypes array matches every type.
CREATE TABLE "notification_rules" (
  "id" UUID PRIMARY KEY,
  "storeId" TEXT NOT NULL,
  "name" TEXT NOT NULL,
  "minimumSeverity" TEXT NOT NULL DEFAULT 'critical',
  "alertTypes" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  "cooldownSeconds" INTEGER NOT NULL DEFAULT 0,
  "isEnabled" BOOLEAN NOT NULL DEFAULT true,
  "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "notification_rules_cooldown_check" CHECK ("cooldownSeconds" BETWEEN 0 AND 86400),
  CONSTRAINT "notification_rules_severity_check" CHECK ("minimumSeverity" IN ('warning', 'high', 'critical'))
);

CREATE TABLE "notification_rule_channels" (
  "id" UUID PRIMARY KEY,
  "storeId" TEXT NOT NULL,
  "ruleId" UUID NOT NULL,
  "endpointId" UUID NOT NULL,
  "priority" SMALLINT NOT NULL DEFAULT 1,
  "fallbackDelaySeconds" INTEGER NOT NULL DEFAULT 0,
  "isEnabled" BOOLEAN NOT NULL DEFAULT true,
  "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "notification_rule_channels_priority_check" CHECK ("priority" >= 1),
  CONSTRAINT "notification_rule_channels_fallback_delay_check" CHECK ("fallbackDelaySeconds" BETWEEN 0 AND 86400)
);

CREATE TABLE "notification_deliveries" (
  "id" UUID PRIMARY KEY,
  "deliveryKind" "NotificationDeliveryKind" NOT NULL DEFAULT 'ALERT',
  "storeId" TEXT NOT NULL,
  "alertId" TEXT,
  "ruleId" UUID,
  "endpointId" UUID NOT NULL,
  "ruleChannelId" UUID,
  "dedupeKey" TEXT NOT NULL,
  "status" "NotificationDeliveryStatus" NOT NULL DEFAULT 'PENDING',
  "provider" "NotificationProvider" NOT NULL,
  "priority" SMALLINT NOT NULL DEFAULT 1,
  "fallbackDelaySeconds" INTEGER NOT NULL DEFAULT 0,
  "templateVersion" TEXT NOT NULL,
  "payload" JSONB NOT NULL,
  "providerMessageId" TEXT,
  "attemptCount" INTEGER NOT NULL DEFAULT 0,
  "maxAttempts" INTEGER NOT NULL DEFAULT 5,
  "availableAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "lockedAt" TIMESTAMPTZ,
  "lockedUntil" TIMESTAMPTZ,
  "lastAttemptAt" TIMESTAMPTZ,
  "sentAt" TIMESTAMPTZ,
  "lastErrorCode" TEXT,
  "lastErrorMessage" TEXT,
  "providerStatus" "NotificationProviderReceiptStatus",
  "providerStatusAt" TIMESTAMPTZ,
  "deliveredAt" TIMESTAMPTZ,
  "readAt" TIMESTAMPTZ,
  "providerFailedAt" TIMESTAMPTZ,
  "providerErrorCode" TEXT,
  "requestedById" TEXT,
  "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "notification_deliveries_payload_object_check" CHECK (jsonb_typeof("payload") = 'object'),
  CONSTRAINT "notification_deliveries_attempt_count_check" CHECK ("attemptCount" >= 0),
  CONSTRAINT "notification_deliveries_max_attempts_check" CHECK ("maxAttempts" BETWEEN 1 AND 20),
  CONSTRAINT "notification_deliveries_attempt_bounds_check" CHECK ("attemptCount" <= "maxAttempts"),
  CONSTRAINT "notification_deliveries_template_version_check" CHECK (char_length(btrim("templateVersion")) > 0),
  CONSTRAINT "notification_deliveries_kind_links_check" CHECK (
    ("deliveryKind" = 'ALERT' AND "alertId" IS NOT NULL AND "ruleId" IS NOT NULL AND "ruleChannelId" IS NOT NULL)
    OR ("deliveryKind" = 'TEST' AND "alertId" IS NULL AND "ruleId" IS NULL AND "ruleChannelId" IS NULL)
  ),
  CONSTRAINT "notification_deliveries_lease_status_check" CHECK (
    ("status" = 'PROCESSING' AND "lockedAt" IS NOT NULL AND "lockedUntil" IS NOT NULL AND "lockedUntil" > "lockedAt")
    OR ("status" <> 'PROCESSING' AND "lockedAt" IS NULL AND "lockedUntil" IS NULL)
  )
);

CREATE TABLE "notification_attempts" (
  "id" UUID PRIMARY KEY,
  "deliveryId" UUID NOT NULL,
  "attemptNumber" INTEGER NOT NULL,
  "status" "NotificationAttemptStatus" NOT NULL,
  "startedAt" TIMESTAMPTZ NOT NULL,
  "finishedAt" TIMESTAMPTZ NOT NULL,
  "durationMs" INTEGER NOT NULL,
  "responseStatus" INTEGER,
  "providerMessageId" TEXT,
  "errorCode" TEXT,
  "errorMessage" TEXT,
  "responseMetadata" JSONB NOT NULL DEFAULT '{}'::jsonb,
  "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "notification_attempts_attempt_number_check" CHECK ("attemptNumber" >= 1),
  CONSTRAINT "notification_attempts_duration_check" CHECK ("durationMs" >= 0),
  CONSTRAINT "notification_attempts_response_metadata_object_check" CHECK (jsonb_typeof("responseMetadata") = 'object')
);

CREATE TABLE "notification_provider_events" (
  "id" UUID PRIMARY KEY,
  "deliveryId" UUID NOT NULL,
  "provider" "NotificationProvider" NOT NULL,
  "providerMessageId" TEXT NOT NULL,
  "status" "NotificationProviderReceiptStatus" NOT NULL,
  "eventAt" TIMESTAMPTZ NOT NULL,
  "errorCode" TEXT,
  "receivedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "notification_provider_events_message_id_check"
    CHECK (char_length(btrim("providerMessageId")) BETWEEN 1 AND 256),
  CONSTRAINT "notification_provider_events_error_code_check"
    CHECK ("errorCode" IS NULL OR char_length("errorCode") BETWEEN 1 AND 120)
);

-- Short-lived, media-scoped review links. Only a SHA-256 token digest is
-- stored; the bearer token itself never enters PostgreSQL, logs, delivery
-- payloads, or notification attempt metadata. mediaId is an alert_media row
-- (a clip) belonging to alertId.
CREATE TABLE "notification_video_links" (
  "id" UUID PRIMARY KEY,
  "tokenHash" BYTEA NOT NULL UNIQUE,
  "storeId" TEXT NOT NULL,
  "alertId" TEXT NOT NULL,
  "mediaId" TEXT NOT NULL,
  "deliveryId" UUID,
  "expiresAt" TIMESTAMPTZ NOT NULL,
  "revokedAt" TIMESTAMPTZ,
  "accessCount" INTEGER NOT NULL DEFAULT 0,
  "lastAccessedAt" TIMESTAMPTZ,
  "createdAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT "notification_video_links_expiry_check" CHECK ("expiresAt" > "createdAt"),
  CONSTRAINT "notification_video_links_access_count_check" CHECK ("accessCount" >= 0)
);

-- Composite unique keys let child tables pin a parent row to one store, so a
-- delivery can never reference a rule, endpoint or channel from another store.
-- The alerts/alert_media equivalents are in the AI schema.
CREATE UNIQUE INDEX "notification_endpoints_id_store_key" ON "notification_endpoints"("id", "storeId");
CREATE UNIQUE INDEX "notification_rules_id_store_key" ON "notification_rules"("id", "storeId");
CREATE UNIQUE INDEX "notification_rule_channels_id_store_key" ON "notification_rule_channels"("id", "storeId");
CREATE UNIQUE INDEX "notification_rule_channels_id_route_key" ON "notification_rule_channels"("id", "ruleId", "endpointId", "storeId");

CREATE UNIQUE INDEX "notification_endpoints_store_provider_destination_key" ON "notification_endpoints"("storeId", "provider", "destinationRef");
CREATE INDEX "notification_endpoints_store_provider_enabled_idx" ON "notification_endpoints"("storeId", "provider", "isEnabled");
CREATE INDEX "notification_rules_store_enabled_severity_idx" ON "notification_rules"("storeId", "isEnabled", "minimumSeverity");
CREATE UNIQUE INDEX "notification_rule_channels_rule_endpoint_key" ON "notification_rule_channels"("ruleId", "endpointId");
CREATE UNIQUE INDEX "notification_rule_channels_rule_priority_key" ON "notification_rule_channels"("ruleId", "priority");
CREATE UNIQUE INDEX "notification_deliveries_dedupe_key" ON "notification_deliveries"("dedupeKey");
CREATE UNIQUE INDEX "notification_deliveries_alert_rule_endpoint_key" ON "notification_deliveries"("alertId", "ruleId", "endpointId");
CREATE INDEX "notification_deliveries_status_available_idx" ON "notification_deliveries"("status", "availableAt");
CREATE INDEX "notification_deliveries_lease_recovery_idx" ON "notification_deliveries"("lockedUntil") WHERE "status" = 'PROCESSING';
CREATE INDEX "notification_deliveries_alert_idx" ON "notification_deliveries"("alertId");
CREATE INDEX "notification_deliveries_store_created_idx" ON "notification_deliveries"("storeId", "createdAt");
CREATE UNIQUE INDEX "notification_deliveries_provider_message_key" ON "notification_deliveries"("provider", "providerMessageId") WHERE "providerMessageId" IS NOT NULL;
CREATE INDEX "notification_test_store_quota_idx" ON "notification_deliveries" ("storeId", "createdAt") WHERE "deliveryKind"='TEST';
CREATE INDEX "notification_test_user_quota_idx" ON "notification_deliveries" ("requestedById", "createdAt") WHERE "deliveryKind"='TEST';
CREATE INDEX "notification_test_recent_idx" ON "notification_deliveries" ("createdAt") WHERE "deliveryKind"='TEST';
CREATE INDEX "notification_test_pending_idx" ON "notification_deliveries" ("storeId", "endpointId") WHERE "deliveryKind"='TEST' AND "status" IN ('PENDING','PROCESSING','RETRY_SCHEDULED');
CREATE INDEX "notification_priority_ready_idx" ON "notification_deliveries" ((CASE WHEN "deliveryKind"='ALERT' THEN 0 ELSE 1 END), "availableAt", "createdAt", "id") WHERE "status" IN ('PENDING','RETRY_SCHEDULED');
CREATE UNIQUE INDEX "notification_attempts_delivery_number_key" ON "notification_attempts"("deliveryId", "attemptNumber");
CREATE INDEX "notification_attempts_delivery_created_idx" ON "notification_attempts"("deliveryId", "createdAt");
CREATE UNIQUE INDEX "notification_provider_events_dedupe_key" ON "notification_provider_events"("provider", "providerMessageId", "status", "eventAt");
CREATE INDEX "notification_provider_events_delivery_received_idx" ON "notification_provider_events"("deliveryId", "receivedAt");
CREATE INDEX "notification_video_links_expiry_idx" ON "notification_video_links"("expiresAt") WHERE "revokedAt" IS NULL;
CREATE INDEX "notification_video_links_delivery_idx" ON "notification_video_links"("deliveryId");
CREATE INDEX "notification_video_links_alert_idx" ON "notification_video_links"("alertId", "createdAt");

ALTER TABLE "notification_endpoints" ADD CONSTRAINT "notification_endpoints_store_fkey" FOREIGN KEY ("storeId") REFERENCES stores(store_id) ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "notification_rules" ADD CONSTRAINT "notification_rules_store_fkey" FOREIGN KEY ("storeId") REFERENCES stores(store_id) ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "notification_rule_channels" ADD CONSTRAINT "notification_rule_channels_rule_fkey" FOREIGN KEY ("ruleId", "storeId") REFERENCES "notification_rules"("id", "storeId") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "notification_rule_channels" ADD CONSTRAINT "notification_rule_channels_endpoint_fkey" FOREIGN KEY ("endpointId", "storeId") REFERENCES "notification_endpoints"("id", "storeId") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "notification_deliveries" ADD CONSTRAINT "notification_deliveries_alert_fkey" FOREIGN KEY ("alertId", "storeId") REFERENCES alerts(alert_id, store_id) ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "notification_deliveries" ADD CONSTRAINT "notification_deliveries_rule_fkey" FOREIGN KEY ("ruleId", "storeId") REFERENCES "notification_rules"("id", "storeId") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "notification_deliveries" ADD CONSTRAINT "notification_deliveries_endpoint_fkey" FOREIGN KEY ("endpointId", "storeId") REFERENCES "notification_endpoints"("id", "storeId") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "notification_deliveries" ADD CONSTRAINT "notification_deliveries_rule_channel_fkey" FOREIGN KEY ("ruleChannelId", "ruleId", "endpointId", "storeId") REFERENCES "notification_rule_channels"("id", "ruleId", "endpointId", "storeId") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "notification_attempts" ADD CONSTRAINT "notification_attempts_delivery_fkey" FOREIGN KEY ("deliveryId") REFERENCES "notification_deliveries"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "notification_provider_events" ADD CONSTRAINT "notification_provider_events_delivery_fkey" FOREIGN KEY ("deliveryId") REFERENCES "notification_deliveries"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "notification_video_links" ADD CONSTRAINT "notification_video_links_alert_fkey" FOREIGN KEY ("alertId", "storeId") REFERENCES alerts(alert_id, store_id) ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "notification_video_links" ADD CONSTRAINT "notification_video_links_media_fkey" FOREIGN KEY ("mediaId", "alertId") REFERENCES alert_media(media_id, alert_id) ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "notification_video_links" ADD CONSTRAINT "notification_video_links_delivery_fkey" FOREIGN KEY ("deliveryId") REFERENCES "notification_deliveries"("id") ON DELETE CASCADE ON UPDATE CASCADE;
