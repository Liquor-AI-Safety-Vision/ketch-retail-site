package server

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/liquor-store/security-api/internal/testdb"
)

// A store with more alerts than one page: the dashboard pages with the
// cursor, filters on several statuses/types at once, and takes its totals
// from the summary instead of counting what it happened to load.
func TestAlertPagingTotalsAndSummary(t *testing.T) {
	db := newTestDB(t)
	srv := New(testConfig(), db, discardLogger())
	ts := httptest.NewServer(srv.Handler())
	defer ts.Close()
	api := &testAPI{t: t, db: db, server: ts}
	api.seedAIStore("store_test_01", "st01_counter", "st01_storage")
	api.seedAIStore("store_other", "other_cam")
	owner := registerAndLogin(t, api, "store_test_01", uniqueEmail("paging"))

	base := time.Date(2026, 11, 27, 0, 0, 0, 0, time.UTC)
	// 130 unknown_entry (100 open, 30 acknowledged), 25 cash (20 open, 5 confirmed), 7 dismissed staff.
	insert := func(n int, camera, pipeline, event, severity, status string, offset int) {
		for i := 0; i < n; i++ {
			id := uuid.NewString()
			testdb.Alert(t, db, id, "store_test_01", camera, pipeline, event, severity, base.Add(time.Duration(offset+i)*time.Minute), nil)
			if status != "open" {
				api.exec(`UPDATE alerts SET status=$2 WHERE alert_id=$1`, id, status)
			}
		}
	}
	insert(100, "st01_storage", "storage_intrusion_v1", "unknown_entry", "warning", "open", 0)
	insert(30, "st01_storage", "storage_intrusion_v1", "unknown_entry", "warning", "acknowledged", 100)
	insert(20, "st01_counter", "counter_cash_theft_v1", "suspected_cash_concealment", "high", "open", 200)
	insert(5, "st01_counter", "counter_cash_theft_v1", "suspected_cash_concealment", "high", "confirmed", 300)
	insert(7, "st01_storage", "storage_intrusion_v1", "staff_outside_schedule", "warning", "dismissed", 400)
	testdb.Alert(t, db, uuid.NewString(), "store_other", "other_cam", "counter_cash_theft_v1", "suspected_cash_concealment", "high", base, nil)

	client := api.client()
	type page struct {
		Items      []alertResponse `json:"items"`
		NextCursor *string         `json:"nextCursor"`
		Total      int             `json:"total"`
	}
	list := func(query url.Values) page {
		t.Helper()
		var p page
		doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts?"+query.Encode()), owner, nil, http.StatusOK, &p)
		return p
	}

	// "Needs attention" = open + acknowledged, walked page by page.
	seen := map[string]bool{}
	query := url.Values{"status": {"open,acknowledged"}, "limit": {"40"}}
	pages := 0
	for {
		p := list(query)
		pages++
		if p.Total != 150 {
			t.Fatalf("total = %d, want 150 (it must not depend on the page)", p.Total)
		}
		for _, item := range p.Items {
			if item.Status != "open" && item.Status != "acknowledged" {
				t.Fatalf("status filter leaked %s", item.Status)
			}
			if seen[item.ID] {
				t.Fatalf("alert %s on two pages", item.ID)
			}
			seen[item.ID] = true
		}
		if p.NextCursor == nil {
			break
		}
		query.Set("cursor", *p.NextCursor)
	}
	if len(seen) != 150 || pages != 4 {
		t.Fatalf("walked %d alerts over %d pages, want 150 over 4", len(seen), pages)
	}

	// Two event types at once (the "restricted area" case).
	if p := list(url.Values{"eventType": {"unknown_entry,staff_outside_schedule"}, "limit": {"1"}}); p.Total != 137 {
		t.Fatalf("restricted area total = %d, want 137", p.Total)
	}
	if p := list(url.Values{"status": {"confirmed"}, "eventType": {"suspected_cash_concealment"}}); p.Total != 5 || len(p.Items) != 5 {
		t.Fatalf("confirmed cash = %d/%d", p.Total, len(p.Items))
	}
	for _, bad := range []string{"status=open,bogus", "severity=,", "eventType=" + url.QueryEscape("a,b,c,d,e,f,g,h,i,j,k")} {
		doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts?"+bad), owner, nil, http.StatusBadRequest, nil)
	}

	var summary struct {
		Counts []struct {
			Status, EventType string
			Count             int
		} `json:"counts"`
	}
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts/summary"), owner, nil, http.StatusOK, &summary)
	got := map[string]int{}
	for _, c := range summary.Counts {
		got[c.Status+"/"+c.EventType] = c.Count
	}
	want := map[string]int{"open/unknown_entry": 100, "acknowledged/unknown_entry": 30, "open/suspected_cash_concealment": 20,
		"confirmed/suspected_cash_concealment": 5, "dismissed/staff_outside_schedule": 7}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("summary = %v, want %v (another store's alert must not count)", got, want)
	}
	from := base.Add(200 * time.Minute).Format(time.RFC3339)
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_test_01/alerts/summary?from="+url.QueryEscape(from)), owner, nil, http.StatusOK, &summary)
	if len(summary.Counts) != 3 {
		t.Fatalf("summary from %s = %+v", from, summary.Counts)
	}
	doJSON(t, client, http.MethodGet, api.url("/api/v1/stores/store_other/alerts/summary"), owner, nil, http.StatusNotFound, nil)
}
