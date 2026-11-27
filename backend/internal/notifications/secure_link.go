package notifications

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var (
	ErrSecureLinkUnavailable = errors.New("secure review links are not configured")
	ErrSecureLinkNotFound    = errors.New("secure review link was not found")
)

// ReviewLinkInput names one alert_media row (a clip or snapshot the AI
// service uploaded) of one alert of one store.
type ReviewLinkInput struct {
	StoreID    string
	AlertID    string
	MediaID    string
	DeliveryID string
}

// ObjectURLSigner turns an R2 object key into a short-lived GET URL.
type ObjectURLSigner interface {
	PresignGET(key string) (string, error)
}

type ReviewLinkBuilder interface {
	CreateReviewLink(ctx context.Context, input ReviewLinkInput) (string, error)
}

// SecureReviewService issues opaque, expiring links to one piece of alert
// media and serves them by fetching the object from R2 through a presigned
// URL. The bucket stays private; the browser only ever sees this API's URL.
type SecureReviewService struct {
	db            *pgxpool.Pool
	publicBaseURL string
	objects       ObjectURLSigner
	ttl           time.Duration
	httpClient    *http.Client
	now           func() time.Time
}

func NewSecureReviewService(db *pgxpool.Pool, publicBaseURL string, objects ObjectURLSigner, ttl time.Duration) (*SecureReviewService, error) {
	if db == nil || strings.TrimSpace(publicBaseURL) == "" || objects == nil || ttl <= 0 {
		return nil, ErrSecureLinkUnavailable
	}
	publicURL, err := parseFixedOrigin(publicBaseURL)
	if err != nil {
		return nil, err
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.ResponseHeaderTimeout = 10 * time.Second
	client := &http.Client{
		Transport: transport,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	return &SecureReviewService{
		db: db, publicBaseURL: strings.TrimRight(publicURL.String(), "/"),
		objects: objects, ttl: ttl, httpClient: client, now: time.Now,
	}, nil
}

func parseFixedOrigin(raw string) (*url.URL, error) {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, errors.New("secure review origin must be an absolute HTTP(S) URL without credentials, query, or fragment")
	}
	return parsed, nil
}

func (s *SecureReviewService) CreateReviewLink(ctx context.Context, input ReviewLinkInput) (string, error) {
	if s == nil || s.db == nil {
		return "", ErrSecureLinkUnavailable
	}
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	token := base64.RawURLEncoding.EncodeToString(raw)
	digest := sha256.Sum256([]byte(token))
	expiresAt := s.now().UTC().Add(s.ttl)
	var deliveryID any
	if strings.TrimSpace(input.DeliveryID) != "" {
		deliveryID = input.DeliveryID
	}
	var id string
	err := s.db.QueryRow(ctx, `INSERT INTO "notification_video_links" ("id","tokenHash","storeId","alertId","mediaId","deliveryId","expiresAt") SELECT $1,$2,a.store_id,m.alert_id,m.media_id,$6,$7 FROM alert_media m JOIN alerts a ON a.alert_id=m.alert_id WHERE m.media_id=$3 AND m.alert_id=$4 AND a.store_id=$5 RETURNING "id"`,
		uuid.NewString(), digest[:], input.MediaID, input.AlertID, input.StoreID, deliveryID, expiresAt).Scan(&id)
	if err != nil {
		if err == pgx.ErrNoRows {
			return "", ErrSecureLinkNotFound
		}
		return "", err
	}
	return s.publicBaseURL + "/api/v1/notification-review/" + token, nil
}

func (s *SecureReviewService) ServeToken(w http.ResponseWriter, r *http.Request, token string) {
	if s == nil || s.db == nil {
		http.Error(w, "Review video is temporarily unavailable.", http.StatusServiceUnavailable)
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		http.Error(w, "Method not allowed.", http.StatusMethodNotAllowed)
		return
	}
	if len(token) < 40 || len(token) > 64 {
		http.NotFound(w, r)
		return
	}
	digest := sha256.Sum256([]byte(token))
	var objectKey, mimeType string
	err := s.db.QueryRow(r.Context(), `UPDATE "notification_video_links" l SET "accessCount"=l."accessCount"+1,"lastAccessedAt"=NOW() FROM alert_media m WHERE l."tokenHash"=$1 AND l."revokedAt" IS NULL AND l."expiresAt">NOW() AND m.media_id=l."mediaId" AND m.alert_id=l."alertId" RETURNING m.path, COALESCE(m.mime_type,'')`, digest[:]).Scan(&objectKey, &mimeType)
	if err != nil {
		if err == pgx.ErrNoRows {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "Review video is temporarily unavailable.", http.StatusServiceUnavailable)
		return
	}
	originURL, err := s.objects.PresignGET(objectKey)
	if err != nil {
		// A local file path: the AI service ran with --storage-mode local-db,
		// so this media never reached R2.
		http.NotFound(w, r)
		return
	}
	request, err := http.NewRequestWithContext(r.Context(), r.Method, originURL, nil)
	if err != nil {
		http.Error(w, "Review video is temporarily unavailable.", http.StatusServiceUnavailable)
		return
	}
	for _, header := range []string{"Range", "If-Range", "If-None-Match", "If-Modified-Since"} {
		if value := r.Header.Get(header); value != "" {
			request.Header.Set(header, value)
		}
	}
	response, err := s.httpClient.Do(request)
	if err != nil {
		http.Error(w, "Review video is temporarily unavailable.", http.StatusBadGateway)
		return
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK && response.StatusCode != http.StatusPartialContent && response.StatusCode != http.StatusNotModified {
		if response.StatusCode == http.StatusNotFound {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "Review video is temporarily unavailable.", http.StatusBadGateway)
		return
	}
	for _, header := range []string{"Accept-Ranges", "Content-Length", "Content-Range", "ETag", "Last-Modified"} {
		if value := response.Header.Get(header); value != "" {
			w.Header().Set(header, value)
		}
	}
	if mimeType == "" {
		mimeType = response.Header.Get("Content-Type")
	}
	if mimeType != "" {
		w.Header().Set("Content-Type", mimeType)
	}
	w.Header().Set("Cache-Control", "private, no-store, max-age=0")
	w.Header().Set("Content-Disposition", `inline; filename="alert-evidence"`)
	w.WriteHeader(response.StatusCode)
	if r.Method == http.MethodGet && response.StatusCode != http.StatusNotModified {
		_, _ = io.Copy(w, response.Body)
	}
}
