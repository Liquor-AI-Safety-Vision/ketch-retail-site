package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/liquor-store/security-api/internal/config"
	"github.com/liquor-store/security-api/internal/notifications"
	"github.com/liquor-store/security-api/internal/testdb"
)

type notifyTestClient struct {
	api   *Server
	http  *http.Client
	base  string
	pool  *pgxpool.Pool
	token string
}

type notificationTestCredentials struct {
	Email     string
	Password  string
	StoreName string
	StoreID   string
}

func newNotificationTestCredentials(label string) notificationTestCredentials {
	suffix := fmt.Sprintf("%d%s", time.Now().UnixNano(), label)
	return notificationTestCredentials{
		Email:     "go-notify-" + suffix + "@example.test",
		Password:  "Notify-only-" + suffix,
		StoreName: "Go Notify Store " + label,
		StoreID:   "store_notify_" + suffix,
	}
}

func newNotifyTestServer(t *testing.T, membersEnabled bool) *notifyTestClient {
	t.Helper()
	pool := newTestDB(t)
	cfg := config.Config{
		JWTAccessSecret: "notification-test-secret-that-is-long-enough",
		JWTAccessTTL:    15 * time.Minute,
		JWTIssuer:       "liquor-store-security-api",
		JWTAudience:     "liquor-store-owner-dashboard",
		RefreshTTLDays:  30,
		WebOrigin:       "http://localhost:5173",
	}
	cfg.RegisterEnabled = true
	cfg.MembersEnabled = membersEnabled
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	api := New(cfg, pool, logger)
	testServer := httptest.NewServer(api.Handler())
	t.Cleanup(testServer.Close)
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	return &notifyTestClient{http: &http.Client{Jar: jar, Timeout: 30 * time.Second}, base: testServer.URL, pool: pool, api: api}
}

func (c *notifyTestClient) registerNotificationUser(t *testing.T, creds notificationTestCredentials) (string, string) {
	t.Helper()
	// The store exists first because the AI service creates stores.
	if _, err := c.pool.Exec(context.Background(), `INSERT INTO stores (store_id, display_name, timezone) VALUES ($1,$2,'America/Chicago')`, creds.StoreID, creds.StoreName); err != nil {
		t.Fatal(err)
	}
	if _, err := c.pool.Exec(context.Background(), `INSERT INTO cameras (camera_id, store_id, display_name, camera_type, source_type, source_uri) VALUES ($1,$2,'Whole store','whole_store','rtsp','rtsp://redacted')`, creds.StoreID+"_cam", creds.StoreID); err != nil {
		t.Fatal(err)
	}
	var registered sessionResponse
	doJSON(t, c.http, http.MethodPost, c.base+"/api/v1/auth/register", "", map[string]any{
		"email": creds.Email, "password": creds.Password, "displayName": "Go Notify Owner", "storeId": creds.StoreID,
	}, http.StatusCreated, &registered)
	if registered.User.ID == "" || len(registered.User.Stores) == 0 || registered.User.Stores[0].StoreID == "" {
		t.Fatalf("register response is missing user or store identifiers: %s", mustMarshalNotification(t, registered))
	}
	storeID := registered.User.Stores[0].StoreID
	// Assign fixture credentials from trusted server configuration, separately
	// from the HTTP endpoint payload under test.
	c.api.config.NotificationCredentialBindings = append(c.api.config.NotificationCredentialBindings,
		notifications.CredentialBinding{StoreID: storeID, Provider: notifications.ProviderTelegram, CredentialRef: "env://TELEGRAM_BOT_TOKEN"},
		notifications.CredentialBinding{StoreID: storeID, Provider: notifications.ProviderTelegram, ProviderAccountRef: "@KetchRetailSecurityBot", CredentialRef: "env://TELEGRAM_BOT_TOKEN"},
		notifications.CredentialBinding{StoreID: storeID, Provider: notifications.ProviderWhatsApp, ProviderAccountRef: "111122223333", CredentialRef: "env://WHATSAPP_ACCESS_TOKEN"},
	)
	return registered.User.ID, storeID
}

func (c *notifyTestClient) login(t *testing.T, email, password string) string {
	t.Helper()
	var loggedIn sessionResponse
	doJSON(t, c.http, http.MethodPost, c.base+"/api/v1/auth/login", "", map[string]any{
		"email": email, "password": password,
	}, http.StatusCreated, &loggedIn)
	return loggedIn.AccessToken
}

func TestNotificationConfigurationFlow(t *testing.T) {
	client := newNotifyTestServer(t, true)

	owner := newNotificationTestCredentials("")
	operator := newNotificationTestCredentials("-op")
	_, ownerStoreID := client.registerNotificationUser(t, owner)
	client.token = client.login(t, owner.Email, owner.Password)
	base := client.base

	telegramChat := "5500" + fmt.Sprintf("%06d", time.Now().UnixNano()%1000000)
	var endpoint notificationEndpointResponse
	doJSON(t, client.http, http.MethodPost, base+"/api/v1/stores/"+ownerStoreID+"/notification-endpoints", client.token, map[string]any{
		"provider": "TELEGRAM", "label": "Owner Telegram", "providerAccountRef": "@KetchRetailSecurityBot",
		"destinationRef": telegramChat, "credentialRef": "env://TELEGRAM_BOT_TOKEN",
		"config": map[string]any{"parseMode": "HTML"}, "isEnabled": true,
	}, http.StatusCreated, &endpoint)
	assertNoSecretLeak(t, endpoint, telegramChat)

	doJSON(t, client.http, http.MethodGet, base+"/api/v1/stores/"+ownerStoreID+"/notification-endpoints", client.token, nil, http.StatusOK, &[]notificationEndpointResponse{})
	doJSON(t, client.http, http.MethodPatch, base+"/api/v1/stores/"+ownerStoreID+"/notification-endpoints/"+endpoint.ID, client.token, map[string]any{
		"label": "Owner Telegram Renamed",
	}, http.StatusOK, &notificationEndpointResponse{})

	doJSON(t, client.http, http.MethodPost, base+"/api/v1/stores/"+ownerStoreID+"/notification-endpoints", client.token, map[string]any{
		"provider": "TELEGRAM", "label": "Duplicate create", "destinationRef": telegramChat, "credentialRef": "env://TELEGRAM_BOT_TOKEN",
	}, http.StatusConflict, &map[string]any{})
	rawTelegramToken := "123456789:" + strings.Repeat("A", 35)
	doJSON(t, client.http, http.MethodPost, base+"/api/v1/stores/"+ownerStoreID+"/notification-endpoints", client.token, map[string]any{
		"provider": "TELEGRAM", "label": "Raw token", "destinationRef": "5500000000", "credentialRef": rawTelegramToken,
	}, http.StatusBadRequest, &map[string]any{})

	conflictChat := "7700" + fmt.Sprintf("%06d", time.Now().UnixNano()%1000000)
	var conflictEndpoint notificationEndpointResponse
	doJSON(t, client.http, http.MethodPost, base+"/api/v1/stores/"+ownerStoreID+"/notification-endpoints", client.token, map[string]any{
		"provider": "TELEGRAM", "label": "Patch conflict target", "destinationRef": conflictChat, "credentialRef": "env://TELEGRAM_BOT_TOKEN",
	}, http.StatusCreated, &conflictEndpoint)
	doJSON(t, client.http, http.MethodPatch, base+"/api/v1/stores/"+ownerStoreID+"/notification-endpoints/"+conflictEndpoint.ID, client.token, map[string]any{
		"destinationRef": telegramChat,
	}, http.StatusConflict, &map[string]any{})
	if !endpointUnchanged(t, client, ownerStoreID, conflictEndpoint.ID, conflictChat, "Patch conflict target") {
		t.Fatal("second endpoint was mutated by a conflicting PATCH")
	}

	disposableEndpoint := client.createEndpoint(t, ownerStoreID, "Disposable Telegram", "5500999999")
	doJSON(t, client.http, http.MethodDelete, base+"/api/v1/stores/"+ownerStoreID+"/notification-endpoints/"+disposableEndpoint, client.token, nil, http.StatusOK, &map[string]bool{})

	var rule notificationRuleResponse
	doJSON(t, client.http, http.MethodPost, base+"/api/v1/stores/"+ownerStoreID+"/notification-rules", client.token, map[string]any{
		"name": "Emergency alerts",
	}, http.StatusCreated, &rule)
	if rule.MinimumSeverity != "critical" || len(rule.AlertTypes) != 0 || rule.CooldownSeconds != 0 || !rule.IsEnabled {
		t.Fatalf("rule create defaults wrong: %+v", rule)
	}

	channelsPath := base + "/api/v1/stores/" + ownerStoreID + "/notification-rules/" + rule.ID + "/channels"
	var primaryChannel notificationRuleChannelResponse
	doJSON(t, client.http, http.MethodPost, channelsPath, client.token, map[string]any{
		"endpointId": endpoint.ID, "priority": 1, "fallbackDelaySeconds": 120, "isEnabled": true,
	}, http.StatusCreated, &primaryChannel)

	whatsAppConfig := map[string]any{
		"wabaId": "111122223333", "templateName": "emergency_security_alert", "templateLanguage": "en",
		"templateVersion": "whatsapp-emergency-security-alert-v1",
		"optIn":           map[string]any{"capturedAt": "2026-08-24T00:00:00Z", "source": "OWNER_DASHBOARD", "policyVersion": "whatsapp-emergency-alerts-v1"},
	}
	var whatsappEndpoint notificationEndpointResponse
	doJSON(t, client.http, http.MethodPost, base+"/api/v1/stores/"+ownerStoreID+"/notification-endpoints", client.token, map[string]any{
		"provider": "WHATSAPP", "label": "Owner WhatsApp", "providerAccountRef": "111122223333",
		"destinationRef": "+15550001234", "credentialRef": "env://WHATSAPP_ACCESS_TOKEN", "isEnabled": false,
		"config": whatsAppConfig,
	}, http.StatusCreated, &whatsappEndpoint)
	testPathWhatsApp := base + "/api/v1/stores/" + ownerStoreID + "/notification-endpoints/" + whatsappEndpoint.ID + "/test"
	doJSON(t, client.http, http.MethodPost, testPathWhatsApp, client.token, map[string]any{"requestId": uuid.NewString()}, http.StatusConflict, &map[string]any{})
	doJSON(t, client.http, http.MethodPatch, base+"/api/v1/stores/"+ownerStoreID+"/notification-endpoints/"+whatsappEndpoint.ID, client.token, map[string]any{
		"isEnabled": true,
	}, http.StatusOK, &notificationEndpointResponse{})

	var whatsappChannel notificationRuleChannelResponse
	doJSON(t, client.http, http.MethodPost, channelsPath, client.token, map[string]any{
		"endpointId": whatsappEndpoint.ID, "priority": 2, "fallbackDelaySeconds": 300, "isEnabled": true,
	}, http.StatusCreated, &whatsappChannel)
	doJSON(t, client.http, http.MethodPost, channelsPath, client.token, map[string]any{
		"endpointId": whatsappEndpoint.ID, "priority": 3,
	}, http.StatusConflict, &map[string]any{})
	doJSON(t, client.http, http.MethodPost, channelsPath, client.token, map[string]any{
		"endpointId": endpoint.ID, "priority": 5,
	}, http.StatusConflict, &map[string]any{})
	doJSON(t, client.http, http.MethodPatch, channelsPath+"/"+whatsappChannel.ID, client.token, map[string]any{
		"priority": 1,
	}, http.StatusConflict, &map[string]any{})
	var channelsAfterConflict []notificationRuleChannelResponse
	doJSON(t, client.http, http.MethodGet, channelsPath, client.token, nil, http.StatusOK, &channelsAfterConflict)
	for _, channel := range channelsAfterConflict {
		expectedPriority := 1
		if channel.EndpointID == whatsappEndpoint.ID {
			expectedPriority = 2
		}
		if channel.Priority != expectedPriority {
			t.Fatalf("channel %s mutated by conflicting PATCH: priority %d, want %d", channel.ID, channel.Priority, expectedPriority)
		}
	}
	doJSON(t, client.http, http.MethodPatch, channelsPath+"/"+whatsappChannel.ID, client.token, map[string]any{
		"fallbackDelaySeconds": 45,
	}, http.StatusOK, &notificationRuleChannelResponse{})
	var listedChannels []notificationRuleChannelResponse
	doJSON(t, client.http, http.MethodGet, channelsPath, client.token, nil, http.StatusOK, &listedChannels)
	if len(listedChannels) != 2 || listedChannels[0].Priority > listedChannels[1].Priority {
		t.Fatalf("channels must be listed by ascending priority, got %+v", listedChannels)
	}

	requestID := uuid.NewString()
	testPath := base + "/api/v1/stores/" + ownerStoreID + "/notification-endpoints/" + endpoint.ID + "/test"
	var firstTest, repeatTest map[string]string
	doJSON(t, client.http, http.MethodPost, testPath, client.token, map[string]any{"requestId": requestID}, http.StatusAccepted, &firstTest)
	doJSON(t, client.http, http.MethodPost, testPath, client.token, map[string]any{"requestId": requestID}, http.StatusAccepted, &repeatTest)
	if firstTest["id"] == "" || firstTest["id"] != repeatTest["id"] || firstTest["deliveryKind"] != "TEST" {
		t.Fatalf("test enqueue not idempotent: %+v vs %+v", firstTest, repeatTest)
	}
	var storedEndpointID, storedKind string
	if err := client.pool.QueryRow(context.Background(), `SELECT "endpointId"::text,"deliveryKind"::text FROM "notification_deliveries" WHERE "id"=$1`, firstTest["id"]).Scan(&storedEndpointID, &storedKind); err != nil {
		t.Fatal(err)
	}
	if storedEndpointID != endpoint.ID || storedKind != "TEST" {
		t.Fatalf("TEST delivery not pinned to endpoint: endpointId=%s kind=%s", storedEndpointID, storedKind)
	}

	insertAlert := func(alertID, correlationID string) {
		t.Helper()
		// As CloudAlertSink writes it. The AI schema has no correlation
		// column; EnqueueAlertTx is still given one, as the dispatcher would.
		_ = correlationID
		testdb.Alert(t, client.pool, alertID, ownerStoreID, ownerStoreID+"_cam", "weapon_v1", "weapon_detected", "critical", time.Now(), nil)
	}
	newAlertInput := func(alertID, correlationID string) notifications.AlertNotificationInput {
		return notifications.AlertNotificationInput{
			StoreID: ownerStoreID, StoreName: "Go Notify Store", StoreTimezone: "America/Chicago",
			AlertID: alertID, CorrelationID: correlationID, AlertType: "weapon_detected",
			Severity: "critical", DetectedAt: time.Now().UTC(), CameraID: ownerStoreID + "_cam", CameraName: "Whole store",
		}
	}

	alertA := uuid.NewString()
	incidentCorrelation := "corr-" + alertA
	insertAlert(alertA, incidentCorrelation)
	summaries := client.enqueueAlert(t, newAlertInput(alertA, incidentCorrelation))
	if len(summaries) != 2 {
		t.Fatalf("expected PENDING + WAITING_FALLBACK deliveries for the first observation, got %+v", summaries)
	}
	if summaries[0].Status != notifications.StatusPending || summaries[0].Priority != 1 || summaries[1].Status != notifications.StatusWaitingFallback || summaries[1].Priority != 2 {
		t.Fatalf("unexpected route statuses: %+v", summaries)
	}
	var deliveryPage struct {
		Items      []notificationDeliveryResponse `json:"items"`
		NextCursor *string                        `json:"nextCursor"`
	}
	deliveryListPath := base + "/api/v1/stores/" + ownerStoreID + "/notification-deliveries?status=PENDING&provider=TELEGRAM&kind=ALERT&alertId=" + alertA
	doJSON(t, client.http, http.MethodGet, deliveryListPath, client.token, nil, http.StatusOK, &deliveryPage)
	if len(deliveryPage.Items) != 1 || deliveryPage.Items[0].ID != summaries[0].ID || deliveryPage.Items[0].DestinationMasked == telegramChat {
		t.Fatalf("delivery history response is incomplete or exposes the raw destination: %+v", deliveryPage)
	}
	var deliveryDetail struct {
		Delivery notificationDeliveryResponse  `json:"delivery"`
		Attempts []notificationAttemptResponse `json:"attempts"`
	}
	doJSON(t, client.http, http.MethodGet, base+"/api/v1/stores/"+ownerStoreID+"/notification-deliveries/"+summaries[0].ID, client.token, nil, http.StatusOK, &deliveryDetail)
	if deliveryDetail.Delivery.ID != summaries[0].ID || deliveryDetail.Attempts == nil || len(deliveryDetail.Attempts) != 0 {
		t.Fatalf("unexpected initial delivery detail: %+v", deliveryDetail)
	}
	doJSON(t, client.http, http.MethodGet, base+"/api/v1/stores/"+ownerStoreID+"/notification-deliveries?cursor=not-base64", client.token, nil, http.StatusBadRequest, &map[string]any{})

	alertB := uuid.NewString()
	insertAlert(alertB, incidentCorrelation)
	if secondCamera := client.enqueueAlert(t, newAlertInput(alertB, incidentCorrelation)); len(secondCamera) != 0 {
		t.Fatalf("same correlationId from another camera must not add deliveries, got %+v", secondCamera)
	}
	doJSON(t, client.http, http.MethodPatch, base+"/api/v1/stores/"+ownerStoreID+"/notification-endpoints/"+endpoint.ID, client.token, map[string]any{
		"config": map[string]any{"parseMode": "HTML", "templateVersion": "telegram-emergency-security-alert-v2"},
	}, http.StatusOK, &notificationEndpointResponse{})
	if afterVersionChange := client.enqueueAlert(t, newAlertInput(alertB, incidentCorrelation)); len(afterVersionChange) != 0 {
		t.Fatalf("template version change within one incident must not add deliveries, got %+v", afterVersionChange)
	}

	alertC := uuid.NewString()
	newIncidentCorrelation := "corr-" + alertC
	insertAlert(alertC, newIncidentCorrelation)
	newIncidentSummaries := client.enqueueAlert(t, newAlertInput(alertC, newIncidentCorrelation))
	if len(newIncidentSummaries) != 2 {
		t.Fatalf("a new correlationId must produce a fresh route delivery set, got %+v", newIncidentSummaries)
	}

	doJSON(t, client.http, http.MethodDelete, base+"/api/v1/stores/"+ownerStoreID+"/notification-endpoints/"+endpoint.ID, client.token, nil, http.StatusConflict, &map[string]any{})
	doJSON(t, client.http, http.MethodDelete, base+"/api/v1/stores/"+ownerStoreID+"/notification-rules/"+rule.ID, client.token, nil, http.StatusConflict, &map[string]any{})
	doJSON(t, client.http, http.MethodDelete, channelsPath+"/"+whatsappChannel.ID, client.token, nil, http.StatusConflict, &map[string]any{})

	client.registerNotificationUser(t, operator)
	doJSON(t, client.http, http.MethodPost, base+"/api/v1/stores/"+ownerStoreID+"/members", client.token, map[string]any{
		"email": operator.Email, "role": roleOperator,
	}, http.StatusCreated, &map[string]any{})
	operatorToken := client.login(t, operator.Email, operator.Password)
	doJSON(t, client.http, http.MethodGet, base+"/api/v1/stores/"+ownerStoreID+"/notification-endpoints", operatorToken, nil, http.StatusForbidden, &map[string]any{})
	doJSON(t, client.http, http.MethodPost, base+"/api/v1/stores/"+ownerStoreID+"/notification-endpoints/"+endpoint.ID+"/test", operatorToken, map[string]any{"requestId": uuid.NewString()}, http.StatusForbidden, &map[string]any{})
	randomStoreID := "store_nobody_" + uuid.NewString()
	doJSON(t, client.http, http.MethodGet, base+"/api/v1/stores/"+randomStoreID+"/notification-endpoints", client.token, nil, http.StatusNotFound, &map[string]any{})
	doJSON(t, client.http, http.MethodPatch, base+"/api/v1/stores/"+randomStoreID+"/notification-rules/"+rule.ID, client.token, map[string]any{"isEnabled": false}, http.StatusNotFound, &map[string]any{})
}

func endpointUnchanged(t *testing.T, client *notifyTestClient, storeID, endpointID, expectedDestination, expectedLabel string) bool {
	t.Helper()
	var destinationRef, label string
	if err := client.pool.QueryRow(context.Background(), `SELECT "destinationRef","label" FROM "notification_endpoints" WHERE "id"=$1 AND "storeId"=$2`, endpointID, storeID).Scan(&destinationRef, &label); err != nil {
		t.Fatal(err)
	}
	return destinationRef == expectedDestination && label == expectedLabel
}

func (c *notifyTestClient) createEndpoint(t *testing.T, storeID, label, chat string) string {
	t.Helper()
	var created notificationEndpointResponse
	doJSON(t, c.http, http.MethodPost, c.base+"/api/v1/stores/"+storeID+"/notification-endpoints", c.token, map[string]any{
		"provider": "TELEGRAM", "label": label, "destinationRef": chat, "credentialRef": "env://TELEGRAM_BOT_TOKEN",
	}, http.StatusCreated, &created)
	return created.ID
}

func (c *notifyTestClient) enqueueAlert(t *testing.T, input notifications.AlertNotificationInput) []notifications.DeliverySummary {
	t.Helper()
	ctx := context.Background()
	tx, err := c.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	summaries, err := notifications.EnqueueAlertTx(ctx, tx, input)
	if err != nil {
		_ = tx.Rollback(ctx)
		t.Fatal(err)
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	return summaries
}

func assertNoSecretLeak(t *testing.T, endpoint notificationEndpointResponse, rawDestination string) {
	t.Helper()
	text := strings.ToLower(mustMarshalNotification(t, endpoint))
	for _, forbidden := range []string{"credentialref", "env://", "render-secret://", strings.ToLower(rawDestination), `"payload"`} {
		if strings.Contains(text, forbidden) {
			t.Fatalf("endpoint response leaks %q: %s", forbidden, text)
		}
	}
	if !endpoint.CredentialConfigured {
		t.Fatal("expected credentialConfigured=true")
	}
}

func mustMarshalNotification(t *testing.T, value any) string {
	t.Helper()
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return string(encoded)
}
