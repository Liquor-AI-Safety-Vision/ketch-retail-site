// Package testdb gives database tests their own Postgres schema.
//
// Each call creates a schema on DATABASE_URL, migrates it exactly as
// production is migrated (the AI service's schema, then this API's tables),
// and drops it when the test ends. Tests in different packages -- which `go
// test ./...` runs in parallel -- or in parallel within one package never see
// each other's rows. Only imported from _test.go files.
package testdb

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/liquor-store/security-api/internal/migrations"
)

var seq atomic.Int64

// New skips the test unless RUN_INTEGRATION_TESTS=1 and DATABASE_URL is set.
func New(t *testing.T) *pgxpool.Pool {
	t.Helper()
	if os.Getenv("RUN_INTEGRATION_TESTS") != "1" {
		t.Skip("set RUN_INTEGRATION_TESTS=1 with DATABASE_URL to run database tests")
	}
	base := os.Getenv("DATABASE_URL")
	if base == "" {
		t.Fatal("DATABASE_URL is required for database tests")
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	schema := fmt.Sprintf("t_%d_%d", time.Now().UnixNano(), seq.Add(1))

	admin, err := pgx.Connect(ctx, base)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := admin.Exec(ctx, `CREATE SCHEMA `+schema); err != nil {
		t.Fatal(err)
	}
	_ = admin.Close(ctx)

	scoped, err := withSearchPath(base, schema)
	if err != nil {
		t.Fatal(err)
	}
	conn, err := pgx.Connect(ctx, scoped)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := migrations.Apply(ctx, conn); err != nil {
		t.Fatalf("migrate test schema: %v", err)
	}
	_ = conn.Close(ctx)

	pool, err := pgxpool.New(ctx, scoped)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		pool.Close()
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		if admin, err := pgx.Connect(ctx, base); err == nil {
			_, _ = admin.Exec(ctx, `DROP SCHEMA `+schema+` CASCADE`)
			_ = admin.Close(ctx)
		}
	})
	return pool
}

func withSearchPath(dsn, schema string) (string, error) {
	u, err := url.Parse(dsn)
	if err != nil {
		return "", err
	}
	q := u.Query()
	q.Set("search_path", schema)
	u.RawQuery = q.Encode()
	return u.String(), nil
}

// ISO is the AI service's *_at text format (Python datetime.isoformat() UTC).
func ISO(t time.Time) string { return t.UTC().Format("2006-01-02T15:04:05.000000+00:00") }

// Store writes a store the way the AI service's seeder does.
func Store(t *testing.T, db *pgxpool.Pool, storeID, displayName, timezone string) {
	t.Helper()
	if _, err := db.Exec(context.Background(), `INSERT INTO stores (store_id, display_name, timezone) VALUES ($1,$2,$3)`, storeID, displayName, timezone); err != nil {
		t.Fatal(err)
	}
}

// Camera writes a camera the way the AI service's seeder does.
func Camera(t *testing.T, db *pgxpool.Pool, cameraID, storeID, displayName, cameraType string) {
	t.Helper()
	if _, err := db.Exec(context.Background(), `INSERT INTO cameras (camera_id, store_id, display_name, camera_type, source_type, source_uri) VALUES ($1,$2,$3,$4,'rtsp','rtsp://admin:***@10.0.0.5:554/stream')`,
		cameraID, storeID, displayName, cameraType); err != nil {
		t.Fatal(err)
	}
}

// Alert writes an alert the way CloudAlertSink.create_alert does.
func Alert(t *testing.T, db *pgxpool.Pool, alertID, storeID, cameraID, pipelineType, eventType, severity string, openedAt time.Time, identityLabel *string) {
	t.Helper()
	if _, err := db.Exec(context.Background(), `INSERT INTO alerts (alert_id, store_id, camera_id, run_id, pipeline_type, event_type, severity, status, opened_at, closed_at, identity_person_id, identity_label, identity_score, reason_codes_json, metadata_json) VALUES ($1,$2,$3,NULL,$4,$5,$6,'open',$7,NULL,NULL,$8,NULL,'["TEST_REASON"]','{"store_id": "x"}')`,
		alertID, storeID, cameraID, pipelineType, eventType, severity, ISO(openedAt), identityLabel); err != nil {
		t.Fatal(err)
	}
}

// Media writes an alert_media row the way CloudAlertSink.add_alert_media does:
// path is the R2 object key alerts/<alert_id>/<file>.
func Media(t *testing.T, db *pgxpool.Pool, mediaID, alertID, kind, path, mimeType string, startedAt, endedAt *time.Time) {
	t.Helper()
	var start, end any
	if startedAt != nil {
		start = ISO(*startedAt)
	}
	if endedAt != nil {
		end = ISO(*endedAt)
	}
	if _, err := db.Exec(context.Background(), `INSERT INTO alert_media (media_id, alert_id, kind, path, mime_type, started_at, ended_at, metadata_json) VALUES ($1,$2,$3,$4,$5,$6,$7,'{}')`,
		mediaID, alertID, kind, path, mimeType, start, end); err != nil {
		t.Fatal(err)
	}
}
