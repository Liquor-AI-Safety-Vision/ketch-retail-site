package server

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/joho/godotenv"
	"github.com/liquor-store/security-api/internal/config"
)

// One controlled alert through the real deployment, exactly the way an alert
// travels in production: written into the shared database as the AI service's
// CloudAlertSink writes it, picked up by the deployed API's dispatcher, and
// sent by its worker to a real Telegram endpoint.
//
// Opt-in and explicit: RUN_PRODUCTION_E2E=1, and the store and camera to use
// are named (PRODUCTION_E2E_STORE_ID, PRODUCTION_E2E_CAMERA_ID) rather than
// guessed. PRODUCTION_E2E_CLIP_KEY may name an R2 object that already exists
// so the message carries a playable review link. The alert row stays as an
// audit record (deliveries reference it), marked controlledTest in metadata.
func TestProductionEmergencyNotificationE2E(t *testing.T) {
	if os.Getenv("RUN_PRODUCTION_E2E") != "1" {
		t.Skip("set RUN_PRODUCTION_E2E=1 to create exactly one controlled production alert")
	}
	_ = godotenv.Load("../../.env")
	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	storeID := strings.TrimSpace(os.Getenv("PRODUCTION_E2E_STORE_ID"))
	cameraID := strings.TrimSpace(os.Getenv("PRODUCTION_E2E_CAMERA_ID"))
	if storeID == "" || cameraID == "" {
		t.Fatal("PRODUCTION_E2E_STORE_ID and PRODUCTION_E2E_CAMERA_ID are required")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
	defer cancel()
	pool, err := pgxpool.New(ctx, cfg.DatabaseURL)
	if err != nil {
		t.Fatalf("connect database: %v", err)
	}
	defer pool.Close()

	alertID := uuid.NewString()
	now := time.Now()
	if _, err := pool.Exec(ctx, `INSERT INTO alerts (alert_id, store_id, camera_id, run_id, pipeline_type, event_type, severity, status, opened_at, reason_codes_json, metadata_json)
VALUES ($1,$2,$3,NULL,'production_e2e','weapon_detected','critical','open',$4,'["CONTROLLED_PRODUCTION_TEST"]','{"controlledTest": true}')`,
		alertID, storeID, cameraID, isoTime(now)); err != nil {
		t.Fatalf("write controlled alert: %v", err)
	}
	if key := strings.TrimSpace(os.Getenv("PRODUCTION_E2E_CLIP_KEY")); key != "" {
		if _, err := pool.Exec(ctx, `INSERT INTO alert_media (media_id, alert_id, kind, path, mime_type, started_at, ended_at, metadata_json) VALUES ($1,$2,'clip',$3,'video/mp4',$4,$5,'{}')`,
			uuid.NewString(), alertID, key, isoTime(now.Add(-15*time.Second)), isoTime(now.Add(15*time.Second))); err != nil {
			t.Fatalf("write controlled clip row: %v", err)
		}
	}

	deadline := time.Now().Add(3 * time.Minute)
	for {
		var queued bool
		var sent, active, failed int
		err = pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM "notification_alert_scans" WHERE "alertId"=$1),
 count(*) FILTER (WHERE d."status"='SENT'),
 count(*) FILTER (WHERE d."status" IN ('PENDING','PROCESSING','RETRY_SCHEDULED','WAITING_FALLBACK')),
 count(*) FILTER (WHERE d."status"='FAILED')
FROM (SELECT 1) one LEFT JOIN "notification_deliveries" d ON d."alertId"=$1`, alertID).Scan(&queued, &sent, &active, &failed)
		if err != nil {
			t.Fatal(err)
		}
		if sent > 0 && active == 0 {
			break
		}
		if queued && sent+active+failed == 0 {
			t.Fatal("the dispatcher considered the alert but no rule matched weapon_detected/critical")
		}
		if failed > 0 && active == 0 {
			t.Fatal("all production delivery routes reached a terminal failure")
		}
		if time.Now().After(deadline) {
			t.Fatal("timed out waiting for the deployed dispatcher and worker (is NOTIFICATION_WORKER_ENABLED=true there?)")
		}
		time.Sleep(2 * time.Second)
	}
	t.Log("production E2E passed: AI-shaped alert dispatched and sent; identifiers were not printed")
}
