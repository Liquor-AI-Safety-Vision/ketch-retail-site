package server

import (
	"context"
	"net/http"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
)

// Stores are read-only here. They are created and renamed by the AI service
// from its manifest (ai/config/stores/*.json) every time it starts, so an edit
// made through this API would be overwritten on the next pipeline start.

func (s *Server) requireRole(w http.ResponseWriter, ctx context.Context, userID, storeID, minimum string) bool {
	var role string
	err := s.db.QueryRow(ctx, `SELECT os.role FROM owner_stores os JOIN owners o ON o.owner_id=os.owner_id WHERE os.owner_id=$1 AND os.store_id=$2 AND o.status='active'`, userID, storeID).Scan(&role)
	if err != nil {
		if err == pgx.ErrNoRows {
			writeError(w, http.StatusNotFound, "Not Found", "Store was not found.")
			return false
		}
		s.internalError(w, err)
		return false
	}
	if roleRank[role] < roleRank[minimum] {
		writeError(w, http.StatusForbidden, "Forbidden", "Your store role does not allow this action.")
		return false
	}
	return true
}

func (s *Server) listStores(w http.ResponseWriter, r *http.Request, user principal) {
	rows, err := s.db.Query(r.Context(), `SELECT s.store_id, s.display_name, COALESCE(s.store_code, s.store_id), s.timezone, os.role FROM stores s JOIN owner_stores os ON os.store_id=s.store_id WHERE os.owner_id=$1 ORDER BY s.display_name`, user.ID)
	if err != nil {
		s.internalError(w, err)
		return
	}
	defer rows.Close()
	items := []storeResponse{}
	for rows.Next() {
		var item storeResponse
		if err := rows.Scan(&item.ID, &item.Name, &item.Code, &item.Timezone, &item.Role); err != nil {
			s.internalError(w, err)
			return
		}
		items = append(items, item)
	}
	if err := rows.Err(); err != nil {
		s.internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, items)
}

func (s *Server) findStore(w http.ResponseWriter, r *http.Request, user principal) {
	storeID := r.PathValue("storeId")
	if !s.requireRole(w, r.Context(), user.ID, storeID, roleOperator) {
		return
	}
	var item storeResponse
	err := s.db.QueryRow(r.Context(), `SELECT s.store_id, s.display_name, COALESCE(s.store_code, s.store_id), s.timezone, os.role FROM stores s JOIN owner_stores os ON os.store_id=s.store_id WHERE s.store_id=$1 AND os.owner_id=$2`, storeID, user.ID).
		Scan(&item.ID, &item.Name, &item.Code, &item.Timezone, &item.Role)
	if err != nil {
		if err == pgx.ErrNoRows {
			writeError(w, http.StatusNotFound, "Not Found", "Store was not found.")
			return
		}
		s.internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, item)
}

func (s *Server) listMembers(w http.ResponseWriter, r *http.Request, user principal) {
	storeID := r.PathValue("storeId")
	if !s.requireRole(w, r.Context(), user.ID, storeID, roleManager) {
		return
	}
	type memberResponse struct {
		ID          string `json:"id"`
		Email       string `json:"email"`
		DisplayName string `json:"displayName"`
		Status      string `json:"status"`
		Role        string `json:"role"`
		CreatedAt   string `json:"createdAt"`
	}
	rows, err := s.db.Query(r.Context(), `SELECT o.owner_id, o.email, o.display_name, o.status, os.role, os.created_at FROM owner_stores os JOIN owners o ON o.owner_id=os.owner_id WHERE os.store_id=$1 ORDER BY os.role, o.display_name`, storeID)
	if err != nil {
		s.internalError(w, err)
		return
	}
	defer rows.Close()
	items := []memberResponse{}
	for rows.Next() {
		var item memberResponse
		if err := rows.Scan(&item.ID, &item.Email, &item.DisplayName, &item.Status, &item.Role, &item.CreatedAt); err != nil {
			s.internalError(w, err)
			return
		}
		items = append(items, item)
	}
	if err := rows.Err(); err != nil {
		s.internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, items)
}

func (s *Server) addMember(w http.ResponseWriter, r *http.Request, user principal) {
	storeID := r.PathValue("storeId")
	if !s.requireRole(w, r.Context(), user.ID, storeID, roleOwner) {
		return
	}
	var input struct {
		Email string `json:"email"`
		Role  string `json:"role"`
	}
	if !decodeJSON(w, r, &input) {
		return
	}
	input.Email = strings.ToLower(strings.TrimSpace(input.Email))
	if !validEmail(input.Email) || !oneOf(input.Role, roleManager, roleOperator) {
		writeError(w, http.StatusBadRequest, "Bad Request", "Member details are invalid.")
		return
	}
	var memberID string
	if err := s.db.QueryRow(r.Context(), `SELECT owner_id FROM owners WHERE lower(email)=$1`, input.Email).Scan(&memberID); err != nil {
		if err == pgx.ErrNoRows {
			writeError(w, http.StatusNotFound, "Not Found", "The invited user must register first.")
			return
		}
		s.internalError(w, err)
		return
	}
	_, err := s.db.Exec(r.Context(), `INSERT INTO owner_stores (owner_id, store_id, role, created_at) VALUES ($1,$2,$3,$4)`, memberID, storeID, input.Role, isoNow())
	if err != nil {
		if uniqueViolation(err) {
			writeError(w, http.StatusConflict, "Conflict", "This user already belongs to the store.")
			return
		}
		s.internalError(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]string{"userId": memberID, "role": input.Role})
}

func (s *Server) updateMember(w http.ResponseWriter, r *http.Request, user principal) {
	storeID, memberID := r.PathValue("storeId"), r.PathValue("memberId")
	if !s.requireRole(w, r.Context(), user.ID, storeID, roleOwner) {
		return
	}
	var input struct {
		Role string `json:"role"`
	}
	if !decodeJSON(w, r, &input) {
		return
	}
	if !oneOf(input.Role, roleManager, roleOperator) {
		writeError(w, http.StatusBadRequest, "Bad Request", "role must be manager or operator.")
		return
	}
	if !s.memberCanChange(w, r.Context(), storeID, memberID) {
		return
	}
	var response struct {
		UserID string `json:"userId"`
		Role   string `json:"role"`
	}
	err := s.db.QueryRow(r.Context(), `UPDATE owner_stores SET role=$1 WHERE store_id=$2 AND owner_id=$3 RETURNING owner_id, role`, input.Role, storeID, memberID).Scan(&response.UserID, &response.Role)
	if err != nil {
		if err == pgx.ErrNoRows {
			writeError(w, http.StatusNotFound, "Not Found", "Store member was not found.")
			return
		}
		s.internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) removeMember(w http.ResponseWriter, r *http.Request, user principal) {
	storeID, memberID := r.PathValue("storeId"), r.PathValue("memberId")
	if !s.requireRole(w, r.Context(), user.ID, storeID, roleOwner) {
		return
	}
	if memberID == user.ID {
		writeError(w, http.StatusConflict, "Conflict", "An owner cannot remove themselves.")
		return
	}
	if !s.memberCanChange(w, r.Context(), storeID, memberID) {
		return
	}
	result, err := s.db.Exec(r.Context(), `DELETE FROM owner_stores WHERE store_id=$1 AND owner_id=$2`, storeID, memberID)
	if err != nil {
		s.internalError(w, err)
		return
	}
	if result.RowsAffected() == 0 {
		writeError(w, http.StatusNotFound, "Not Found", "Store member was not found.")
		return
	}
	writeJSON(w, http.StatusOK, map[string]bool{"success": true})
}

func (s *Server) memberCanChange(w http.ResponseWriter, ctx context.Context, storeID, memberID string) bool {
	var role string
	err := s.db.QueryRow(ctx, `SELECT role FROM owner_stores WHERE store_id=$1 AND owner_id=$2`, storeID, memberID).Scan(&role)
	if err != nil {
		if err == pgx.ErrNoRows {
			writeError(w, http.StatusNotFound, "Not Found", "Store member was not found.")
			return false
		}
		s.internalError(w, err)
		return false
	}
	if role == roleOwner {
		writeError(w, http.StatusConflict, "Conflict", "Owner membership cannot be changed or removed.")
		return false
	}
	return true
}

func itoa(value int) string {
	return strconv.Itoa(value)
}
