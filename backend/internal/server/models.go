package server

type authUser struct {
	ID           string
	Email        string
	DisplayName  string
	PasswordHash string
	Status       string
}

type currentUser struct {
	ID          string       `json:"id"`
	Email       string       `json:"email"`
	DisplayName string       `json:"displayName"`
	Stores      []membership `json:"stores"`
}

type membership struct {
	StoreID   string `json:"storeId"`
	StoreName string `json:"storeName"`
	StoreCode string `json:"storeCode"`
	Role      string `json:"role"`
}

type storeResponse struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Code     string `json:"code"`
	Timezone string `json:"timezone"`
	Role     string `json:"role,omitempty"`
}

// A row of the AI service's cameras table. source_uri is deliberately absent.
type cameraResponse struct {
	ID         string   `json:"id"`
	StoreID    string   `json:"storeId"`
	Name       string   `json:"name"`
	CameraType string   `json:"cameraType"`
	SourceType string   `json:"sourceType"`
	IsActive   bool     `json:"isActive"`
	Pipelines  []string `json:"pipelines"`
}

type zoneResponse struct {
	PipelineID string      `json:"pipelineId"`
	Name       string      `json:"name"`
	Type       string      `json:"type"`
	PointsNorm [][]float64 `json:"pointsNorm"`
}
