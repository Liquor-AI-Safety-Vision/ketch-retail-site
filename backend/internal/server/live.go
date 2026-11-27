package server

// Live view: the browser plays each camera as HLS from MediaMTX, which only
// listens on localhost. The reverse proxy (deploy/Caddyfile) serves it under
// /live/<camera_id>/ and asks this API about every request first
// (forward_auth -> GET /api/v1/live/authorize).
//
// Authorisation rides on a cookie, not the access token, because the
// browser's HLS player fetches playlists and segments itself and cannot add an
// Authorization header to each. POST /stores/{id}/live-session issues it to an
// operator (or higher) of that store: a short-lived JWT naming the owner and
// the store, scoped to Path=/live/ so it is sent nowhere else.
//
// The camera is the same in test and production mode -- MediaMTX either
// replays the store's footage or relays the real camera under the same
// camera_id -- so LIVE_VIEW_MODE only tells the dashboard which it is showing.

import (
	"errors"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/jackc/pgx/v5"
)

// camera_id as the AI manifest writes it, which is also the MediaMTX path.
var liveCameraIDPattern = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,200}$`)

const (
	liveCookie   = "liquor_live"
	liveAudience = "liquor-live-view"
	liveTTL      = 30 * time.Minute
	// An HLS player fetches a segment every second or two; a decision is
	// reused this long instead of querying the database each time.
	liveDecisionTTL = 30 * time.Second
)

type liveClaims struct {
	StoreID string `json:"store"`
	jwt.RegisteredClaims
}

type liveDecisionCache struct {
	mu      sync.Mutex
	entries map[string]time.Time
}

func (c *liveDecisionCache) allowed(key string, now time.Time) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	until, ok := c.entries[key]
	return ok && now.Before(until)
}

func (c *liveDecisionCache) remember(key string, now time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.entries == nil || len(c.entries) > 10_000 {
		c.entries = map[string]time.Time{}
	}
	c.entries[key] = now.Add(liveDecisionTTL)
}

func (s *Server) createLiveSession(w http.ResponseWriter, r *http.Request, user principal) {
	storeID := r.PathValue("storeId")
	if !s.requireRole(w, r.Context(), user.ID, storeID, roleOperator) {
		return
	}
	if s.config.LiveViewMode == "" {
		writeError(w, http.StatusServiceUnavailable, "Service Unavailable", "Live view is not enabled on this server.")
		return
	}
	now := time.Now()
	token, err := jwt.NewWithClaims(jwt.SigningMethodHS256, liveClaims{
		StoreID: storeID,
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   user.ID,
			Issuer:    s.config.JWTIssuer,
			Audience:  jwt.ClaimStrings{liveAudience},
			IssuedAt:  jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(now.Add(liveTTL)),
		},
	}).SignedString([]byte(s.config.JWTAccessSecret))
	if err != nil {
		s.internalError(w, err)
		return
	}
	http.SetCookie(w, &http.Cookie{
		Name: liveCookie, Value: token, Path: "/live/", MaxAge: int(liveTTL.Seconds()),
		Expires: now.Add(liveTTL), HttpOnly: true, Secure: s.config.Environment == "production",
		SameSite: http.SameSiteStrictMode,
	})
	rows, err := s.db.Query(r.Context(), `SELECT camera_id FROM cameras WHERE store_id=$1 AND is_active=1 ORDER BY camera_id`, storeID)
	if err != nil {
		s.internalError(w, err)
		return
	}
	defer rows.Close()
	streams := map[string]string{}
	for rows.Next() {
		var cameraID string
		if err := rows.Scan(&cameraID); err != nil {
			s.internalError(w, err)
			return
		}
		streams[cameraID] = "/live/" + url.PathEscape(cameraID) + "/index.m3u8"
	}
	if err := rows.Err(); err != nil {
		s.internalError(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{
		"mode": s.config.LiveViewMode, "expiresInSeconds": int(liveTTL.Seconds()), "streams": streams,
	})
}

// authorizeLive answers the reverse proxy's forward_auth subrequest for one
// /live/<camera_id>/... request: 204 to let it through, 401/403 otherwise.
func (s *Server) authorizeLive(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if s.config.LiveViewMode == "" {
		w.WriteHeader(http.StatusServiceUnavailable)
		return
	}
	cameraID, ok := liveCameraFromURI(r.Header.Get("X-Forwarded-Uri"))
	if !ok {
		w.WriteHeader(http.StatusForbidden)
		return
	}
	cookie, err := r.Cookie(liveCookie)
	if err != nil {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	claims := &liveClaims{}
	token, err := jwt.ParseWithClaims(cookie.Value, claims, func(token *jwt.Token) (any, error) {
		if token.Method != jwt.SigningMethodHS256 {
			return nil, errors.New("unexpected JWT signing method")
		}
		return []byte(s.config.JWTAccessSecret), nil
	}, jwt.WithExpirationRequired(), jwt.WithIssuer(s.config.JWTIssuer), jwt.WithAudience(liveAudience))
	if err != nil || !token.Valid || claims.Subject == "" || claims.StoreID == "" {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	now := time.Now()
	key := claims.Subject + "\n" + claims.StoreID + "\n" + cameraID
	if s.live.allowed(key, now) {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	// Still a member with access, and the camera is that store's.
	var role string
	err = s.db.QueryRow(r.Context(), `SELECT os.role FROM owner_stores os JOIN owners o ON o.owner_id=os.owner_id
JOIN cameras c ON c.store_id=os.store_id
WHERE os.owner_id=$1 AND os.store_id=$2 AND c.camera_id=$3 AND o.status='active'`, claims.Subject, claims.StoreID, cameraID).Scan(&role)
	if errors.Is(err, pgx.ErrNoRows) || (err == nil && roleRank[role] < roleRank[roleOperator]) {
		w.WriteHeader(http.StatusForbidden)
		return
	}
	if err != nil {
		s.log.Error("live authorize failed", "error", err)
		w.WriteHeader(http.StatusServiceUnavailable)
		return
	}
	s.live.remember(key, now)
	w.WriteHeader(http.StatusNoContent)
}

// "/live/<camera_id>/index.m3u8?x" -> camera_id. Anything that is not exactly
// one path segment after /live/ followed by more path is refused.
func liveCameraFromURI(uri string) (string, bool) {
	parsed, err := url.Parse(uri)
	if err != nil {
		return "", false
	}
	rest, found := strings.CutPrefix(parsed.Path, "/live/")
	if !found {
		return "", false
	}
	cameraID, tail, found := strings.Cut(rest, "/")
	if !found || tail == "" || cameraID == "" || strings.Contains(tail, "..") || !liveCameraIDPattern.MatchString(cameraID) {
		return "", false
	}
	return cameraID, true
}
