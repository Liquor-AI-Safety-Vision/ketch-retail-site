package server

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/liquor-store/security-api/internal/security"
)

// #nosec G101 -- this public test hash is used only to equalize failed-login timing and cannot authenticate any account.
const dummyPasswordHash = "$argon2id$v=19$m=65536,p=4,t=3$tRJklxOdZO9aABYGMhSuPQ$efdf+S6xCWV0Qd61ZJlhMSYxZUR4weZFzfXrlb2FE9A"

// Registration links a new owner to a store that already exists. Stores are
// created by the AI service from its manifest (ai/config/stores/*.json), so
// there is nothing for this API to create. Only mounted when REGISTER_ENABLED.
type registerRequest struct {
	Email       string `json:"email"`
	Password    string `json:"password"`
	DisplayName string `json:"displayName"`
	StoreID     string `json:"storeId"`
}

type loginRequest struct {
	Email    string `json:"email"`
	Password string `json:"password"`
}

type sessionResponse struct {
	AccessToken string      `json:"accessToken"`
	User        currentUser `json:"user"`
}

func (s *Server) register(w http.ResponseWriter, r *http.Request) {
	var input registerRequest
	if !decodeJSON(w, r, &input) {
		return
	}
	input.Email = strings.ToLower(strings.TrimSpace(input.Email))
	input.DisplayName = strings.TrimSpace(input.DisplayName)
	input.StoreID = strings.TrimSpace(input.StoreID)
	if !validEmail(input.Email) || len(input.Password) < 12 || len(input.Password) > 128 ||
		!validLength(input.DisplayName, 2, 100) || !validLength(input.StoreID, 1, 200) {
		writeError(w, http.StatusBadRequest, "Bad Request", "Registration details are invalid.")
		return
	}

	passwordHash, err := security.HashPassword(input.Password)
	if err != nil {
		s.internalError(w, err)
		return
	}
	ownerID, err := newOwnerID()
	if err != nil {
		s.internalError(w, err)
		return
	}
	user := authUser{ID: ownerID, Email: input.Email, DisplayName: input.DisplayName, PasswordHash: passwordHash, Status: ownerActive}
	tx, err := s.db.Begin(r.Context())
	if err != nil {
		s.internalError(w, err)
		return
	}
	defer func() { _ = tx.Rollback(r.Context()) }()
	var storeExists bool
	if err := tx.QueryRow(r.Context(), `SELECT EXISTS(SELECT 1 FROM stores WHERE store_id=$1)`, input.StoreID).Scan(&storeExists); err != nil {
		s.internalError(w, err)
		return
	}
	if !storeExists {
		writeError(w, http.StatusNotFound, "Not Found", "Store was not found. Stores come from the AI service's manifest; start it once to create the store.")
		return
	}
	now := isoNow()
	if _, err = tx.Exec(r.Context(), `INSERT INTO owners (owner_id, display_name, email, password_hash, status, metadata_json, created_at, updated_at) VALUES ($1,$2,$3,$4,$5,'{}',$6,$6)`,
		user.ID, user.DisplayName, user.Email, passwordHash, ownerActive, now); err != nil {
		if uniqueViolation(err) {
			writeError(w, http.StatusConflict, "Conflict", "Email is already in use.")
			return
		}
		s.internalError(w, err)
		return
	}
	if _, err = tx.Exec(r.Context(), `INSERT INTO owner_stores (owner_id, store_id, role, created_at) VALUES ($1,$2,$3,$4)`, user.ID, input.StoreID, roleOwner, now); err != nil {
		s.internalError(w, err)
		return
	}
	if err = tx.Commit(r.Context()); err != nil {
		s.internalError(w, err)
		return
	}

	session, refreshToken, err := s.createSession(r.Context(), user, r)
	if err != nil {
		s.internalError(w, err)
		return
	}
	s.setRefreshCookie(w, refreshToken)
	writeJSON(w, http.StatusCreated, session)
}

func (s *Server) login(w http.ResponseWriter, r *http.Request) {
	var input loginRequest
	if !decodeJSON(w, r, &input) {
		return
	}
	input.Email = strings.ToLower(strings.TrimSpace(input.Email))
	if !validEmail(input.Email) || len(input.Password) < 1 || len(input.Password) > 128 {
		writeError(w, http.StatusBadRequest, "Bad Request", "Email or password is invalid.")
		return
	}
	var user authUser
	err := s.db.QueryRow(r.Context(), `SELECT owner_id, email, display_name, password_hash, status FROM owners WHERE lower(email)=$1`, input.Email).
		Scan(&user.ID, &user.Email, &user.DisplayName, &user.PasswordHash, &user.Status)
	if err != nil {
		if err == pgx.ErrNoRows {
			_, _ = security.VerifyPassword(input.Password, dummyPasswordHash)
			writeError(w, http.StatusUnauthorized, "Unauthorized", "Invalid email or password.")
			return
		}
		s.internalError(w, err)
		return
	}
	verified, verifyErr := security.VerifyPassword(input.Password, user.PasswordHash)
	if verifyErr != nil || !verified || user.Status != ownerActive {
		writeError(w, http.StatusUnauthorized, "Unauthorized", "Invalid email or password.")
		return
	}
	if security.IsLegacyHash(user.PasswordHash) {
		// An account created before logins moved here (bcrypt, by the AI
		// service's seeding). The password was just proven, so replace the
		// hash with Argon2id; failing to is logged, never a failed login.
		if rehashed, err := security.HashPassword(input.Password); err == nil {
			if _, err := s.db.Exec(r.Context(), `UPDATE owners SET password_hash=$1, updated_at=$2 WHERE owner_id=$3 AND password_hash=$4`, rehashed, isoNow(), user.ID, user.PasswordHash); err != nil {
				s.log.Warn("could not upgrade legacy password hash", "owner", user.ID, "error", err)
			}
		}
	}
	session, refreshToken, err := s.createSession(r.Context(), user, r)
	if err != nil {
		s.internalError(w, err)
		return
	}
	s.setRefreshCookie(w, refreshToken)
	writeJSON(w, http.StatusCreated, session)
}

func (s *Server) refresh(w http.ResponseWriter, r *http.Request) {
	cookie, err := r.Cookie("refresh_token")
	if err != nil || cookie.Value == "" {
		writeError(w, http.StatusUnauthorized, "Unauthorized", "Refresh token is missing.")
		return
	}
	tx, err := s.db.Begin(r.Context())
	if err != nil {
		s.internalError(w, err)
		return
	}
	defer func() { _ = tx.Rollback(r.Context()) }()

	var userID string
	err = tx.QueryRow(r.Context(), `UPDATE "refresh_sessions" SET "revokedAt"=NOW(), "updatedAt"=NOW()
		WHERE "tokenHash"=$1 AND "revokedAt" IS NULL AND "expiresAt">NOW() RETURNING "ownerId"`, hashRefreshToken(cookie.Value)).Scan(&userID)
	if err != nil {
		writeError(w, http.StatusUnauthorized, "Unauthorized", "Refresh token is invalid or expired.")
		return
	}
	var user authUser
	err = tx.QueryRow(r.Context(), `SELECT owner_id, email, display_name, password_hash, status FROM owners WHERE owner_id=$1 AND status='active'`, userID).
		Scan(&user.ID, &user.Email, &user.DisplayName, &user.PasswordHash, &user.Status)
	if err != nil {
		writeError(w, http.StatusUnauthorized, "Unauthorized", "Refresh token is invalid or expired.")
		return
	}
	session, refreshToken, err := s.createSessionWith(r.Context(), tx, user, r)
	if err != nil {
		s.internalError(w, err)
		return
	}
	if err = tx.Commit(r.Context()); err != nil {
		s.internalError(w, err)
		return
	}
	s.setRefreshCookie(w, refreshToken)
	writeJSON(w, http.StatusCreated, session)
}

func (s *Server) logout(w http.ResponseWriter, r *http.Request) {
	if cookie, err := r.Cookie("refresh_token"); err == nil && cookie.Value != "" {
		_, _ = s.db.Exec(r.Context(), `UPDATE "refresh_sessions" SET "revokedAt"=NOW(), "updatedAt"=NOW() WHERE "tokenHash"=$1`, hashRefreshToken(cookie.Value))
	}
	// #nosec G124 -- Secure is mandatory in production; localhost development intentionally uses HTTP.
	http.SetCookie(w, &http.Cookie{Name: "refresh_token", Value: "", Path: "/api/v1/auth", MaxAge: -1, HttpOnly: true, Secure: s.config.Environment == "production", SameSite: s.cookieSameSite()})
	writeJSON(w, http.StatusCreated, map[string]bool{"success": true})
}

func (s *Server) authMe(w http.ResponseWriter, r *http.Request, userPrincipal principal) {
	user, err := s.loadCurrentUser(r.Context(), userPrincipal.ID)
	if err != nil {
		if err == pgx.ErrNoRows {
			writeError(w, http.StatusUnauthorized, "Unauthorized", "User is not active.")
			return
		}
		s.internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, user)
}

func (s *Server) userProfile(w http.ResponseWriter, r *http.Request, user principal) {
	var response struct {
		ID          string `json:"id"`
		Email       string `json:"email"`
		DisplayName string `json:"displayName"`
		Status      string `json:"status"`
		CreatedAt   string `json:"createdAt"`
	}
	err := s.db.QueryRow(r.Context(), `SELECT owner_id, email, display_name, status, created_at FROM owners WHERE owner_id=$1`, user.ID).
		Scan(&response.ID, &response.Email, &response.DisplayName, &response.Status, &response.CreatedAt)
	if err != nil {
		if err == pgx.ErrNoRows {
			writeError(w, http.StatusNotFound, "Not Found", "User was not found.")
			return
		}
		s.internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) updateUserProfile(w http.ResponseWriter, r *http.Request, user principal) {
	var input struct {
		DisplayName string `json:"displayName"`
	}
	if !decodeJSON(w, r, &input) {
		return
	}
	input.DisplayName = strings.TrimSpace(input.DisplayName)
	if !validLength(input.DisplayName, 2, 100) {
		writeError(w, http.StatusBadRequest, "Bad Request", "displayName must contain 2 to 100 characters.")
		return
	}
	var response struct {
		ID          string `json:"id"`
		Email       string `json:"email"`
		DisplayName string `json:"displayName"`
	}
	err := s.db.QueryRow(r.Context(), `UPDATE owners SET display_name=$1, updated_at=$2 WHERE owner_id=$3 RETURNING owner_id, email, display_name`, input.DisplayName, isoNow(), user.ID).
		Scan(&response.ID, &response.Email, &response.DisplayName)
	if err != nil {
		if err == pgx.ErrNoRows {
			writeError(w, http.StatusNotFound, "Not Found", "User was not found.")
			return
		}
		s.internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, response)
}

type sessionStore interface {
	Exec(context.Context, string, ...any) (pgconn.CommandTag, error)
	Query(context.Context, string, ...any) (pgx.Rows, error)
	QueryRow(context.Context, string, ...any) pgx.Row
}

func (s *Server) createSession(ctx context.Context, user authUser, r *http.Request) (sessionResponse, string, error) {
	return s.createSessionWith(ctx, s.db, user, r)
}

func (s *Server) createSessionWith(ctx context.Context, store sessionStore, user authUser, r *http.Request) (sessionResponse, string, error) {
	raw := make([]byte, 48)
	if _, err := rand.Read(raw); err != nil {
		return sessionResponse{}, "", err
	}
	refreshToken := base64.RawURLEncoding.EncodeToString(raw)
	expiresAt := time.Now().Add(time.Duration(s.config.RefreshTTLDays) * 24 * time.Hour)
	userAgent := strings.TrimSpace(r.UserAgent())
	if len(userAgent) > 500 {
		userAgent = userAgent[:500]
	}
	if _, err := store.Exec(ctx, `INSERT INTO "refresh_sessions" ("id", "ownerId", "tokenHash", "expiresAt", "ipAddress", "userAgent", "updatedAt") VALUES ($1,$2,$3,$4,$5,$6,NOW())`, uuid.NewString(), user.ID, hashRefreshToken(refreshToken), expiresAt, s.clientIP(r), userAgent); err != nil {
		return sessionResponse{}, "", err
	}
	accessToken, err := s.signAccessToken(user)
	if err != nil {
		return sessionResponse{}, "", err
	}
	current, err := s.loadCurrentUserWith(ctx, store, user.ID)
	if err != nil {
		return sessionResponse{}, "", err
	}
	return sessionResponse{AccessToken: accessToken, User: current}, refreshToken, nil
}

func (s *Server) loadCurrentUser(ctx context.Context, userID string) (currentUser, error) {
	return s.loadCurrentUserWith(ctx, s.db, userID)
}

func (s *Server) loadCurrentUserWith(ctx context.Context, store sessionStore, userID string) (currentUser, error) {
	var user currentUser
	var status string
	if err := store.QueryRow(ctx, `SELECT owner_id, email, display_name, status FROM owners WHERE owner_id=$1 AND status='active'`, userID).Scan(&user.ID, &user.Email, &user.DisplayName, &status); err != nil {
		return currentUser{}, err
	}
	rows, err := store.Query(ctx, `SELECT os.store_id, s.display_name, COALESCE(s.store_code, s.store_id), os.role FROM owner_stores os JOIN stores s ON s.store_id=os.store_id WHERE os.owner_id=$1 ORDER BY s.display_name`, userID)
	if err != nil {
		return currentUser{}, err
	}
	defer rows.Close()
	user.Stores = []membership{}
	for rows.Next() {
		var item membership
		if err := rows.Scan(&item.StoreID, &item.StoreName, &item.StoreCode, &item.Role); err != nil {
			return currentUser{}, err
		}
		user.Stores = append(user.Stores, item)
	}
	return user, rows.Err()
}

func (s *Server) setRefreshCookie(w http.ResponseWriter, token string) {
	maxAge := s.config.RefreshTTLDays * 24 * 60 * 60
	// #nosec G124 -- Secure is mandatory in production; localhost development intentionally uses HTTP.
	http.SetCookie(w, &http.Cookie{Name: "refresh_token", Value: token, Path: "/api/v1/auth", MaxAge: maxAge, Expires: time.Now().Add(time.Duration(maxAge) * time.Second), HttpOnly: true, Secure: s.config.Environment == "production", SameSite: s.cookieSameSite()})
}

func (s *Server) cookieSameSite() http.SameSite {
	if s.config.Environment == "production" {
		return http.SameSiteNoneMode
	}
	return http.SameSiteLaxMode
}

func hashRefreshToken(value string) string {
	hash := sha256.Sum256([]byte(value))
	return hex.EncodeToString(hash[:])
}

func (s *Server) internalError(w http.ResponseWriter, err error) {
	s.log.Error("request failed", "error", err)
	writeError(w, http.StatusInternalServerError, "Internal Server Error", "An unexpected error occurred.")
}
