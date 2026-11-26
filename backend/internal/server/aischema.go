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
