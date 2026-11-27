package server

// Conventions of the AI service's schema (ai/sql/postgres_schema.sql) that
// this API has to follow when it writes into those tables.

import (
	"crypto/rand"
	"encoding/hex"
	"time"
)

// isoTimeLayout matches Python's datetime.isoformat() for an aware UTC time
// with microseconds -- the format ai/ writes into every *_at TEXT column.
// Keeping one format means text order stays time order (opened_at is sorted
// as text by an index).
const isoTimeLayout = "2006-01-02T15:04:05.000000+00:00"

func isoTime(t time.Time) string { return t.UTC().Format(isoTimeLayout) }

func isoNow() string { return isoTime(time.Now()) }

// parseISOTime reads a *_at TEXT column written by either side.
func parseISOTime(value string) (time.Time, error) {
	return time.Parse(time.RFC3339Nano, value)
}

// newOwnerID follows the AI service's former owner seeding: "owner_" plus 12
// hex characters.
func newOwnerID() (string, error) {
	raw := make([]byte, 6)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	return "owner_" + hex.EncodeToString(raw), nil
}

// Values of owners.status and owner_stores.role.
const (
	ownerActive = "active"

	roleOwner    = "owner"
	roleManager  = "manager"
	roleOperator = "operator"
)

var roleRank = map[string]int{roleOperator: 1, roleManager: 2, roleOwner: 3}

// alerts.event_type values the AI pipelines write (ai/src/pipelines/*.py).
// weapon_detected has no pipeline yet; it is accepted so a weapon module can
// start writing it without an API change.
const (
	eventCashConcealment      = "suspected_cash_concealment" // counter_cash_theft_v1
	eventUnknownEntry         = "unknown_entry"              // storage/cold_storage_intrusion_v1
	eventStaffOutsideSchedule = "staff_outside_schedule"     // storage/cold_storage_intrusion_v1
	eventWatchlistMatch       = "watchlist_match"            // entrance_face_capture_v1
	eventWeaponDetected       = "weapon_detected"            // reserved for the weapon module
)

var alertEventTypes = []string{eventCashConcealment, eventUnknownEntry, eventStaffOutsideSchedule, eventWatchlistMatch, eventWeaponDetected}

// alerts.severity: ai/ writes warning and high; critical is reserved.
var alertSeverities = []string{"warning", "high", "critical"}

// alerts.status: ai/ inserts 'open'; everything after is a review decision.
const (
	statusOpen         = "open"
	statusAcknowledged = "acknowledged"
	statusConfirmed    = "confirmed"
	statusDismissed    = "dismissed"
)
