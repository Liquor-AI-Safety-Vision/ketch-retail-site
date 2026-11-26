package server

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/liquor-store/security-api/internal/config"
	"github.com/liquor-store/security-api/internal/testdb"
)

// newTestDB: see internal/testdb.
func newTestDB(t *testing.T) *pgxpool.Pool { return testdb.New(t) }

func testConfig() config.Config {
	return config.Config{
		Environment:     "development",
		WebOrigin:       "http://localhost:5173",
		JWTAccessSecret: "test-secret-0123456789abcdef0123456789abcdef",
		JWTAccessTTL:    15 * time.Minute,
		JWTIssuer:       "liquor-store-security-api",
		JWTAudience:     "liquor-store-owner-dashboard",
		RefreshTTLDays:  30,
		RegisterEnabled: true,
		MembersEnabled:  true,
	}
}

type testAPI struct {
	t      *testing.T
	db     *pgxpool.Pool
	server *httptest.Server
}

func newTestAPI(t *testing.T, db *pgxpool.Pool, cfg config.Config) *testAPI {
	t.Helper()
	ts := httptest.NewServer(New(cfg, db, slog.New(slog.NewTextHandler(io.Discard, nil))).Handler())
	t.Cleanup(ts.Close)
	return &testAPI{t: t, db: db, server: ts}
}

func (a *testAPI) client() *http.Client {
	jar, err := cookiejar.New(nil)
	if err != nil {
		a.t.Fatal(err)
	}
	return &http.Client{Jar: jar, Timeout: 30 * time.Second}
}

func (a *testAPI) url(path string) string { return a.server.URL + path }

func (a *testAPI) exec(sql string, args ...any) {
	a.t.Helper()
	if _, err := a.db.Exec(context.Background(), sql, args...); err != nil {
		a.t.Fatalf("%v\n%s", err, sql)
	}
}

// seedAIStore writes what the AI service writes at startup: a store and its
// cameras, with metadata_json as camera_metadata.py produces it.
func (a *testAPI) seedAIStore(storeID string, cameraIDs ...string) {
	a.t.Helper()
	a.exec(`INSERT INTO stores (store_id, display_name, timezone) VALUES ($1, $2, 'Asia/Ho_Chi_Minh')`, storeID, "Store "+storeID)
	for _, cameraID := range cameraIDs {
		meta := `{"pipelines": ["counter_cash_theft_v1"], "zones": [{"name": "checkout_left", "pipeline_id": "counter_cash_theft_v1", "points_norm": [[0.1, 0.2], [0.3, 0.2], [0.3, 0.4]], "type": "polygon"}]}`
		a.exec(`INSERT INTO cameras (camera_id, store_id, display_name, camera_type, source_type, source_uri, metadata_json) VALUES ($1, $2, $3, 'counter', 'rtsp', 'rtsp://admin:***@10.0.0.5:554/stream', $4)`,
			cameraID, storeID, "Camera "+cameraID, meta)
	}
}

func uniqueEmail(prefix string) string {
	return strings.ToLower(fmt.Sprintf("%s-%d@example.test", prefix, time.Now().UnixNano()))
}

func ioCopy(dst *strings.Builder, response *http.Response) (int64, error) {
	defer response.Body.Close()
	return io.Copy(dst, response.Body)
}

func discardLogger() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }
