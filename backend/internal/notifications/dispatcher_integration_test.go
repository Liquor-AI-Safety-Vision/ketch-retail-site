package notifications

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/liquor-store/security-api/internal/testdb"
)

// Alerts arrive the way the AI service writes them -- the alert row first,
// its clip later -- and the dispatcher turns each into deliveries once.
func TestDispatcherQueuesAIAlertsOnce(t *testing.T) {
	db := testdb.New(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	const store = "store_test_01"
	testdb.Store(t, db, store, "Store test 01", "Asia/Ho_Chi_Minh")
	testdb.Camera(t, db, "st01_storage", store, "Storage", "storage")
	testdb.Camera(t, db, "st01_counter", store, "Counter", "counter")
	ruleID, endpointID, channelID := uuid.NewString(), uuid.NewString(), uuid.NewString()
	mustExec(t, db, `INSERT INTO "notification_rules" ("id","storeId","name","minimumSeverity","alertTypes") VALUES ($1,$2,'Everything','warning','{}')`, ruleID, store)
	mustExec(t, db, `INSERT INTO "notification_endpoints" ("id","storeId","provider","label","destinationRef","credentialRef") VALUES ($1,$2,'TELEGRAM','Owner','12345','env://TELEGRAM_BOT_TOKEN')`, endpointID, store)
	mustExec(t, db, `INSERT INTO "notification_rule_channels" ("id","storeId","ruleId","endpointId") VALUES ($1,$2,$3,$4)`, channelID, store, ruleID, endpointID)

	now := time.Date(2026, 11, 27, 12, 0, 0, 0, time.UTC)
	d := NewAlertDispatcher(db, slog.New(slog.NewTextHandler(io.Discard, nil)), DispatcherOptions{
		BatchSize: 10, MaxAlertAge: 15 * time.Minute, ClipWait: 90 * time.Second})
	d.now = func() time.Time { return now }

	// 1. A storage entry, just opened, clip not uploaded yet: held back.
	label := "Unknown"
	fresh := uuid.NewString()
	testdb.Alert(t, db, fresh, store, "st01_storage", "storage_intrusion_v1", "unknown_entry", "warning", now.Add(-5*time.Second), &label)
	if n, err := d.RunOnce(ctx); err != nil || n != 0 {
		t.Fatalf("alert without its clip was dispatched early: n=%d err=%v", n, err)
	}
	// 2. The clip lands (CloudAlertSink queues it ~post_s later): dispatched with it.
	clip := uuid.NewString()
	start, end := now.Add(-20*time.Second), now.Add(10*time.Second)
	testdb.Media(t, db, clip, fresh, "clip", "alerts/"+fresh+"/evidence.mp4", "video/mp4", &start, &end)
	testdb.Media(t, db, uuid.NewString(), fresh, "snapshot", "alerts/"+fresh+"/snapshot.jpg", "image/jpeg", &start, nil)
	if n, err := d.RunOnce(ctx); err != nil || n != 1 {
		t.Fatalf("alert with clip: n=%d err=%v", n, err)
	}
	payload := deliveryPayload(t, db, fresh)
	if payload.MediaID != clip || payload.AlertType != "unknown_entry" || payload.Severity != "warning" ||
		payload.CameraName != "Storage" || payload.Description != AlertDescription("unknown_entry", "Unknown") {
		t.Fatalf("payload = %+v", payload)
	}
	// 3. Never twice.
	if n, err := d.RunOnce(ctx); err != nil || n != 0 {
		t.Fatalf("second pass re-dispatched: n=%d err=%v", n, err)
	}

	// 4. Past ClipWait with no clip: dispatched without one.
	late := uuid.NewString()
	testdb.Alert(t, db, late, store, "st01_counter", "counter_cash_theft_v1", "suspected_cash_concealment", "high", now.Add(-2*time.Minute), nil)
	// 5. Older than MaxAlertAge (e.g. already in the database at first deploy): never.
	old := uuid.NewString()
	testdb.Alert(t, db, old, store, "st01_counter", "counter_cash_theft_v1", "suspected_cash_concealment", "high", now.Add(-time.Hour), nil)
	// 6. Dismissed before the dispatcher saw it: never.
	dismissed := uuid.NewString()
	testdb.Alert(t, db, dismissed, store, "st01_counter", "counter_cash_theft_v1", "suspected_cash_concealment", "high", now.Add(-3*time.Minute), nil)
	mustExec(t, db, `UPDATE alerts SET status='dismissed' WHERE alert_id=$1`, dismissed)
	if n, err := d.RunOnce(ctx); err != nil || n != 1 {
		t.Fatalf("late/old/dismissed: n=%d err=%v", n, err)
	}
	if p := deliveryPayload(t, db, late); p.MediaID != "" || p.AlertType != "suspected_cash_concealment" {
		t.Fatalf("late payload = %+v", p)
	}
	for _, id := range []string{old, dismissed} {
		var count int
		if err := db.QueryRow(ctx, `SELECT count(*) FROM "notification_deliveries" WHERE "alertId"=$1`, id).Scan(&count); err != nil || count != 0 {
			t.Fatalf("alert %s got %d deliveries", id, count)
		}
	}

	// 7. A rule below the alert's severity, or for another type, does not
	// match -- but the alert is still marked as considered.
	mustExec(t, db, `UPDATE "notification_rules" SET "minimumSeverity"='high' WHERE "id"=$1`, ruleID)
	quiet := uuid.NewString()
	testdb.Alert(t, db, quiet, store, "st01_storage", "storage_intrusion_v1", "unknown_entry", "warning", now.Add(-2*time.Minute), nil)
	if n, err := d.RunOnce(ctx); err != nil || n != 1 {
		t.Fatalf("non-matching alert: n=%d err=%v", n, err)
	}
	var queued int
	if err := db.QueryRow(ctx, `SELECT "deliveriesQueued" FROM "notification_alert_scans" WHERE "alertId"=$1`, quiet).Scan(&queued); err != nil || queued != 0 {
		t.Fatalf("non-matching alert queued=%d err=%v", queued, err)
	}

	// 8. Two API instances polling at once claim each alert once.
	race := uuid.NewString()
	testdb.Alert(t, db, race, store, "st01_counter", "counter_cash_theft_v1", "suspected_cash_concealment", "high", now.Add(-2*time.Minute), nil)
	other := NewAlertDispatcher(db, slog.New(slog.NewTextHandler(io.Discard, nil)), d.options)
	other.now = d.now
	var wg sync.WaitGroup
	results := make([]int, 2)
	for i, dispatcher := range []*AlertDispatcher{d, other} {
		wg.Add(1)
		go func(i int, dispatcher *AlertDispatcher) {
			defer wg.Done()
			n, err := dispatcher.RunOnce(ctx)
			if err != nil {
				t.Errorf("concurrent dispatcher: %v", err)
			}
			results[i] = n
		}(i, dispatcher)
	}
	wg.Wait()
	if results[0]+results[1] != 1 {
		t.Fatalf("concurrent claims = %v, want exactly one", results)
	}
	var count int
	if err := db.QueryRow(ctx, `SELECT count(*) FROM "notification_deliveries" WHERE "alertId"=$1`, race).Scan(&count); err != nil || count != 1 {
		t.Fatalf("raced alert has %d deliveries", count)
	}
}

func deliveryPayload(t *testing.T, db *pgxpool.Pool, alertID string) RenderPayload {
	t.Helper()
	var raw []byte
	if err := db.QueryRow(context.Background(), `SELECT "payload" FROM "notification_deliveries" WHERE "alertId"=$1`, alertID).Scan(&raw); err != nil {
		t.Fatalf("delivery for %s: %v", alertID, err)
	}
	var payload RenderPayload
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatal(err)
	}
	return payload
}

func mustExec(t *testing.T, db *pgxpool.Pool, sql string, args ...any) {
	t.Helper()
	if _, err := db.Exec(context.Background(), sql, args...); err != nil {
		t.Fatalf("%v\n%s", err, sql)
	}
}
