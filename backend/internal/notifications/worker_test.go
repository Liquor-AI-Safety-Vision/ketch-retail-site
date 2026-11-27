package notifications

import (
	"errors"
	"strings"
	"testing"
	"time"
)

func TestWorkerRetryDelay(t *testing.T) {
	worker := NewWorker(nil, nil, nil, nil, WorkerOptions{BaseBackoff: 2 * time.Second, MaxBackoff: 30 * time.Second})
	cases := []struct {
		attempt       int
		providerDelay time.Duration
		want          time.Duration
	}{
		{attempt: 1, want: 2 * time.Second},
		{attempt: 2, want: 4 * time.Second},
		{attempt: 5, want: 30 * time.Second},
		{attempt: 1, providerDelay: 9 * time.Second, want: 9 * time.Second},
		{attempt: 1, providerDelay: time.Hour, want: time.Hour},
		{attempt: 1, providerDelay: 24 * time.Hour, want: time.Hour},
	}
	for _, tc := range cases {
		if got := worker.retryDelay(tc.attempt, tc.providerDelay); got != tc.want {
			t.Fatalf("attempt %d: retry delay = %s, want %s", tc.attempt, got, tc.want)
		}
	}
}

func TestClassifyWorkerErrorDoesNotPersistDetails(t *testing.T) {
	tokenLikeDetail := "123456789:AASecretValue"
	code, transient, retryAfter := classifyWorkerError(&PermanentSendError{Code: "telegram_forbidden", Detail: tokenLikeDetail})
	if code != "telegram_forbidden" || transient || retryAfter != 0 {
		t.Fatalf("unexpected permanent classification: %q %v %s", code, transient, retryAfter)
	}
	code, transient, retryAfter = classifyWorkerError(&TransientSendError{Code: "telegram_rate_limited", RetryAfter: 7 * time.Second})
	if code != "telegram_rate_limited" || !transient || retryAfter != 7*time.Second {
		t.Fatalf("unexpected transient classification: %q %v %s", code, transient, retryAfter)
	}
	code, transient, _ = classifyWorkerError(errors.New(tokenLikeDetail))
	if code != WorkerCodeUnexpectedError || !transient {
		t.Fatalf("unexpected unknown-error classification: %q %v", code, transient)
	}
}

func TestSafeResponseMetadataUsesAllowlist(t *testing.T) {
	raw, status := safeResponseMetadata(map[string]any{
		"httpStatus": 200,
		"token":      "must-not-persist",
		"chat_id":    "must-not-persist",
	})
	if status == nil || *status != 200 {
		t.Fatalf("response status = %v", status)
	}
	text := string(raw)
	if text != `{"httpStatus":200}` {
		t.Fatalf("safe metadata = %s", text)
	}
}

// The object fetched for a review link is always inside the configured
// bucket: alert_media.path is used as a key, never as a URL or a path that
// climbs out of it.
func TestSecureReviewObjectKeysAreTraversalSafe(t *testing.T) {
	p, err := NewR2Presigner(R2Config{Endpoint: "https://acct.r2.cloudflarestorage.com", Bucket: "private",
		AccessKeyID: "AK", SecretAccessKey: "SK"}, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	got, err := p.PresignGET("alerts/clip 01.mp4")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(got, "https://acct.r2.cloudflarestorage.com/private/alerts/clip%2001.mp4?") {
		t.Fatalf("object URL = %q", got)
	}
	for _, key := range []string{"https://attacker.test/video", "../secret", "/absolute", `folder\\secret`, "folder//clip"} {
		if _, err := p.PresignGET(key); err == nil {
			t.Fatalf("storage key %q should be rejected", key)
		}
	}
	if _, err := parseFixedOrigin("https://api.example.test/?q=1"); err == nil {
		t.Fatal("a public base URL with a query was accepted")
	}
}
