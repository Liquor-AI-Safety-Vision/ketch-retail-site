package server

import (
	"context"
	"encoding/json"
	"net/http"

	"github.com/jackc/pgx/v5"
)

// Cameras are read-only here, like stores: the AI service upserts every row
// from its manifest at startup, so an edit made through this API would be
// overwritten on the next pipeline start. To add or change a camera, change
// the manifest (ai/config/stores/*.json).
//
// source_uri is never selected. The AI service redacts the password before
// writing it, but the camera's address is still not the dashboard's business.

// cameraMetadata is what the AI service writes into cameras.metadata_json
// (ai/src/infra/camera_metadata.py).
type cameraMetadata struct {
	Pipelines  []string       `json:"pipelines"`
	Zones      []metadataZone `json:"zones"`
	ZoneErrors []string       `json:"zone_errors"`
}

type metadataZone struct {
	PipelineID string      `json:"pipeline_id"`
	Name       string      `json:"name"`
	Type       string      `json:"type"`
	PointsNorm [][]float64 `json:"points_norm"`
}

const cameraColumns = `camera_id, store_id, display_name, camera_type, source_type, is_active <> 0, metadata_json`

func scanCamera(row rowScanner, item *cameraResponse) error {
	var rawMetadata string
	if err := row.Scan(&item.ID, &item.StoreID, &item.Name, &item.CameraType, &item.SourceType, &item.IsActive, &rawMetadata); err != nil {
		return err
	}
	meta := parseCameraMetadata(rawMetadata)
	item.Pipelines = meta.Pipelines
	return nil
}

func parseCameraMetadata(raw string) cameraMetadata {
	var meta cameraMetadata
	// A row seeded before metadata_json carried anything is '{}': no
	// pipelines, no zones. Malformed JSON reads the same way rather than
	// failing the whole camera list.
	_ = json.Unmarshal([]byte(raw), &meta)
	if meta.Pipelines == nil {
		meta.Pipelines = []string{}
	}
	return meta
}

func (m cameraMetadata) zoneResponses() []zoneResponse {
	out := make([]zoneResponse, 0, len(m.Zones))
	for _, z := range m.Zones {
		out = append(out, zoneResponse{PipelineID: z.PipelineID, Name: z.Name, Type: z.Type, PointsNorm: z.PointsNorm})
	}
	return out
}

func (s *Server) listCameras(w http.ResponseWriter, r *http.Request, user principal) {
	storeID := r.PathValue("storeId")
	if !s.requireRole(w, r.Context(), user.ID, storeID, roleOperator) {
		return
	}
	rows, err := s.db.Query(r.Context(), `SELECT `+cameraColumns+` FROM cameras WHERE store_id=$1 ORDER BY display_name, camera_id`, storeID)
	if err != nil {
		s.internalError(w, err)
		return
	}
	defer rows.Close()
	items := []cameraResponse{}
	for rows.Next() {
		var item cameraResponse
		if err := scanCamera(rows, &item); err != nil {
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

func (s *Server) findCamera(w http.ResponseWriter, r *http.Request, user principal) {
	storeID, cameraID := r.PathValue("storeId"), r.PathValue("cameraId")
	if !s.requireRole(w, r.Context(), user.ID, storeID, roleOperator) {
		return
	}
	var item cameraResponse
	if err := scanCamera(s.db.QueryRow(r.Context(), `SELECT `+cameraColumns+` FROM cameras WHERE store_id=$1 AND camera_id=$2`, storeID, cameraID), &item); err != nil {
		if err == pgx.ErrNoRows {
			writeError(w, http.StatusNotFound, "Not Found", "Camera was not found.")
			return
		}
		s.internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, item)
}

// Zones as the AI pipelines use them, for drawing over the camera view.
// points are fractions of frame width/height.
func (s *Server) listZones(w http.ResponseWriter, r *http.Request, user principal) {
	storeID, cameraID := r.PathValue("storeId"), r.PathValue("cameraId")
	if !s.requireRole(w, r.Context(), user.ID, storeID, roleOperator) {
		return
	}
	meta, err := s.loadCameraMetadata(r.Context(), storeID, cameraID)
	if err != nil {
		if err == pgx.ErrNoRows {
			writeError(w, http.StatusNotFound, "Not Found", "Camera was not found.")
			return
		}
		s.internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, meta.zoneResponses())
}

func (s *Server) loadCameraMetadata(ctx context.Context, storeID, cameraID string) (cameraMetadata, error) {
	var raw string
	if err := s.db.QueryRow(ctx, `SELECT metadata_json FROM cameras WHERE store_id=$1 AND camera_id=$2`, storeID, cameraID).Scan(&raw); err != nil {
		return cameraMetadata{}, err
	}
	return parseCameraMetadata(raw), nil
}
