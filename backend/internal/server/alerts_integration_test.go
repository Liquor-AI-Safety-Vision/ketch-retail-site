package server

import (
	"context"
	"encoding/base64"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/liquor-store/security-api/internal/notifications"
	"github.com/liquor-store/security-api/internal/testdb"
)

// fakeR2 serves objects by key, as R2 would behind a presigned URL.
type fakeR2 struct{ base string }

func (f fakeR2) PresignGET(key string) (string, error) {
	if strings.HasPrefix(key, "/") || strings.Contains(key, "..") || strings.Contains(key, "://") {
		return "", errBadKey
	}
	return f.base + "/" + url.PathEscape(key), nil
}

type badKeyError struct{}

func (badKeyError) Error() string { return "not an object key" }

var errBadKey = badKeyError{}

// The dashboard's view of alerts the AI service wrote: list, filter, page,
// detail with media, review decisions, history, and playback through R2.
func TestAlertsAPIOverAIRows(t *testing.T) {
	db := newTestDB(t)
	cfg := testConfig()
	srv := New(cfg, db, discardLogger())
	var fetched []string
	r2 := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fetched = append(fetched, r.URL.Path)
		w.Header().Set("Content-Type", "video/mp4")
		_, _ = w.Write([]byte("mp4-bytes"))
	}))
	defer r2.Close()
	api := &testAPI{t: t, db: db}
	ts := httptest.NewServer(srv.Handler())
	defer ts.Close()
	api.server = ts
	review, err := notifications.NewSecureReviewService(db, ts.URL, fakeR2{base: r2.URL}, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	srv.SetSecureReviewService(review)

	api.seedAIStore("store_test_01", "st01_counter", "st01_storage")
	api.seedAIStore("store_other", "other_cam")
	owner := registerAndLogin(t, api, "store_test_01", uniqueEmail("owner"))

	base := time.Date(2026, 11, 27, 8, 0, 0, 0, time.UTC)
	staffA := "Staff A"
	cash, entry, staff := uuid.NewString(), uuid.NewString(), uuid.NewString()
	testdb.Alert(t, db, cash, "store_test_01", "st01_counter", "counter_cash_theft_v1", "suspected_cash_concealment", "high", base, nil)
	testdb.Alert(t, db, entry, "store_test_01", "st01_storage", "storage_intrusion_v1", "unknown_entry", "warning", base.Add(time.Minute), nil)
	testdb.Alert(t, db, staff, "store_test_01", "st01_storage", "storage_intrusion_v1", "staff_outside_schedule", "warning", base.Add(2*time.Minute), &staffA)
	testdb.Alert(t, db, uuid.NewString(), "store_other", "other_cam", "counter_cash_theft_v1", "suspected_cash_concealment", "high", base, nil)
	clipID, snapID := uuid.NewString(), uuid.NewString()
	clipStart, clipEnd := base.Add(-15*time.Second), base.Add(15*time.Second)
	testdb.Media(t, db, snapID, cash, "snapshot", "alerts/"+cash+"/snapshot.jpg", "image/jpeg", &base, nil)
	testdb.Media(t, db, clipID, cash, "clip", "alerts/"+cash+"/evidence.mp4", "video/mp4", &clipStart, &clipEnd)
	// A row from an AI run with local storage: a filesystem path, not an R2 key.
	localClip := uuid.NewString()
	testdb.Media(t, db, localClip, entry, "clip", "/workspace/liquir_store/ai/data/split_alerts/"+entry+"/evidence.mp4", "video/mp4", &clipStart, &clipEnd)

	client := api.client()
	type page struct {
		Items      []alertResponse `json:"items"`
		NextCursor *string         `json:"nextCursor"`
	}
	var all page
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts"), owner, nil, http.StatusOK, &all)
	if len(all.Items) != 3 || all.Items[0].ID != staff || all.Items[2].ID != cash || all.NextCursor != nil {
		t.Fatalf("list is not this store's alerts newest first: %+v", all.Items)
	}
	first := all.Items[2]
	if first.EventType != "suspected_cash_concealment" || first.PipelineType != "counter_cash_theft_v1" || first.Severity != "high" ||
		first.Status != statusOpen || first.CameraName == nil || *first.CameraName != "Camera st01_counter" ||
		!first.HasVideoEvidence || !first.HasSnapshot || len(first.ReasonCodes) != 1 || first.ReasonCodes[0] != "TEST_REASON" ||
		strings.ReplaceAll(string(first.Metadata), " ", "") != `{"store_id":"x"}` || first.DetectedAt != testdb.ISO(base) {
		t.Fatalf("alert fields: %+v", first)
	}
	if all.Items[0].IdentityLabel == nil || *all.Items[0].IdentityLabel != "Staff A" {
		t.Fatalf("identity label lost: %+v", all.Items[0])
	}

	// Paging walks every alert once, in order.
	var p1, p2 page
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts?limit=2"), owner, nil, http.StatusOK, &p1)
	if len(p1.Items) != 2 || p1.NextCursor == nil {
		t.Fatalf("page 1 = %+v", p1)
	}
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts?limit=2&cursor="+url.QueryEscape(*p1.NextCursor)), owner, nil, http.StatusOK, &p2)
	if len(p2.Items) != 1 || p2.Items[0].ID != cash || p2.NextCursor != nil {
		t.Fatalf("page 2 = %+v", p2)
	}
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts?cursor="+base64.RawURLEncoding.EncodeToString([]byte("garbage"))), owner, nil, http.StatusBadRequest, nil)

	// Filters use the AI vocabulary.
	for query, want := range map[string]int{
		"eventType=unknown_entry": 1, "severity=warning": 2, "severity=high": 1, "status=open": 3,
		"cameraId=st01_storage": 2, "pipelineType=storage_intrusion_v1": 2,
		"from=" + url.QueryEscape(base.Add(30*time.Second).Format(time.RFC3339)): 2,
		"to=" + url.QueryEscape(base.Add(30*time.Second).Format(time.RFC3339)):   1,
	} {
		var filtered page
		doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts?"+query), owner, nil, http.StatusOK, &filtered)
		if len(filtered.Items) != want {
			t.Fatalf("%s: got %d alerts, want %d", query, len(filtered.Items), want)
		}
	}
	for _, bad := range []string{"status=NEW", "severity=CRITICALLY", "limit=0"} {
		doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts?"+bad), owner, nil, http.StatusBadRequest, nil)
	}

	// Detail carries the media, clip duration from its recorded window.
	var detail alertDetailResponse
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts/"+cash), owner, nil, http.StatusOK, &detail)
	if len(detail.Media) != 2 {
		t.Fatalf("media = %+v", detail.Media)
	}
	var clip *mediaResponse
	for i := range detail.Media {
		if detail.Media[i].Kind == "clip" {
			clip = &detail.Media[i]
		}
	}
	if clip == nil || clip.ID != clipID || clip.DurationSeconds == nil || *clip.DurationSeconds != 30 || clip.MimeType == nil || *clip.MimeType != "video/mp4" {
		t.Fatalf("clip = %+v", clip)
	}
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_other/alerts"), owner, nil, http.StatusNotFound, nil)

	// Playback: an opaque link to this API, which fetches the object from R2.
	var link struct {
		URL string `json:"url"`
	}
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts/"+cash+"/media/"+clipID+"/playback-url"), owner, nil, http.StatusCreated, &link)
	if !strings.HasPrefix(link.URL, ts.URL+"/api/v1/notification-review/") || strings.Contains(link.URL, "alerts/") {
		t.Fatalf("playback link leaks the object key or points elsewhere: %s", link.URL)
	}
	response, err := http.Get(link.URL)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(response.Body)
	_ = response.Body.Close()
	if response.StatusCode != http.StatusOK || string(body) != "mp4-bytes" || len(fetched) != 1 || fetched[0] != "/alerts/"+cash+"/evidence.mp4" {
		t.Fatalf("playback = %d %q, R2 saw %v", response.StatusCode, body, fetched)
	}
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts/"+entry+"/media/"+clipID+"/playback-url"), owner, nil, http.StatusNotFound, nil)
	// Local-storage media: the link is issued, but there is nothing in R2 to serve.
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts/"+entry+"/media/"+localClip+"/playback-url"), owner, nil, http.StatusCreated, &link)
	if response, err = http.Get(link.URL); err != nil || response.StatusCode != http.StatusNotFound {
		t.Fatalf("local path was served: %v %v", response.StatusCode, err)
	}
	_ = response.Body.Close()

	// Review: open -> acknowledged -> confirmed (manager+), recorded in history.
	doJSON(t, client, http.MethodPost, api.url("/api/v1/stores/store_test_01/alerts/"+cash+"/acknowledge"), owner, map[string]any{}, http.StatusCreated, nil)
	doJSON(t, client, http.MethodPost, api.url("/api/v1/stores/store_test_01/alerts/"+cash+"/confirm"), owner, map[string]any{"note": "seen on the clip"}, http.StatusCreated, nil)
	doJSON(t, client, http.MethodPost, api.url("/api/v1/stores/store_test_01/alerts/"+cash+"/acknowledge"), owner, map[string]any{}, http.StatusConflict, nil)
	doJSON(t, client, http.MethodPost, api.url("/api/v1/stores/store_test_01/alerts/"+cash+"/resolve"), owner, map[string]any{}, http.StatusNotFound, nil)
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts/"+cash), owner, nil, http.StatusOK, &detail)
	if detail.Status != statusConfirmed || detail.ReviewNote == nil || *detail.ReviewNote != "seen on the clip" || detail.ReviewedByName == nil || detail.ReviewedAt == nil {
		t.Fatalf("review not recorded on the AI row: %+v", detail.alertResponse)
	}
	if _, err := parseISOTime(*detail.ReviewedAt); err != nil {
		t.Fatalf("reviewed_at %q is not ISO text", *detail.ReviewedAt)
	}
	var history []string
	rows, err := db.Query(context.Background(), `SELECT "previousStatus"||'>'||"newStatus" FROM "alert_status_history" WHERE "alertId"=$1 ORDER BY "createdAt"`, cash)
	if err != nil {
		t.Fatal(err)
	}
	for rows.Next() {
		var h string
		_ = rows.Scan(&h)
		history = append(history, h)
	}
	rows.Close()
	if strings.Join(history, ",") != "open>acknowledged,acknowledged>confirmed" {
		t.Fatalf("history = %v", history)
	}

	// An operator may dismiss an open alert but not confirm one.
	operatorEmail := uniqueEmail("operator")
	registerAndLogin(t, api, "store_other", operatorEmail)
	doJSON(t, client, http.MethodPost, api.url("/api/v1/stores/store_test_01/members"), owner, map[string]any{"email": operatorEmail, "role": roleOperator}, http.StatusCreated, nil)
	operator := login(t, api, operatorEmail)
	doJSON(t, api.client(), http.MethodPost, api.url("/api/v1/stores/store_test_01/alerts/"+entry+"/confirm"), operator, map[string]any{}, http.StatusForbidden, nil)
	doJSON(t, api.client(), http.MethodPost, api.url("/api/v1/stores/store_test_01/alerts/"+entry+"/dismiss"), operator, map[string]any{}, http.StatusCreated, nil)
}

const testPassword = "Test-password-1234"

func registerAndLogin(t *testing.T, api *testAPI, storeID, email string) string {
	t.Helper()
	doJSON(t, api.client(), http.MethodPost, api.url("/api/v1/auth/register"), "", map[string]any{
		"email": email, "password": testPassword, "displayName": "Test Owner", "storeId": storeID,
	}, http.StatusCreated, nil)
	return login(t, api, email)
}

func login(t *testing.T, api *testAPI, email string) string {
	t.Helper()
	var session sessionResponse
	doJSON(t, api.client(), http.MethodPost, api.url("/api/v1/auth/login"), "", map[string]any{
		"email": email, "password": testPassword,
	}, http.StatusCreated, &session)
	return session.AccessToken
}
