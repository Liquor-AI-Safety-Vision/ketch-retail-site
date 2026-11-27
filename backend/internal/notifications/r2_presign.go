package notifications

// Presigned GET URLs for Cloudflare R2 (S3 API, AWS Signature Version 4,
// query-string form). The AI service uploads every clip and snapshot to R2
// and stores the object key in alert_media.path; this is how the API reads
// one back without the bucket ever being public.
//
// Written out rather than pulling in the AWS SDK for one request shape.
// TestSigV4MatchesAWSExample checks it against the worked example in the AWS
// documentation ("Authenticating Requests: Using Query Parameters").

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"
)

// R2Config names the bucket the AI service writes to. The variable names are
// the AI service's own (.env: R2_ENDPOINT, R2_BUCKET, R2_ACCESS_KEY_ID,
// R2_SECRET_ACCESS_KEY), so one file configures both.
type R2Config struct {
	Endpoint        string
	Bucket          string
	AccessKeyID     string
	SecretAccessKey string
}

func (c R2Config) Configured() bool {
	return strings.TrimSpace(c.Endpoint) != "" && strings.TrimSpace(c.Bucket) != "" &&
		strings.TrimSpace(c.AccessKeyID) != "" && strings.TrimSpace(c.SecretAccessKey) != ""
}

// R2Presigner turns an object key into a short-lived GET URL.
type R2Presigner struct {
	endpoint *url.URL
	bucket   string
	access   string
	secret   string
	ttl      time.Duration
	now      func() time.Time
}

// R2's region is always "auto".
const r2Region = "auto"

func NewR2Presigner(cfg R2Config, ttl time.Duration) (*R2Presigner, error) {
	if !cfg.Configured() {
		return nil, errors.New("R2_ENDPOINT, R2_BUCKET, R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY are all required")
	}
	endpoint, err := url.Parse(strings.TrimRight(strings.TrimSpace(cfg.Endpoint), "/"))
	if err != nil || endpoint.Host == "" || (endpoint.Scheme != "https" && endpoint.Scheme != "http") || endpoint.User != nil || endpoint.RawQuery != "" {
		return nil, errors.New("R2_ENDPOINT must be an absolute HTTP(S) URL without credentials or query")
	}
	if ttl <= 0 || ttl > 7*24*time.Hour {
		return nil, errors.New("presigned URL lifetime must be between 1s and 7 days")
	}
	return &R2Presigner{endpoint: endpoint, bucket: strings.TrimSpace(cfg.Bucket),
		access: strings.TrimSpace(cfg.AccessKeyID), secret: strings.TrimSpace(cfg.SecretAccessKey),
		ttl: ttl, now: time.Now}, nil
}

// PresignGET returns a path-style URL (endpoint/bucket/key) valid for ttl.
func (p *R2Presigner) PresignGET(key string) (string, error) {
	if err := validObjectKey(key); err != nil {
		return "", err
	}
	path := strings.TrimRight(p.endpoint.Path, "/") + "/" + awsURIEncode(p.bucket, false) + "/" + awsURIEncode(key, true)
	query := presignQuery(p.endpoint.Host, path, p.access, p.secret, r2Region, p.now().UTC(), p.ttl)
	return p.endpoint.Scheme + "://" + p.endpoint.Host + path + "?" + query, nil
}

// validObjectKey refuses anything that is not a relative object key -- in
// particular the absolute local paths alert_media.path holds when the AI
// service ran with local storage.
func validObjectKey(key string) error {
	if key == "" || strings.HasPrefix(key, "/") || strings.Contains(key, "\\") || strings.Contains(key, "://") || strings.ContainsRune(key, 0) {
		return errors.New("not an R2 object key")
	}
	for _, part := range strings.Split(key, "/") {
		if part == "" || part == "." || part == ".." {
			return errors.New("not an R2 object key")
		}
	}
	return nil
}

// presignQuery builds the signed query string for GET <host><canonicalPath>,
// with canonicalPath already URI-encoded. Only the host header is signed and
// the payload is UNSIGNED-PAYLOAD, as S3 presigned GETs require.
func presignQuery(host, canonicalPath, accessKey, secret, region string, now time.Time, ttl time.Duration) string {
	amzDate := now.Format("20060102T150405Z")
	date := now.Format("20060102")
	scope := date + "/" + region + "/s3/aws4_request"
	params := map[string]string{
		"X-Amz-Algorithm":     "AWS4-HMAC-SHA256",
		"X-Amz-Credential":    accessKey + "/" + scope,
		"X-Amz-Date":          amzDate,
		"X-Amz-Expires":       strconv.Itoa(int(ttl.Seconds())),
		"X-Amz-SignedHeaders": "host",
	}
	canonicalQuery := canonicalQueryString(params)
	canonicalRequest := strings.Join([]string{
		"GET", canonicalPath, canonicalQuery, "host:" + host + "\n", "host", "UNSIGNED-PAYLOAD",
	}, "\n")
	digest := sha256.Sum256([]byte(canonicalRequest))
	stringToSign := strings.Join([]string{"AWS4-HMAC-SHA256", amzDate, scope, hex.EncodeToString(digest[:])}, "\n")

	key := hmacSHA256([]byte("AWS4"+secret), date)
	key = hmacSHA256(key, region)
	key = hmacSHA256(key, "s3")
	key = hmacSHA256(key, "aws4_request")
	signature := hex.EncodeToString(hmacSHA256(key, stringToSign))
	return canonicalQuery + "&X-Amz-Signature=" + signature
}

func canonicalQueryString(params map[string]string) string {
	keys := make([]string, 0, len(params))
	for k := range params {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, awsURIEncode(k, false)+"="+awsURIEncode(params[k], false))
	}
	return strings.Join(parts, "&")
}

// awsURIEncode is SigV4's UriEncode: every byte except A-Z a-z 0-9 - _ . ~ is
// percent-encoded with uppercase hex; "/" is kept only in object key paths.
func awsURIEncode(value string, keepSlash bool) string {
	const hexDigits = "0123456789ABCDEF"
	var b strings.Builder
	for i := 0; i < len(value); i++ {
		c := value[i]
		switch {
		case c >= 'A' && c <= 'Z', c >= 'a' && c <= 'z', c >= '0' && c <= '9', c == '-', c == '_', c == '.', c == '~':
			b.WriteByte(c)
		case c == '/' && keepSlash:
			b.WriteByte(c)
		default:
			b.WriteByte('%')
			b.WriteByte(hexDigits[c>>4])
			b.WriteByte(hexDigits[c&0x0f])
		}
	}
	return b.String()
}

func hmacSHA256(key []byte, data string) []byte {
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(data))
	return mac.Sum(nil)
}
