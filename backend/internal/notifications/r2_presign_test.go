package notifications

import (
	"net/url"
	"strings"
	"testing"
	"time"
)

// The worked example from the AWS documentation for query-string SigV4
// ("Authenticating Requests: Using Query Parameters (AWS Signature Version
// 4)", Amazon S3 API Reference): GET /test.txt on examplebucket, us-east-1,
// 2013-05-24T00:00:00Z, 86400 s. The published signature is below; if the
// canonical request, string to sign or key derivation is off by one byte,
// this fails.
func TestSigV4MatchesAWSExample(t *testing.T) {
	now := time.Date(2013, 5, 24, 0, 0, 0, 0, time.UTC)
	query := presignQuery("examplebucket.s3.amazonaws.com", "/test.txt",
		"AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1", now, 86400*time.Second)
	const want = "X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"
	if !strings.HasSuffix(query, want) {
		t.Fatalf("signature mismatch:\n got %s\nwant suffix %s", query, want)
	}
	if !strings.HasPrefix(query, "X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20130524%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20130524T000000Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=host&") {
		t.Fatalf("canonical query mismatch: %s", query)
	}
}

func TestR2PresignerBuildsPathStyleURLsForAIKeys(t *testing.T) {
	p, err := NewR2Presigner(R2Config{Endpoint: "https://acct.r2.cloudflarestorage.com/", Bucket: "liquir",
		AccessKeyID: "AK", SecretAccessKey: "SK"}, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	p.now = func() time.Time { return time.Date(2026, 11, 27, 1, 2, 3, 0, time.UTC) }
	// The shape CloudAlertSink uploads under (ai/src/infra/cloud_alert_sink.py):
	// alerts/<alert_id>/<file name>. The space checks encoding.
	raw, err := p.PresignGET("alerts/0b6e9c1e-7d1a-4c55-9f0e-2a1c3b4d5e6f/evidence clip.mp4")
	if err != nil {
		t.Fatal(err)
	}
	u, err := url.Parse(raw)
	if err != nil {
		t.Fatal(err)
	}
	if u.Host != "acct.r2.cloudflarestorage.com" || u.EscapedPath() != "/liquir/alerts/0b6e9c1e-7d1a-4c55-9f0e-2a1c3b4d5e6f/evidence%20clip.mp4" {
		t.Fatalf("url = %s", raw)
	}
	if u.Query().Get("X-Amz-Expires") != "60" || !strings.Contains(u.Query().Get("X-Amz-Credential"), "/auto/s3/aws4_request") || len(u.Query().Get("X-Amz-Signature")) != 64 {
		t.Fatalf("query = %v", u.Query())
	}

	for _, bad := range []string{"", "/Users/x/ai/data/split_alerts/a/clip.mp4", "../etc/passwd", "a//b", "https://evil/x", `a\b`} {
		if _, err := p.PresignGET(bad); err == nil {
			t.Errorf("PresignGET(%q) accepted a key that is not an object key", bad)
		}
	}
	if _, err := NewR2Presigner(R2Config{Endpoint: "https://x", Bucket: "b"}, time.Minute); err == nil {
		t.Fatal("missing credentials were accepted")
	}
}
