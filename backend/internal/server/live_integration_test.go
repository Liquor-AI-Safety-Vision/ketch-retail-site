package server

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
)

// The reverse proxy asks GET /api/v1/live/authorize for every HLS request
// under /live/<camera_id>/; only an operator of the camera's store, holding
// the cookie from POST /stores/{id}/live-session, gets 204.
func TestLiveViewSessionAndAuthorize(t *testing.T) {
	db := newTestDB(t)
	cfg := testConfig()
	cfg.LiveViewMode = "test"
	srv := New(cfg, db, discardLogger())
	ts := httptest.NewServer(srv.Handler())
	defer ts.Close()
	api := &testAPI{t: t, db: db, server: ts}
	api.seedAIStore("store_test_01", "st01_counter", "st01_storage")
	api.seedAIStore("store_other", "other_cam")
	owner := registerAndLogin(t, api, "store_test_01", uniqueEmail("live"))

	// The session: a cookie scoped to /live/, and the stream paths.
	client := api.client()
	var session struct {
		Mode    string            `json:"mode"`
		Streams map[string]string `json:"streams"`
	}
	doJSON(t, client, http.MethodPost, api.url("/api/v1/stores/store_test_01/live-session"), owner, map[string]any{}, http.StatusCreated, &session)
	if session.Mode != "test" || session.Streams["st01_counter"] != "/live/st01_counter/index.m3u8" || len(session.Streams) != 2 {
		t.Fatalf("session = %+v", session)
	}
	base, _ := url.Parse(ts.URL)
	cookies := client.Jar.Cookies(&url.URL{Scheme: base.Scheme, Host: base.Host, Path: "/live/st01_counter/"})
	if len(cookies) != 1 || cookies[0].Name != liveCookie {
		t.Fatalf("live cookie not scoped to /live/: %+v", cookies)
	}
	if other := client.Jar.Cookies(&url.URL{Scheme: base.Scheme, Host: base.Host, Path: "/api/v1/stores"}); len(other) != 0 {
		t.Fatalf("live cookie leaks outside /live/: %+v", other)
	}
	// Someone else's store is refused before any cookie is issued.
	doJSON(t, api.client(), http.MethodPost, api.url("/api/v1/stores/store_other/live-session"), owner, map[string]any{}, http.StatusNotFound, nil)

	authorize := func(cookie, forwardedURI string) int {
		t.Helper()
		request, _ := http.NewRequest(http.MethodGet, api.url("/api/v1/live/authorize"), nil)
		request.Header.Set("X-Forwarded-Uri", forwardedURI)
		if cookie != "" {
			request.AddCookie(&http.Cookie{Name: liveCookie, Value: cookie})
		}
		response, err := http.DefaultClient.Do(request)
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		return response.StatusCode
	}
	token := cookies[0].Value
	for _, c := range []struct {
		cookie, uri string
		want        int
	}{
		{token, "/live/st01_counter/index.m3u8", http.StatusNoContent},
		{token, "/live/st01_storage/seg12.mp4?_HLS_msn=3", http.StatusNoContent},
		{token, "/live/st01_counter/index.m3u8", http.StatusNoContent}, // cached decision
		{token, "/live/other_cam/index.m3u8", http.StatusForbidden},    // another store's camera
		{token, "/live/no_such_cam/index.m3u8", http.StatusForbidden},
		{token, "/live/st01_counter", http.StatusForbidden}, // not under a camera
		{token, "/live/../api/v1/stores", http.StatusForbidden},
		{token, "/elsewhere/st01_counter/index.m3u8", http.StatusForbidden},
		{"", "/live/st01_counter/index.m3u8", http.StatusUnauthorized},
		{token + "x", "/live/st01_counter/index.m3u8", http.StatusUnauthorized},
		{owner, "/live/st01_counter/index.m3u8", http.StatusUnauthorized}, // an access token is not a live token
	} {
		if got := authorize(c.cookie, c.uri); got != c.want {
			t.Errorf("authorize(%q) = %d, want %d", c.uri, got, c.want)
		}
	}

	// A suspended owner loses live view once the cached decision expires;
	// a storage camera never checked before is refused at once.
	api.exec(`UPDATE owners SET status='suspended'`)
	srv.live = liveDecisionCache{}
	if got := authorize(token, "/live/st01_storage/index.m3u8"); got != http.StatusForbidden {
		t.Fatalf("suspended owner: %d", got)
	}
}

func TestLiveViewDisabledByDefault(t *testing.T) {
	db := newTestDB(t)
	srv := New(testConfig(), db, discardLogger())
	ts := httptest.NewServer(srv.Handler())
	defer ts.Close()
	api := &testAPI{t: t, db: db, server: ts}
	api.seedAIStore("store_test_01", "st01_counter")
	owner := registerAndLogin(t, api, "store_test_01", uniqueEmail("nolive"))
	doJSON(t, api.client(), http.MethodPost, api.url("/api/v1/stores/store_test_01/live-session"), owner, map[string]any{}, http.StatusServiceUnavailable, nil)
	response, err := http.Get(api.url("/api/v1/live/authorize"))
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("authorize with live view off = %d", response.StatusCode)
	}
}

func TestLiveCameraFromURI(t *testing.T) {
	for uri, want := range map[string]string{
		"/live/st01_counter/index.m3u8":    "st01_counter",
		"/live/st01_counter/s/part1.mp4?x": "st01_counter",
		"/live/":                           "",
		"/live/a":                          "",
		"/live/a/":                         "",
		"/live/a b/index.m3u8":             "",
		"/live/a/../b/index.m3u8":          "",
	} {
		got, ok := liveCameraFromURI(uri)
		if (want == "") == ok || got != want {
			t.Errorf("liveCameraFromURI(%q) = %q,%v; want %q", uri, got, ok, want)
		}
	}
}
