package server

import (
	"encoding/json"
	"os"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// Every route server.go mounts is documented in openapi.json, and every
// documented operation is mounted. The frontend is written against this
// document; a route that changed shape here but not there is how a dashboard
// ends up calling an endpoint that no longer exists.
func TestOpenAPIMatchesMountedRoutes(t *testing.T) {
	source, err := os.ReadFile("server.go")
	if err != nil {
		t.Fatal(err)
	}
	pattern := regexp.MustCompile(`"(GET|POST|PATCH|DELETE) (/api/v1/[^"]+)"`)
	mounted := map[string]bool{}
	for _, m := range pattern.FindAllStringSubmatch(string(source), -1) {
		mounted[strings.ToLower(m[1])+" "+m[2]] = true
	}
	if len(mounted) < 20 {
		t.Fatalf("found only %d routes in server.go; the pattern no longer matches how routes are mounted", len(mounted))
	}

	raw, err := os.ReadFile("openapi.json")
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Paths map[string]map[string]json.RawMessage `json:"paths"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	documented := map[string]bool{}
	for path, ops := range doc.Paths {
		for method := range ops {
			if method != "parameters" {
				documented[method+" "+path] = true
			}
		}
	}

	var missing, stale []string
	for route := range mounted {
		if !documented[route] {
			missing = append(missing, route)
		}
	}
	for route := range documented {
		if !mounted[route] {
			stale = append(stale, route)
		}
	}
	sort.Strings(missing)
	sort.Strings(stale)
	if len(missing) > 0 || len(stale) > 0 {
		t.Fatalf("openapi.json out of sync with server.go\n  mounted but undocumented: %v\n  documented but not mounted: %v", missing, stale)
	}
}
