package security

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"golang.org/x/crypto/argon2"
	"golang.org/x/crypto/bcrypt"
)

const (
	argonMemory      = 64 * 1024
	argonIterations  = 3
	argonParallelism = 4
	argonSaltLength  = 16
	argonKeyLength   = 32
)

func HashPassword(password string) (string, error) {
	salt := make([]byte, argonSaltLength)
	if _, err := rand.Read(salt); err != nil {
		return "", fmt.Errorf("generate password salt: %w", err)
	}
	hash := argon2.IDKey([]byte(password), salt, argonIterations, argonMemory, argonParallelism, argonKeyLength)
	return fmt.Sprintf("$argon2id$v=19$m=%d,t=%d,p=%d$%s$%s", argonMemory, argonIterations, argonParallelism,
		base64.RawStdEncoding.EncodeToString(salt), base64.RawStdEncoding.EncodeToString(hash)), nil
}

// Highest bcrypt cost accepted from the database. The AI service's former
// owner seeding (Python bcrypt.gensalt()) used 12; anything far above that is
// either tampering or a mistake, and verifying it would let one login request
// burn seconds of CPU.
const maxLegacyBcryptCost = 14

// IsLegacyHash reports whether encoded is a bcrypt hash, the format owner
// accounts were created with before logins moved to this API. Such a hash
// still verifies, and is replaced with Argon2id on the next successful login.
func IsLegacyHash(encoded string) bool {
	return strings.HasPrefix(encoded, "$2a$") || strings.HasPrefix(encoded, "$2b$") || strings.HasPrefix(encoded, "$2y$")
}

func VerifyPassword(password, encoded string) (bool, error) {
	if IsLegacyHash(encoded) {
		return verifyBcrypt(password, encoded)
	}
	parts := strings.Split(encoded, "$")
	if len(parts) != 6 || parts[1] != "argon2id" || parts[2] != "v=19" {
		return false, errors.New("unsupported password hash format")
	}
	var memory uint64
	var iterations uint64
	var parallelism uint64
	for _, field := range strings.Split(parts[3], ",") {
		pair := strings.SplitN(field, "=", 2)
		if len(pair) != 2 {
			return false, errors.New("invalid argon2 parameters")
		}
		value, err := strconv.ParseUint(pair[1], 10, 32)
		if err != nil {
			return false, errors.New("invalid argon2 parameters")
		}
		switch pair[0] {
		case "m":
			memory = value
		case "t":
			iterations = value
		case "p":
			parallelism = value
		}
	}
	// Bound database-supplied PHC parameters so a malformed or tampered hash
	// cannot force unbounded CPU or memory allocation during login.
	if memory < 8*1024 || memory > 256*1024 || iterations == 0 || iterations > 10 || parallelism == 0 || parallelism > 16 {
		return false, errors.New("invalid argon2 parameters")
	}
	salt, err := base64.RawStdEncoding.DecodeString(parts[4])
	if err != nil || len(salt) < 8 || len(salt) > 64 {
		return false, errors.New("invalid argon2 salt")
	}
	expected, err := base64.RawStdEncoding.DecodeString(parts[5])
	if err != nil || len(expected) < 16 || len(expected) > 64 {
		return false, errors.New("invalid argon2 hash")
	}
	// #nosec G115 -- ParseUint used a 32-bit ceiling and the stricter bounds above are enforced before conversion.
	actual := argon2.IDKey([]byte(password), salt, uint32(iterations), uint32(memory), uint8(parallelism), uint32(len(expected)))
	return subtle.ConstantTimeCompare(actual, expected) == 1, nil
}

func verifyBcrypt(password, encoded string) (bool, error) {
	cost, err := bcrypt.Cost([]byte(encoded))
	if err != nil {
		return false, errors.New("invalid bcrypt hash")
	}
	if cost > maxLegacyBcryptCost {
		return false, errors.New("bcrypt cost is above the accepted maximum")
	}
	// bcrypt only reads the first 72 bytes. Refuse rather than compare a
	// truncated prefix, so a long password never matches by its first 72 bytes.
	if len(password) > 72 {
		return false, nil
	}
	switch err := bcrypt.CompareHashAndPassword([]byte(encoded), []byte(password)); {
	case err == nil:
		return true, nil
	case errors.Is(err, bcrypt.ErrMismatchedHashAndPassword):
		return false, nil
	default:
		return false, errors.New("invalid bcrypt hash")
	}
}
