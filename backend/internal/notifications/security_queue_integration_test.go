package notifications

import (
	"context"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/liquor-store/security-api/internal/testdb"
)

func TestSecurityQueuePrioritizesAlerts(t *testing.T) {
	db := testdb.New(t)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	var err error
	storeID, endpointID, alertID, ruleID, channelID := "store_queue", uuid.NewString(), uuid.NewString(), uuid.NewString(), uuid.NewString()
	exec := func(q string, args ...any) {
		t.Helper()
		if _, err := db.Exec(ctx, q, args...); err != nil {
			t.Fatal(err)
		}
	}
	testdb.Store(t, db, storeID, "Queue test", "Asia/Ho_Chi_Minh")
	testdb.Camera(t, db, "cam_queue", storeID, "Counter", "counter")
	exec(`INSERT INTO "notification_endpoints" ("id","storeId","provider","label","destinationRef","credentialRef","config","updatedAt") VALUES ($1,$2,'TELEGRAM','Test','test-only','env://TELEGRAM_BOT_TOKEN','{}',NOW())`, endpointID, storeID)
	tx, err := db.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	first, err := EnqueueTestTx(ctx, tx, TestDeliveryInput{StoreID: storeID, EndpointID: endpointID, RequestID: uuid.NewString()})
	if err != nil {
		_ = tx.Rollback(ctx)
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	exec(`UPDATE "notification_deliveries" SET "availableAt"=NOW()-interval '1 hour',"createdAt"=NOW()-interval '1 hour' WHERE "id"=$1`, first.ID)
	testdb.Alert(t, db, alertID, storeID, "cam_queue", "weapon_v1", "weapon_detected", "critical", time.Now(), nil)
	exec(`INSERT INTO "notification_rules" ("id","storeId","name","minimumSeverity","alertTypes","cooldownSeconds","updatedAt") VALUES ($1,$2,'Emergency','critical',ARRAY['weapon_detected'],0,NOW())`, ruleID, storeID)
	exec(`INSERT INTO "notification_rule_channels" ("id","storeId","ruleId","endpointId","priority","fallbackDelaySeconds","updatedAt") VALUES ($1,$2,$3,$4,1,0,NOW())`, channelID, storeID, ruleID, endpointID)
	tx, err = db.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	deliveries, err := EnqueueAlertTx(ctx, tx, AlertNotificationInput{StoreID: storeID, AlertID: alertID, AlertType: "weapon_detected", Severity: "critical", DetectedAt: time.Now()})
	if err != nil {
		_ = tx.Rollback(ctx)
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if len(deliveries) != 1 {
		t.Fatalf("expected one alert job, got %d", len(deliveries))
	}
	worker := NewWorker(db, slog.New(slog.NewTextHandler(io.Discard, nil)), nil, nil, WorkerOptions{BatchSize: 1, LeaseDuration: 15 * time.Second})
	claims, err := worker.claim(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(claims) != 1 || claims[0].ID != deliveries[0].ID || claims[0].Kind != DeliveryKindAlert {
		t.Fatalf("older TEST displaced ALERT: %+v", claims)
	}
	// No sender is configured or invoked by this test.
}
