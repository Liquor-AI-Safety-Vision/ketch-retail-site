package migrations

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

// The AI service owns the schema; this package only carries a copy so the API
// can bring up an empty database. A copy that drifts would create tables the
// AI service then writes to with different columns -- caught here instead.
func TestEmbeddedAISchemaMatchesAI(t *testing.T) {
	source := filepath.Join("..", "..", "..", "ai", "sql", "postgres_schema.sql")
	want, err := os.ReadFile(source)
	if os.IsNotExist(err) {
		t.Skip("ai/ is not beside backend/ (backend copied alone); nothing to compare against")
	}
	if err != nil {
		t.Fatal(err)
	}
	if string(want) != AISchema() {
		t.Fatalf("internal/migrations/ai_schema.sql differs from %s; run `go generate ./internal/migrations`", source)
	}
}

// Runs against DATABASE_URL inside a throwaway Postgres schema, so it never
// touches the tables already there.
func TestApplyBuildsOnTheAISchema(t *testing.T) {
	if os.Getenv("RUN_INTEGRATION_TESTS") != "1" {
		t.Skip("set RUN_INTEGRATION_TESTS=1 with DATABASE_URL to run")
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	conn, err := pgx.Connect(ctx, os.Getenv("DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(context.Background())

	schema := fmt.Sprintf("migtest_%d", time.Now().UnixNano())
	mustExec(t, ctx, conn, `CREATE SCHEMA `+schema)
	t.Cleanup(func() { _, _ = conn.Exec(context.Background(), `DROP SCHEMA `+schema+` CASCADE`) })
	mustExec(t, ctx, conn, `SET search_path TO `+schema)

	// The AI service gets there first in a real deployment: its tables and a
	// row in each exist before the API ever runs.
	mustExec(t, ctx, conn, AISchema())
	mustExec(t, ctx, conn, `INSERT INTO stores (store_id, display_name, timezone) VALUES ('store_a','A','Asia/Ho_Chi_Minh'),('store_b','B','Asia/Ho_Chi_Minh')`)
	mustExec(t, ctx, conn, `INSERT INTO cameras (camera_id, store_id, display_name, camera_type, source_type, source_uri) VALUES ('cam_a','store_a','Counter','counter','rtsp','rtsp://redacted')`)
	mustExec(t, ctx, conn, `INSERT INTO alerts (alert_id, store_id, camera_id, pipeline_type, event_type, severity, status, opened_at) VALUES ('alert_a','store_a','cam_a','counter_cash_theft_v1','suspected_cash_concealment','high','open','2026-11-27T01:02:03.456789+00:00')`)

	for i := 0; i < 2; i++ {
		if _, err := Apply(ctx, conn); err != nil {
			t.Fatalf("apply #%d: %v", i+1, err)
		}
	}
	var status string
	if err := conn.QueryRow(ctx, `SELECT status FROM alerts WHERE alert_id='alert_a'`).Scan(&status); err != nil || status != "open" {
		t.Fatalf("existing AI row changed: status=%q err=%v", status, err)
	}

	// A delivery may only reference an alert of its own store.
	mustExec(t, ctx, conn, `INSERT INTO "notification_rules" ("id","storeId","name") VALUES ('00000000-0000-0000-0000-000000000001','store_b','r')`)
	mustExec(t, ctx, conn, `INSERT INTO "notification_endpoints" ("id","storeId","provider","label","destinationRef","credentialRef") VALUES ('00000000-0000-0000-0000-000000000002','store_b','TELEGRAM','e','d','env://X')`)
	mustExec(t, ctx, conn, `INSERT INTO "notification_rule_channels" ("id","storeId","ruleId","endpointId") VALUES ('00000000-0000-0000-0000-000000000003','store_b','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002')`)
	_, err = conn.Exec(ctx, `INSERT INTO "notification_deliveries" ("id","storeId","alertId","ruleId","endpointId","ruleChannelId","dedupeKey","provider","templateVersion","payload") VALUES ('00000000-0000-0000-0000-000000000004','store_b','alert_a','00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000003','k','TELEGRAM','v1','{}')`)
	if err == nil {
		t.Fatal("a delivery in store_b was allowed to reference store_a's alert")
	}
	if _, err := conn.Exec(ctx, `INSERT INTO "notification_rules" ("id","storeId","name","minimumSeverity") VALUES ('00000000-0000-0000-0000-000000000005','store_a','r','HIGH')`); err == nil {
		t.Fatal("a severity outside the AI vocabulary was accepted")
	}
}

// A database the AI service created with an older copy of its schema (the
// shared Neon database was one: no review fields, no owner status/role)
// keeps those tables -- CREATE TABLE IF NOT EXISTS does not touch them -- so
// the schema's ALTERs must bring every column the API reads up to date.
func TestApplyUpgradesAnOlderAISchema(t *testing.T) {
	if os.Getenv("RUN_INTEGRATION_TESTS") != "1" {
		t.Skip("set RUN_INTEGRATION_TESTS=1 with DATABASE_URL to run")
	}
	legacy, err := os.ReadFile(filepath.Join("testdata", "ai_schema_before_review_fields.sql"))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	conn, err := pgx.Connect(ctx, os.Getenv("DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(context.Background())
	schema := fmt.Sprintf("migold_%d", time.Now().UnixNano())
	mustExec(t, ctx, conn, `CREATE SCHEMA `+schema)
	t.Cleanup(func() { _, _ = conn.Exec(context.Background(), `DROP SCHEMA `+schema+` CASCADE`) })
	mustExec(t, ctx, conn, `SET search_path TO `+schema)

	mustExec(t, ctx, conn, string(legacy))
	mustExec(t, ctx, conn, `INSERT INTO stores (store_id, display_name, timezone) VALUES ('store_a','A','Asia/Ho_Chi_Minh')`)
	mustExec(t, ctx, conn, `INSERT INTO cameras (camera_id, store_id, display_name, camera_type, source_type, source_uri) VALUES ('cam_a','store_a','Counter','counter','rtsp','rtsp://redacted')`)
	mustExec(t, ctx, conn, `INSERT INTO alerts (alert_id, store_id, camera_id, pipeline_type, event_type, severity, status, opened_at) VALUES ('alert_a','store_a','cam_a','storage_intrusion_v1','unknown_entry','warning','open','2026-09-18T01:43:27.729080+00:00')`)
	mustExec(t, ctx, conn, `INSERT INTO owners (owner_id, display_name, email, password_hash, created_at, updated_at) VALUES ('owner_a','A','a@example.com','x','t','t')`)
	mustExec(t, ctx, conn, `INSERT INTO owner_stores (owner_id, store_id, created_at) VALUES ('owner_a','store_a','t')`)

	for i := 0; i < 2; i++ {
		if _, err := Apply(ctx, conn); err != nil {
			t.Fatalf("apply #%d over the older schema: %v", i+1, err)
		}
	}
	var status, ownerStatus, role string
	var reviewedBy, reviewedAt, reviewNote *string
	if err := conn.QueryRow(ctx, `SELECT a.status, a.reviewed_by, a.reviewed_at, a.review_note, o.status, os.role
FROM alerts a, owners o JOIN owner_stores os USING (owner_id) WHERE a.alert_id='alert_a'`).
		Scan(&status, &reviewedBy, &reviewedAt, &reviewNote, &ownerStatus, &role); err != nil {
		t.Fatalf("columns the API reads are missing after the upgrade: %v", err)
	}
	if status != "open" || reviewedBy != nil || reviewedAt != nil || reviewNote != nil || ownerStatus != "active" || role != "owner" {
		t.Fatalf("upgrade changed or mis-defaulted existing rows: %s %v %v %v %s %s", status, reviewedBy, reviewedAt, reviewNote, ownerStatus, role)
	}
	mustExec(t, ctx, conn, `UPDATE alerts SET status='confirmed', reviewed_by='owner_a', reviewed_at='t', review_note='n' WHERE alert_id='alert_a'`)
	if _, err := conn.Exec(ctx, `UPDATE alerts SET reviewed_by='owner_missing' WHERE alert_id='alert_a'`); err == nil {
		t.Fatal("reviewed_by accepted an owner that does not exist")
	}
}

func mustExec(t *testing.T, ctx context.Context, conn *pgx.Conn, sql string) {
	t.Helper()
	if _, err := conn.Exec(ctx, sql); err != nil {
		t.Fatalf("%v\n%s", err, sql)
	}
}
