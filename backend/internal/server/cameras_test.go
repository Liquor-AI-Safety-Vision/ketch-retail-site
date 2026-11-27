package server

import (
	"testing"
	"time"
)

func TestCameraStatusFromHeartbeat(t *testing.T) {
	now := time.Date(2026, 11, 27, 3, 0, 0, 0, time.UTC)
	at := func(d time.Duration) *string {
		value := now.Add(-d).Format("2006-01-02T15:04:05.000000+00:00")
		return &value
	}
	bad := "not a time"
	for _, c := range []struct {
		active bool
		seen   *string
		want   string
	}{
		{true, at(10 * time.Second), "online"},
		{true, at(cameraOfflineAfter - time.Second), "online"},
		{true, at(cameraOfflineAfter + time.Second), "offline"},
		{true, nil, "unknown"},
		{true, &bad, "unknown"},
		{false, at(time.Second), "inactive"},
	} {
		if got := cameraStatus(c.active, c.seen, now); got != c.want {
			t.Errorf("cameraStatus(%v, %v) = %s, want %s", c.active, c.seen, got, c.want)
		}
	}
}
