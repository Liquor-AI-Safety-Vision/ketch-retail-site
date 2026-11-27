package config

import (
	"strings"
	"testing"
)

func setMinimumTestEnvironment(t *testing.T) {
	t.Helper()
	t.Setenv("NODE_ENV", "development")
	t.Setenv("DATABASE_URL", "postgresql://user:password@example.test/database")
	t.Setenv("DIRECT_URL", "")
	t.Setenv("NOTIFICATION_CREDENTIAL_BINDINGS", "")
	t.Setenv("TRUSTED_PROXY_CIDRS", "")
	t.Setenv("NOTIFICATION_WORKER_ENABLED", "false")
	t.Setenv("PUBLIC_API_BASE_URL", "")
	for _, name := range []string{"R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"} {
		t.Setenv(name, "")
	}
	t.Setenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")
	t.Setenv("WHATSAPP_APP_SECRET", "")
}

func TestNotificationWorkerRequiresSecureReviewOrigins(t *testing.T) {
	setMinimumTestEnvironment(t)
	t.Setenv("NOTIFICATION_WORKER_ENABLED", "true")
	if _, err := Load(); err == nil || !strings.Contains(err.Error(), "PUBLIC_API_BASE_URL") {
		t.Fatalf("expected missing review origins to fail, got %v", err)
	}
	t.Setenv("PUBLIC_API_BASE_URL", "https://api.example.test")
	if _, err := Load(); err == nil || !strings.Contains(err.Error(), "R2_ENDPOINT") {
		t.Fatalf("worker without R2 must fail to start (review links read clips from R2): %v", err)
	}
	t.Setenv("R2_ENDPOINT", "https://acct.r2.cloudflarestorage.com")
	t.Setenv("R2_BUCKET", "liquir")
	t.Setenv("R2_ACCESS_KEY_ID", "AK")
	t.Setenv("R2_SECRET_ACCESS_KEY", "SK")
	if _, err := Load(); err == nil || !strings.Contains(err.Error(), "NOTIFICATION_CREDENTIAL_BINDINGS") {
		t.Fatalf("unscoped worker must fail to start: %v", err)
	}
	t.Setenv("NOTIFICATION_CREDENTIAL_BINDINGS", `[{"storeId":"store_test_01","provider":"TELEGRAM","providerAccountRef":"","credentialRef":"env://TELEGRAM_BOT_TOKEN"}]`)
	if _, err := Load(); err != nil {
		t.Fatalf("valid worker origins rejected: %v", err)
	}
}

func TestWhatsAppWebhookSecretsMustBePaired(t *testing.T) {
	setMinimumTestEnvironment(t)
	t.Setenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "verify-token")
	if _, err := Load(); err == nil || !strings.Contains(err.Error(), "configured together") {
		t.Fatalf("expected unpaired webhook secret to fail, got %v", err)
	}
}

func TestTrustedProxyCIDRConfiguration(t *testing.T) {
	setMinimumTestEnvironment(t)
	for _, raw := range []string{"*", "0.0.0.0/0", "::/0", "10.0.0.1", "10.0.0.0/24,"} {
		t.Setenv("TRUSTED_PROXY_CIDRS", raw)
		if _, err := Load(); err == nil {
			t.Fatalf("unsafe proxy config accepted: %q", raw)
		}
	}
	t.Setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/24, 2001:db8:1::/64")
	cfg, err := Load()
	if err != nil || len(cfg.TrustedProxyCIDRs) != 2 {
		t.Fatalf("valid proxy config rejected: %v", err)
	}

	// The deployed Render value: its ingress reaches the process over loopback and
	// appends its own private address, so both must load unchanged.
	deployed := "::1/128,127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"
	t.Setenv("TRUSTED_PROXY_CIDRS", deployed)
	cfg, err = Load()
	if err != nil || len(cfg.TrustedProxyCIDRs) != 6 {
		t.Fatalf("deployed ingress proxy config rejected: %v", err)
	}
	for i, want := range strings.Split(deployed, ",") {
		if cfg.TrustedProxyCIDRs[i].String() != want {
			t.Fatalf("prefix %d altered during load: got %s want %s", i, cfg.TrustedProxyCIDRs[i], want)
		}
	}
}

// In production every public origin must be HTTPS, except loopback: that is
// only reachable through an SSH tunnel, and browsers treat it as secure.
func TestProductionOriginsMustBeHTTPSExceptLoopback(t *testing.T) {
	setMinimumTestEnvironment(t)
	t.Setenv("NODE_ENV", "production")
	t.Setenv("JWT_ACCESS_SECRET", "production-test-secret-0123456789abcdef0123")
	for origin, ok := range map[string]bool{
		"https://dashboard.example.com": true,
		"http://localhost:18000":        true,
		"http://127.0.0.1:18000":        true,
		"http://[::1]:18000":            true,
		"http://dashboard.example.com":  false,
		"http://89.121.253.244:20565":   false,
		"http://localhost.example.com":  false,
	} {
		t.Setenv("WEB_ORIGIN", origin)
		t.Setenv("PUBLIC_API_BASE_URL", origin)
		_, err := Load()
		if ok && err != nil {
			t.Errorf("%s rejected: %v", origin, err)
		}
		if !ok && (err == nil || !strings.Contains(err.Error(), "HTTPS")) {
			t.Errorf("%s accepted in production: %v", origin, err)
		}
	}
}
