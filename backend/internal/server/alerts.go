package server

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// Alerts are written by the AI service (ai/src/infra/postgres_alert_store.py):
// one row in alerts with status 'open', then alert_media rows for the snapshot
// and, about post_s seconds later, the clip. This API only reads them and
// records review decisions in status / reviewed_by / reviewed_at / review_note.

type alertResponse struct {
	ID               string          `json:"id"`
	StoreID          string          `json:"storeId"`
	CameraID         string          `json:"cameraId"`
	CameraName       *string         `json:"cameraName"`
	PipelineType     string          `json:"pipelineType"`
	EventType        string          `json:"eventType"`
	Severity         string          `json:"severity"`
	Status           string          `json:"status"`
	DetectedAt       string          `json:"detectedAt"`
	ClosedAt         *string         `json:"closedAt"`
	IdentityPersonID *string         `json:"identityPersonId"`
	IdentityLabel    *string         `json:"identityLabel"`
	IdentityScore    *float64        `json:"identityScore"`
	ReasonCodes      []string        `json:"reasonCodes"`
	Metadata         json.RawMessage `json:"metadata"`
	ReviewedByID     *string         `json:"reviewedById"`
	ReviewedByName   *string         `json:"reviewedByName"`
	ReviewedAt       *string         `json:"reviewedAt"`
	ReviewNote       *string         `json:"reviewNote"`
	HasVideoEvidence bool            `json:"hasVideoEvidence"`
	HasSnapshot      bool            `json:"hasSnapshot"`
}

type mediaResponse struct {
	ID              string  `json:"id"`
	Kind            string  `json:"kind"`
	MimeType        *string `json:"mimeType"`
	StartedAt       *string `json:"startedAt"`
	EndedAt         *string `json:"endedAt"`
	DurationSeconds *int    `json:"durationSeconds"`
}

type alertDetailResponse struct {
	alertResponse
	Media []mediaResponse `json:"media"`
}

const alertSelect = `SELECT a.alert_id, a.store_id, a.camera_id, c.display_name, a.pipeline_type, a.event_type,
 a.severity, a.status, a.opened_at, a.closed_at, a.identity_person_id, a.identity_label, a.identity_score,
 a.reason_codes_json, a.metadata_json, a.reviewed_by, o.display_name, a.reviewed_at, a.review_note,
 EXISTS(SELECT 1 FROM alert_media m WHERE m.alert_id=a.alert_id AND m.kind='clip'),
 EXISTS(SELECT 1 FROM alert_media m WHERE m.alert_id=a.alert_id AND m.kind='snapshot')
FROM alerts a LEFT JOIN cameras c ON c.camera_id=a.camera_id LEFT JOIN owners o ON o.owner_id=a.reviewed_by`

type rowScanner interface{ Scan(dest ...any) error }

func scanAlert(row rowScanner, item *alertResponse) error {
	var reasons, metadata string
	if err := row.Scan(&item.ID, &item.StoreID, &item.CameraID, &item.CameraName, &item.PipelineType, &item.EventType,
		&item.Severity, &item.Status, &item.DetectedAt, &item.ClosedAt, &item.IdentityPersonID, &item.IdentityLabel, &item.IdentityScore,
		&reasons, &metadata, &item.ReviewedByID, &item.ReviewedByName, &item.ReviewedAt, &item.ReviewNote,
		&item.HasVideoEvidence, &item.HasSnapshot); err != nil {
		return err
	}
	// Both columns are JSON text written by json.dumps. Unreadable values are
	// shown as empty rather than failing the whole list.
	if json.Unmarshal([]byte(reasons), &item.ReasonCodes) != nil || item.ReasonCodes == nil {
		item.ReasonCodes = []string{}
	}
	if json.Valid([]byte(metadata)) && strings.HasPrefix(strings.TrimSpace(metadata), "{") {
		item.Metadata = json.RawMessage(metadata)
	} else {
		item.Metadata = json.RawMessage("{}")
	}
	return nil
}

func (s *Server) listAlerts(w http.ResponseWriter, r *http.Request, user principal) {
	storeID := r.PathValue("storeId")
	if !s.requireRole(w, r.Context(), user.ID, storeID, roleOperator) {
		return
	}
	query, values, countQuery, countValues, limit, ok := buildAlertQuery(w, r.URL.Query(), storeID)
	if !ok {
		return
	}
	var total int
	if err := s.db.QueryRow(r.Context(), countQuery, countValues...).Scan(&total); err != nil {
		s.internalError(w, err)
		return
	}
	rows, err := s.db.Query(r.Context(), query, values...)
	if err != nil {
		s.internalError(w, err)
		return
	}
	defer rows.Close()
	items := []alertResponse{}
	for rows.Next() {
		var item alertResponse
		if err := scanAlert(rows, &item); err != nil {
			s.internalError(w, err)
			return
		}
		items = append(items, item)
	}
	if err := rows.Err(); err != nil {
		s.internalError(w, err)
		return
	}
	var nextCursor *string
	if len(items) > limit {
		items = items[:limit]
		last := items[len(items)-1]
		value := encodeAlertCursor(last.DetectedAt, last.ID)
		nextCursor = &value
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": items, "nextCursor": nextCursor, "total": total})
}

// The cursor is the last item's (opened_at, alert_id), exactly as stored.
// opened_at is UTC isoformat text, so the text order the index keeps is time
// order; alert_id breaks ties between alerts opened in the same microsecond.
func encodeAlertCursor(openedAt, alertID string) string {
	return base64.RawURLEncoding.EncodeToString([]byte(openedAt + "\n" + alertID))
}

func decodeAlertCursor(value string) (string, string, bool) {
	raw, err := base64.RawURLEncoding.DecodeString(value)
	if err != nil {
		return "", "", false
	}
	openedAt, alertID, found := strings.Cut(string(raw), "\n")
	if !found || openedAt == "" || alertID == "" {
		return "", "", false
	}
	if _, err := parseISOTime(openedAt); err != nil {
		return "", "", false
	}
	return openedAt, alertID, true
}

// alertFilters turns the shared query parameters into SQL conditions:
// status, severity and eventType take one value or a comma-separated list.
// Used by the list (with a cursor) and the summary (without).
func alertFilters(w http.ResponseWriter, q url.Values, storeID string) ([]string, []any, bool) {
	conditions := []string{`a.store_id=$1`}
	values := []any{storeID}
	add := func(column string, value any) {
		values = append(values, value)
		conditions = append(conditions, column+`=$`+itoa(len(values)))
	}
	addList := func(column, name, message string, valid func(string) bool) bool {
		raw := q.Get(name)
		if raw == "" {
			return true
		}
		items := []string{}
		for _, item := range strings.Split(raw, ",") {
			item = strings.TrimSpace(item)
			if item == "" || !valid(item) || len(items) >= 10 {
				writeError(w, http.StatusBadRequest, "Bad Request", message)
				return false
			}
			items = append(items, item)
		}
		values = append(values, items)
		conditions = append(conditions, column+`=ANY($`+itoa(len(values))+`)`)
		return true
	}
	if !addList(`a.status`, "status", "Invalid alert status filter.", func(v string) bool {
		return oneOf(v, statusOpen, statusAcknowledged, statusConfirmed, statusDismissed)
	}) || !addList(`a.severity`, "severity", "Invalid alert severity filter.", func(v string) bool {
		return oneOf(v, alertSeverities...)
	}) || !addList(`a.event_type`, "eventType", "Invalid alert type filter.", func(v string) bool {
		return validLength(v, 1, 64)
	}) {
		return nil, nil, false
	}
	if value := q.Get("pipelineType"); value != "" {
		if !validLength(value, 1, 64) {
			writeError(w, http.StatusBadRequest, "Bad Request", "Invalid pipeline filter.")
			return nil, nil, false
		}
		add(`a.pipeline_type`, value)
	}
	if value := q.Get("cameraId"); value != "" {
		add(`a.camera_id`, value)
	}
	for _, name := range []string{"from", "to"} {
		if value := q.Get(name); value != "" {
			parsed, err := time.Parse(time.RFC3339, value)
			if err != nil {
				writeError(w, http.StatusBadRequest, "Bad Request", name+" must be an ISO-8601 timestamp.")
				return nil, nil, false
			}
			values = append(values, parsed.UTC())
			operator := ">="
			if name == "to" {
				operator = "<="
			}
			conditions = append(conditions, `a.opened_at::timestamptz `+operator+` $`+itoa(len(values)))
		}
	}
	return conditions, values, true
}

// buildAlertQuery returns the page query, the count query (same filters, no
// cursor) with its arguments, and the page size.
func buildAlertQuery(w http.ResponseWriter, q url.Values, storeID string) (string, []any, string, []any, int, bool) {
	conditions, values, ok := alertFilters(w, q, storeID)
	if !ok {
		return "", nil, "", nil, 0, false
	}
	countQuery := `SELECT count(*) FROM alerts a WHERE ` + strings.Join(conditions, " AND ")
	countValues := append([]any{}, values...)
	if value := q.Get("cursor"); value != "" {
		openedAt, alertID, ok := decodeAlertCursor(value)
		if !ok {
			writeError(w, http.StatusBadRequest, "Bad Request", "cursor is invalid.")
			return "", nil, "", nil, 0, false
		}
		values = append(values, openedAt, alertID)
		conditions = append(conditions, `(a.opened_at, a.alert_id) < ($`+itoa(len(values)-1)+`, $`+itoa(len(values))+`)`)
	}
	limit := 30
	if value := q.Get("limit"); value != "" {
		parsed, err := strconv.Atoi(value)
		if err != nil || parsed < 1 || parsed > 100 {
			writeError(w, http.StatusBadRequest, "Bad Request", "limit must be between 1 and 100.")
			return "", nil, "", nil, 0, false
		}
		limit = parsed
	}
	values = append(values, limit+1)
	query := alertSelect + ` WHERE ` + strings.Join(conditions, " AND ") + ` ORDER BY a.opened_at DESC, a.alert_id DESC LIMIT $` + itoa(len(values))
	return query, values, countQuery, countValues, limit, true
}

// summarizeAlerts counts the store's alerts by status and event type, with
// the same filters as the list (no cursor): the dashboard's totals and its
// per-case counts, however many alerts there are.
func (s *Server) summarizeAlerts(w http.ResponseWriter, r *http.Request, user principal) {
	storeID := r.PathValue("storeId")
	if !s.requireRole(w, r.Context(), user.ID, storeID, roleOperator) {
		return
	}
	conditions, values, ok := alertFilters(w, r.URL.Query(), storeID)
	if !ok {
		return
	}
	rows, err := s.db.Query(r.Context(), `SELECT a.status, a.event_type, count(*) FROM alerts a WHERE `+
		strings.Join(conditions, " AND ")+` GROUP BY 1, 2 ORDER BY 1, 2`, values...)
	if err != nil {
		s.internalError(w, err)
		return
	}
	defer rows.Close()
	type bucket struct {
		Status    string `json:"status"`
		EventType string `json:"eventType"`
		Count     int    `json:"count"`
	}
	counts := []bucket{}
	for rows.Next() {
		var b bucket
		if err := rows.Scan(&b.Status, &b.EventType, &b.Count); err != nil {
			s.internalError(w, err)
			return
		}
		counts = append(counts, b)
	}
	if err := rows.Err(); err != nil {
		s.internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"counts": counts})
}

func (s *Server) findAlert(w http.ResponseWriter, r *http.Request, user principal) {
	storeID, alertID := r.PathValue("storeId"), r.PathValue("alertId")
	if !s.requireRole(w, r.Context(), user.ID, storeID, roleOperator) {
		return
	}
	detail, err := s.loadAlertDetail(r.Context(), storeID, alertID)
	if err != nil {
		if err == pgx.ErrNoRows {
			writeError(w, http.StatusNotFound, "Not Found", "Alert was not found.")
			return
		}
		s.internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, detail)
}

func (s *Server) loadAlertDetail(ctx context.Context, storeID, alertID string) (alertDetailResponse, error) {
	var item alertDetailResponse
	if err := scanAlert(s.db.QueryRow(ctx, alertSelect+` WHERE a.alert_id=$1 AND a.store_id=$2`, alertID, storeID), &item.alertResponse); err != nil {
		return alertDetailResponse{}, err
	}
	rows, err := s.db.Query(ctx, `SELECT media_id, kind, mime_type, started_at, ended_at FROM alert_media WHERE alert_id=$1 ORDER BY kind DESC, started_at NULLS FIRST, media_id`, alertID)
	if err != nil {
		return alertDetailResponse{}, err
	}
	defer rows.Close()
	item.Media = []mediaResponse{}
	for rows.Next() {
		var media mediaResponse
		if err := rows.Scan(&media.ID, &media.Kind, &media.MimeType, &media.StartedAt, &media.EndedAt); err != nil {
			return alertDetailResponse{}, err
		}
		media.DurationSeconds = mediaDuration(media.StartedAt, media.EndedAt)
		item.Media = append(item.Media, media)
	}
	return item, rows.Err()
}

// A clip's length from the window the AI service recorded for it; nil for a
// snapshot or when either end is missing.
func mediaDuration(startedAt, endedAt *string) *int {
	if startedAt == nil || endedAt == nil {
		return nil
	}
	start, err1 := parseISOTime(*startedAt)
	end, err2 := parseISOTime(*endedAt)
	if err1 != nil || err2 != nil || !end.After(start) {
		return nil
	}
	seconds := int(end.Sub(start).Round(time.Second).Seconds())
	return &seconds
}

func (s *Server) acknowledgeAlert(w http.ResponseWriter, r *http.Request, user principal) {
	s.changeAlertStatus(w, r, user, statusAcknowledged, roleOperator)
}
func (s *Server) dismissAlert(w http.ResponseWriter, r *http.Request, user principal) {
	s.changeAlertStatus(w, r, user, statusDismissed, roleOperator)
}
func (s *Server) confirmAlert(w http.ResponseWriter, r *http.Request, user principal) {
	s.changeAlertStatus(w, r, user, statusConfirmed, roleManager)
}

func (s *Server) changeAlertStatus(w http.ResponseWriter, r *http.Request, user principal, status, minimumRole string) {
	storeID, alertID := r.PathValue("storeId"), r.PathValue("alertId")
	if !s.requireRole(w, r.Context(), user.ID, storeID, minimumRole) {
		return
	}
	var input struct {
		Note *string `json:"note"`
	}
	if !decodeJSON(w, r, &input) {
		return
	}
	var note any
	if input.Note != nil {
		trimmed := strings.TrimSpace(*input.Note)
		if len(trimmed) > 1000 {
			writeError(w, http.StatusBadRequest, "Bad Request", "note must be at most 1000 characters.")
			return
		}
		if trimmed != "" {
			note = trimmed
		}
	}
	var response struct {
		ID         string  `json:"id"`
		Status     string  `json:"status"`
		ReviewedAt string  `json:"reviewedAt"`
		ReviewNote *string `json:"reviewNote"`
	}
	tx, err := s.db.Begin(r.Context())
	if err != nil {
		s.internalError(w, err)
		return
	}
	defer func() { _ = tx.Rollback(r.Context()) }()
	var role string
	err = tx.QueryRow(r.Context(), `SELECT os.role FROM owner_stores os JOIN owners o ON o.owner_id=os.owner_id WHERE os.owner_id=$1 AND os.store_id=$2 AND o.status='active' FOR SHARE OF os, o`, user.ID, storeID).Scan(&role)
	if err != nil {
		if err == pgx.ErrNoRows {
			writeError(w, http.StatusForbidden, "Forbidden", "Your store role does not allow this action.")
		} else {
			s.internalError(w, err)
		}
		return
	}
	var previousStatus string
	var previousActor, previousAt, previousNote *string
	err = tx.QueryRow(r.Context(), `SELECT status, reviewed_by, reviewed_at, review_note FROM alerts WHERE alert_id=$1 AND store_id=$2 FOR UPDATE`, alertID, storeID).Scan(&previousStatus, &previousActor, &previousAt, &previousNote)
	if err != nil {
		if err == pgx.ErrNoRows {
			writeError(w, http.StatusNotFound, "Not Found", "Alert was not found.")
		} else {
			s.internalError(w, err)
		}
		return
	}
	if code := alertTransitionStatus(previousStatus, status, role); code != 0 {
		writeError(w, code, http.StatusText(code), "This alert decision cannot be changed with your current role or from its current state.")
		return
	}
	if input.Note == nil {
		note = previousNote
	}
	err = tx.QueryRow(r.Context(), `UPDATE alerts SET status=$1, reviewed_by=$2, reviewed_at=$3, review_note=$4 WHERE alert_id=$5 AND store_id=$6 RETURNING alert_id, status, reviewed_at, review_note`,
		status, user.ID, isoNow(), note, alertID, storeID).Scan(&response.ID, &response.Status, &response.ReviewedAt, &response.ReviewNote)
	if err != nil {
		if err == pgx.ErrNoRows {
			writeError(w, http.StatusNotFound, "Not Found", "Alert was not found.")
			return
		}
		s.internalError(w, err)
		return
	}
	if _, err := tx.Exec(r.Context(), `INSERT INTO "alert_status_history" ("id","storeId","alertId","actorId","actorRole","previousStatus","newStatus","previousActorId","previousReviewedAt","previousNote","note") VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`,
		uuid.NewString(), storeID, alertID, user.ID, role, previousStatus, status, previousActor, previousAt, previousNote, note); err != nil {
		s.internalError(w, err)
		return
	}
	if err := tx.Commit(r.Context()); err != nil {
		s.internalError(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, response)
}

// Terminal decisions (confirmed, dismissed) can only be corrected by
// management. Repeating a decision never rewrites its actor/note; row locking
// serializes competing transitions.
func alertTransitionStatus(from, to, role string) int {
	if roleRank[role] < roleRank[roleOperator] || (to == statusConfirmed && roleRank[role] < roleRank[roleManager]) ||
		(oneOf(from, statusConfirmed, statusDismissed) && roleRank[role] < roleRank[roleManager]) {
		return http.StatusForbidden
	}
	if from == to || !oneOf(from, statusOpen, statusAcknowledged, statusConfirmed, statusDismissed) || !oneOf(to, statusAcknowledged, statusConfirmed, statusDismissed) ||
		(oneOf(from, statusConfirmed, statusDismissed) && to == statusAcknowledged) {
		return http.StatusConflict
	}
	return 0
}
