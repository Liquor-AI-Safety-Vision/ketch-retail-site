-- One row per alert the notification dispatcher has considered, whether or
-- not any rule matched. The AI service writes alerts straight into the shared
-- database, so there is no ingestion request to hang notifications off; the
-- dispatcher polls for alerts without a row here instead. Claiming an alert
-- is the INSERT itself (ON CONFLICT DO NOTHING), so several API instances can
-- poll the same database without notifying twice.
CREATE TABLE "notification_alert_scans" (
  "alertId" TEXT PRIMARY KEY REFERENCES alerts(alert_id) ON DELETE CASCADE ON UPDATE CASCADE,
  "storeId" TEXT NOT NULL,
  "mediaId" TEXT,
  "deliveriesQueued" INTEGER NOT NULL DEFAULT 0,
  "scannedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX "notification_alert_scans_store_idx" ON "notification_alert_scans"("storeId", "scannedAt");
