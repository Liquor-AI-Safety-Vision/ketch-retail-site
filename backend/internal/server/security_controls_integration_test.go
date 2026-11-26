package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/liquor-store/security-api/internal/config"
	"github.com/liquor-store/security-api/internal/notifications"
)

func TestSecurityRegressionPostgreSQL(t *testing.T) {
	pool := newTestDB(t)
	ctx := context.Background()
	ownerID, operatorID, storeID, otherStore := "owner_aaaaaaaaaaaa", "owner_bbbbbbbbbbbb", "store_security", "store_security_other"
	cfg := config.Config{JWTAccessSecret: "security-regression-test-key-not-production", JWTAccessTTL: time.Minute * 15, JWTIssuer: "security-tests", JWTAudience: "security-tests", NotificationCredentialBindings: []notifications.CredentialBinding{{StoreID: storeID, Provider: notifications.ProviderTelegram, CredentialRef: "env://TELEGRAM_BOT_TOKEN"}}}
	api := New(cfg, pool, slog.New(slog.NewTextHandler(io.Discard, nil)))
	server := httptest.NewServer(api.Handler())
	defer server.Close()
	exec := func(t *testing.T, query string, args ...any) {
		t.Helper()
		if _, err := pool.Exec(ctx, query, args...); err != nil {
			t.Fatal(err)
		}
	}
	for _, id := range []string{ownerID, operatorID} {
		exec(t, `INSERT INTO owners (owner_id, display_name, email, password_hash, metadata_json, created_at, updated_at) VALUES ($1,'Security test',$2,'unused-test-hash','{}',$3,$3)`, id, id+"@example.test", isoNow())
	}
	for _, id := range []string{storeID, otherStore} {
		exec(t, `INSERT INTO stores (store_id, display_name, timezone) VALUES ($1,'Security test','Asia/Ho_Chi_Minh')`, id)
		exec(t, `INSERT INTO cameras (camera_id, store_id, display_name, camera_type, source_type, source_uri) VALUES ($1,$2,'Counter','counter','rtsp','rtsp://redacted')`, id+"_cam", id)
	}
	for _, member := range []struct{ id, role string }{{ownerID, roleOwner}, {operatorID, roleOperator}} {
		exec(t, `INSERT INTO owner_stores (owner_id, store_id, role, created_at) VALUES ($1,$2,$3,$4)`, member.id, storeID, member.role, isoNow())
	}
	ownerToken, _ := api.signAccessToken(authUser{ID: ownerID})
	operatorToken, _ := api.signAccessToken(authUser{ID: operatorID})
	request := func(method, path, token string, payload any) (int, []byte) {
		raw, _ := json.Marshal(payload)
		req, _ := http.NewRequest(method, server.URL+"/api/v1"+path, bytes.NewReader(raw))
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		res, err := server.Client().Do(req)
		if err != nil {
			return 0, []byte(err.Error())
		}
		defer res.Body.Close()
		body, _ := io.ReadAll(res.Body)
		return res.StatusCode, body
	}
	expect := func(t *testing.T, method, path, token string, payload any, want int) []byte {
		t.Helper()
		code, body := request(method, path, token, payload)
		if code != want {
			t.Fatalf("%s %s: got %d want %d: %s", method, path, code, want, body)
		}
		return body
	}

	t.Run("stores come from the AI manifest and suspension is immediate", func(t *testing.T) {
		// Nobody creates stores through the API, owner or not: the AI service
		// writes them from its manifest.
		body := map[string]any{"name": "Extra store", "code": "extra"}
		expect(t, "POST", "/stores", operatorToken, body, 405)
		expect(t, "POST", "/stores", ownerToken, body, 405)
		exec(t, `UPDATE owners SET status='suspended' WHERE owner_id=$1`, ownerID)
		for _, route := range []struct {
			method, path string
			body         any
		}{{"GET", "/stores", nil}, {"GET", "/stores/" + storeID + "/alerts", nil}, {"GET", "/users/me", nil}, {"PATCH", "/users/me", map[string]any{"displayName": "changed"}}} {
			expect(t, route.method, route.path, ownerToken, route.body, 401)
		}
		exec(t, `UPDATE owners SET status='active' WHERE owner_id=$1`, ownerID)
		expect(t, "GET", "/stores", ownerToken, nil, 200)
	})

	alertID := uuid.NewString()
	// An alert the AI service wrote and a manager already confirmed.
	exec(t, `INSERT INTO alerts (alert_id, store_id, camera_id, pipeline_type, event_type, severity, status, opened_at, reason_codes_json, metadata_json, reviewed_by, reviewed_at, review_note) VALUES ($1,$2,$3,'counter_cash_theft_v1','suspected_cash_concealment','high','confirmed',$4,'[]','{}',$5,$4,'original manager decision')`, alertID, storeID, storeID+"_cam", isoNow(), ownerID)
	t.Run("terminal decision permission and immutable history", func(t *testing.T) {
		path := "/stores/" + storeID + "/alerts/" + alertID + "/dismiss"
		expect(t, "POST", path, operatorToken, map[string]any{}, 403)
		var status, actor, note string
		if err := pool.QueryRow(ctx, `SELECT status, reviewed_by, review_note FROM alerts WHERE alert_id=$1`, alertID).Scan(&status, &actor, &note); err != nil {
			t.Fatal(err)
		}
		if status != statusConfirmed || actor != ownerID || note != "original manager decision" {
			t.Fatal("blocked decision changed stored data")
		}
		var wg sync.WaitGroup
		codes := make(chan int, 2)
		for i := 0; i < 2; i++ {
			wg.Add(1)
			go func() { defer wg.Done(); code, _ := request("POST", path, ownerToken, map[string]any{}); codes <- code }()
		}
		wg.Wait()
		close(codes)
		counts := map[int]int{}
		for code := range codes {
			counts[code]++
		}
		if counts[201] != 1 || counts[409] != 1 {
			t.Fatalf("concurrent decisions: %v", counts)
		}
		var n int
		if err := pool.QueryRow(ctx, `SELECT count(*) FROM "alert_status_history" WHERE "alertId"=$1 AND "previousStatus"='confirmed' AND "newStatus"='dismissed' AND "previousActorId"=$2 AND "previousNote"='original manager decision' AND "note"='original manager decision' AND "previousReviewedAt" IS NOT NULL`, alertID, ownerID).Scan(&n); err != nil || n != 1 {
			t.Fatalf("audit history: %d %v", n, err)
		}
		if _, err := pool.Exec(ctx, `UPDATE "alert_status_history" SET "note"='tampered' WHERE "alertId"=$1`, alertID); err == nil {
			t.Fatal("audit UPDATE allowed")
		}
		if _, err := pool.Exec(ctx, `DELETE FROM "alert_status_history" WHERE "alertId"=$1`, alertID); err == nil {
			t.Fatal("audit DELETE allowed")
		}
		if _, err := pool.Exec(ctx, `TRUNCATE "alert_status_history"`); err == nil {
			t.Fatal("audit TRUNCATE allowed")
		}
	})

	endpoints := "/stores/" + storeID + "/notification-endpoints"
	endpointPayload := map[string]any{"provider": "TELEGRAM", "label": "Security test", "destinationRef": "test-destination", "credentialRef": "env://JWT_ACCESS_SECRET"}
	t.Run("API credential scope", func(t *testing.T) {
		expect(t, "POST", endpoints, ownerToken, endpointPayload, 403)
		endpointPayload["credentialRef"] = "env://TELEGRAM_BOT_TOKEN"
		endpointPayload["providerAccountRef"] = "unassigned-account"
		expect(t, "POST", endpoints, ownerToken, endpointPayload, 403)
		delete(endpointPayload, "providerAccountRef")
		// Even a store owner cannot borrow the first store's credential assignment.
		exec(t, `INSERT INTO owner_stores (owner_id, store_id, role, created_at) VALUES ($1,$2,'owner',$3)`, ownerID, otherStore, isoNow())
		expect(t, "POST", "/stores/"+otherStore+"/notification-endpoints", ownerToken, endpointPayload, 403)
	})
	var endpoint notificationEndpointResponse
	if err := json.Unmarshal(expect(t, "POST", endpoints, ownerToken, endpointPayload, 201), &endpoint); err != nil {
		t.Fatal(err)
	}
	if err := notifications.CheckCredentialBindings(ctx, pool, cfg.NotificationCredentialBindings); err != nil {
		t.Fatalf("valid binding preflight failed: %v", err)
	}
	if err := notifications.CheckCredentialBindings(ctx, pool, nil); err == nil {
		t.Fatal("worker startup accepted an unbound active endpoint")
	}
	t.Run("test quota is atomic and idempotent", func(t *testing.T) {
		path := endpoints + "/" + endpoint.ID + "/test"
		var wg sync.WaitGroup
		type outcome struct {
			code int
			id   string
		}
		results := make(chan outcome, 12)
		for i := 0; i < 12; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				id := uuid.NewString()
				code, _ := request("POST", path, ownerToken, map[string]any{"requestId": id})
				results <- outcome{code, id}
			}()
		}
		wg.Wait()
		close(results)
		accepted, rejected := 0, 0
		id := ""
		for result := range results {
			switch result.code {
			case 202:
				accepted++
				id = result.id
			case 429:
				rejected++
			default:
				t.Errorf("unexpected quota response %d", result.code)
			}
		}
		if accepted != 1 || rejected != 11 {
			t.Fatalf("admission accepted=%d rejected=%d", accepted, rejected)
		}
		expect(t, "POST", path, ownerToken, map[string]any{"requestId": id}, 202)
		var n int
		if err := pool.QueryRow(ctx, `SELECT count(*) FROM "notification_deliveries" WHERE "storeId"=$1 AND "requestedById"=$2`, storeID, ownerID).Scan(&n); err != nil || n != 1 {
			t.Fatalf("queue count %d %v", n, err)
		}
		// Changing a stored endpoint cannot evade worker policy; API rejects a test
		// immediately, and scoped sender tests cover the final outbound check.
		exec(t, `UPDATE "notification_endpoints" SET "credentialRef"='env://JWT_ACCESS_SECRET' WHERE "id"=$1`, endpoint.ID)
		expect(t, "POST", path, ownerToken, map[string]any{"requestId": uuid.NewString()}, 403)
		expect(t, "PATCH", endpoints+"/"+endpoint.ID, ownerToken, map[string]any{"isEnabled": false}, 200)
	})
	t.Log(fmt.Sprintf("Security regressions verified for isolated store %s", storeID))
}
