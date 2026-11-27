package server

import (
	"context"
	"net/http"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/liquor-store/security-api/internal/security"
)

// Accounts live in the AI schema's owners/owner_stores; stores and cameras
// are written by the AI service and only read here.
func TestAccountsStoresAndCamerasOnTheAISchema(t *testing.T) {
	api := newTestAPI(t, newTestDB(t), testConfig())
	api.seedAIStore("store_a", "cam_a1", "cam_a2")
	api.seedAIStore("store_b", "cam_b1")
	client := api.client()

	// Registration links to a store the AI service created; it creates none.
	email, password := uniqueEmail("owner"), "Owner-password-1234"
	doJSON(t, client, http.MethodPost, api.url("/api/v1/auth/register"), "", map[string]any{
		"email": email, "password": password, "displayName": "Owner A", "storeId": "store_missing",
	}, http.StatusNotFound, nil)
	var registered sessionResponse
	doJSON(t, client, http.MethodPost, api.url("/api/v1/auth/register"), "", map[string]any{
		"email": email, "password": password, "displayName": "Owner A", "storeId": "store_a",
	}, http.StatusCreated, &registered)
	if !strings.HasPrefix(registered.User.ID, "owner_") || len(registered.User.Stores) != 1 ||
		registered.User.Stores[0].StoreID != "store_a" || registered.User.Stores[0].Role != roleOwner ||
		registered.User.Stores[0].StoreName != "Store store_a" {
		t.Fatalf("unexpected registration: %+v", registered.User)
	}
	var createdAt, hash string
	if err := api.db.QueryRow(context.Background(), `SELECT created_at, password_hash FROM owners WHERE owner_id=$1`, registered.User.ID).Scan(&createdAt, &hash); err != nil {
		t.Fatal(err)
	}
	if _, err := parseISOTime(createdAt); err != nil || !strings.HasSuffix(createdAt, "+00:00") {
		t.Fatalf("owners.created_at %q is not the AI schema's ISO format", createdAt)
	}
	if !strings.HasPrefix(hash, "$argon2id$") {
		t.Fatalf("new owner hash is not Argon2id: %q", hash[:10])
	}

	var loggedIn sessionResponse
	doJSON(t, client, http.MethodPost, api.url("/api/v1/auth/login"), "", map[string]any{
		"email": strings.ToUpper(email), "password": password,
	}, http.StatusCreated, &loggedIn)
	token := loggedIn.AccessToken
	doJSON(t, client, http.MethodGet, api.url("/api/v1/auth/me"), token, nil, http.StatusOK, &currentUser{})

	var stores []storeResponse
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores"), token, nil, http.StatusOK, &stores)
	if len(stores) != 1 || stores[0].ID != "store_a" || stores[0].Code != "store_a" || stores[0].Timezone != "Asia/Ho_Chi_Minh" {
		t.Fatalf("stores = %+v", stores)
	}
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_b"), token, nil, http.StatusNotFound, nil)
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_b/cameras"), token, nil, http.StatusNotFound, nil)

	// Cameras: the AI service's rows, never their source_uri.
	request, _ := http.NewRequest(http.MethodGet, api.url("/api/v1/stores/store_a/cameras"), nil)
	request.Header.Set("Authorization", "Bearer "+token)
	response, err := client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	raw := new(strings.Builder)
	_, _ = ioCopy(raw, response)
	if response.StatusCode != http.StatusOK || strings.Contains(raw.String(), "rtsp://") || strings.Contains(raw.String(), "10.0.0.5") || strings.Contains(raw.String(), "admin") {
		t.Fatalf("camera list leaked the stream address or failed (%d): %s", response.StatusCode, raw)
	}
	var cameras []cameraResponse
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_a/cameras"), token, nil, http.StatusOK, &cameras)
	if len(cameras) != 2 || cameras[0].ID != "cam_a1" || cameras[0].CameraType != "counter" || !cameras[0].IsActive ||
		len(cameras[0].Pipelines) != 1 || cameras[0].Pipelines[0] != "counter_cash_theft_v1" {
		t.Fatalf("cameras = %+v", cameras)
	}
	// Online/offline comes from the AI's heartbeat in cameras.last_frame_at.
	if cameras[0].Status != "unknown" || cameras[0].LastFrameAt != nil {
		t.Fatalf("a camera the AI never reported: %+v", cameras[0])
	}
	api.exec(`UPDATE cameras SET last_frame_at=$1 WHERE camera_id='cam_a1'`, time.Now().UTC().Format("2006-01-02T15:04:05.000000+00:00"))
	api.exec(`UPDATE cameras SET last_frame_at=$1 WHERE camera_id='cam_a2'`, time.Now().UTC().Add(-10*time.Minute).Format("2006-01-02T15:04:05.000000+00:00"))
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_a/cameras"), token, nil, http.StatusOK, &cameras)
	if cameras[0].Status != "online" || cameras[1].Status != "offline" || cameras[0].LastFrameAt == nil {
		t.Fatalf("heartbeat status: %+v", cameras)
	}
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_a/cameras/cam_b1"), token, nil, http.StatusNotFound, nil)
	var zones []zoneResponse
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_a/cameras/cam_a1/zones"), token, nil, http.StatusOK, &zones)
	if len(zones) != 1 || zones[0].Name != "checkout_left" || zones[0].PipelineID != "counter_cash_theft_v1" || len(zones[0].PointsNorm) != 3 {
		t.Fatalf("zones = %+v", zones)
	}
	// Writes to AI-owned rows are not routes any more.
	doJSON(t, client, http.MethodPost, api.url("/api/v1/stores/store_a/cameras"), token, map[string]any{"name": "x"}, http.StatusMethodNotAllowed, nil)
	doJSON(t, client, http.MethodPatch, api.url("/api/v1/stores/store_a"), token, map[string]any{"name": "x"}, http.StatusMethodNotAllowed, nil)

	// Refresh rotates; the old cookie is dead.
	refreshURL, _ := url.Parse(api.url("/api/v1/auth/refresh"))
	var original *http.Cookie
	for _, c := range client.Jar.Cookies(refreshURL) {
		if c.Name == "refresh_token" {
			copied := *c
			original = &copied
		}
	}
	if original == nil {
		t.Fatal("login did not issue a refresh cookie")
	}
	doJSON(t, client, http.MethodPost, api.url("/api/v1/auth/refresh"), "", nil, http.StatusCreated, &sessionResponse{})
	replay, _ := http.NewRequest(http.MethodPost, api.url("/api/v1/auth/refresh"), nil)
	replay.AddCookie(original)
	replayed, err := (&http.Client{Timeout: 10 * time.Second}).Do(replay)
	if err != nil {
		t.Fatal(err)
	}
	_ = replayed.Body.Close()
	if replayed.StatusCode != http.StatusUnauthorized {
		t.Fatalf("replayed refresh token: got %d, want 401", replayed.StatusCode)
	}

	// Members: an operator can read alerts but not manage members.
	operatorEmail := uniqueEmail("operator")
	doJSON(t, api.client(), http.MethodPost, api.url("/api/v1/auth/register"), "", map[string]any{
		"email": operatorEmail, "password": password, "displayName": "Operator", "storeId": "store_b",
	}, http.StatusCreated, nil)
	var added map[string]string
	doJSON(t, client, http.MethodPost, api.url("/api/v1/stores/store_a/members"), token, map[string]any{
		"email": operatorEmail, "role": "OPERATOR",
	}, http.StatusBadRequest, nil)
	doJSON(t, client, http.MethodPost, api.url("/api/v1/stores/store_a/members"), token, map[string]any{
		"email": operatorEmail, "role": roleOperator,
	}, http.StatusCreated, &added)
	operatorClient := api.client()
	var operatorSession sessionResponse
	doJSON(t, operatorClient, http.MethodPost, api.url("/api/v1/auth/login"), "", map[string]any{
		"email": operatorEmail, "password": password,
	}, http.StatusCreated, &operatorSession)
	doJSON(t, operatorClient, http.MethodGet, api.url("/api/v1/stores/store_a/cameras"), operatorSession.AccessToken, nil, http.StatusOK, nil)
	doJSON(t, operatorClient, http.MethodGet, api.url("/api/v1/stores/store_a/members"), operatorSession.AccessToken, nil, http.StatusForbidden, nil)
	doJSON(t, client, http.MethodPatch, api.url("/api/v1/stores/store_a/members/"+registered.User.ID), token, map[string]any{"role": roleManager}, http.StatusConflict, nil)
	doJSON(t, client, http.MethodPatch, api.url("/api/v1/stores/store_a/members/"+added["userId"]), token, map[string]any{"role": roleManager}, http.StatusOK, nil)
	doJSON(t, operatorClient, http.MethodGet, api.url("/api/v1/stores/store_a/members"), operatorSession.AccessToken, nil, http.StatusOK, nil)
	doJSON(t, client, http.MethodDelete, api.url("/api/v1/stores/store_a/members/"+added["userId"]), token, nil, http.StatusOK, nil)
	doJSON(t, operatorClient, http.MethodGet, api.url("/api/v1/stores/store_a/cameras"), operatorSession.AccessToken, nil, http.StatusNotFound, nil)

	// A suspended account loses access at once, access token or not.
	api.exec(`UPDATE owners SET status='suspended' WHERE owner_id=$1`, registered.User.ID)
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores"), token, nil, http.StatusUnauthorized, nil)
	doJSON(t, api.client(), http.MethodPost, api.url("/api/v1/auth/login"), "", map[string]any{
		"email": email, "password": password,
	}, http.StatusUnauthorized, nil)
}

// Owners seeded by the AI service before logins moved here carry a bcrypt
// hash. They log in unchanged, and the hash becomes Argon2id on the way.
func TestLegacyBcryptOwnerLogsInAndIsRehashed(t *testing.T) {
	api := newTestAPI(t, newTestDB(t), testConfig())
	api.seedAIStore("store_test_01", "st01_counter")
	// Exactly what seed_cloud_store.py used to write, hash included
	// (Python: bcrypt.hashpw(b"owner-test-password", bcrypt.gensalt(12))).
	const pythonHash = "$2b$12$.eIq6CAh0NqzujQGWMSJWu2gLykf4iJ3l4ZM1eyTcCHe0uM49yMKu"
	api.exec(`INSERT INTO owners (owner_id, display_name, email, password_hash, metadata_json, created_at, updated_at) VALUES ('owner_0123456789ab','Owner Test 01','owner.test01@liquir.local',$1,'{}','2026-09-17T10:00:00+00:00','2026-09-17T10:00:00+00:00')`, pythonHash)
	api.exec(`INSERT INTO owner_stores (owner_id, store_id, created_at) VALUES ('owner_0123456789ab','store_test_01','2026-09-17T10:00:00+00:00')`)

	doJSON(t, api.client(), http.MethodPost, api.url("/api/v1/auth/login"), "", map[string]any{
		"email": "owner.test01@liquir.local", "password": "wrong-password-0000",
	}, http.StatusUnauthorized, nil)
	var stillLegacy string
	_ = api.db.QueryRow(context.Background(), `SELECT password_hash FROM owners WHERE owner_id='owner_0123456789ab'`).Scan(&stillLegacy)
	if stillLegacy != pythonHash {
		t.Fatal("a failed login must not touch the stored hash")
	}

	var session sessionResponse
	client := api.client()
	doJSON(t, client, http.MethodPost, api.url("/api/v1/auth/login"), "", map[string]any{
		"email": "owner.test01@liquir.local", "password": "owner-test-password",
	}, http.StatusCreated, &session)
	if len(session.User.Stores) != 1 || session.User.Stores[0].Role != roleOwner {
		t.Fatalf("legacy owner stores = %+v (role column default must be 'owner')", session.User.Stores)
	}
	var upgraded string
	if err := api.db.QueryRow(context.Background(), `SELECT password_hash FROM owners WHERE owner_id='owner_0123456789ab'`).Scan(&upgraded); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(upgraded, "$argon2id$") {
		t.Fatalf("hash was not upgraded: %q", upgraded[:10])
	}
	if ok, err := security.VerifyPassword("owner-test-password", upgraded); err != nil || !ok {
		t.Fatalf("upgraded hash does not verify: %v %v", ok, err)
	}
	doJSON(t, api.client(), http.MethodPost, api.url("/api/v1/auth/login"), "", map[string]any{
		"email": "owner.test01@liquir.local", "password": "owner-test-password",
	}, http.StatusCreated, nil)
}
