#!/bin/bash
# Build what the server runs but does not compile itself, into deploy/dist/:
# the Go API and its tools (linux/amd64 by default) and the dashboard bundle
# (frontend/dist). Run on a machine with Go and Node; copy the repo after.
#   GOOS=linux GOARCH=amd64 deploy/build.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/deploy/dist"
mkdir -p "$OUT"
( cd "$ROOT/backend"
  export CGO_ENABLED=0 GOOS="${GOOS:-linux}" GOARCH="${GOARCH:-amd64}"
  go build -trimpath -o "$OUT/liquor-api" ./cmd/api
  go build -trimpath -o "$OUT/liquor-migrate" ./cmd/migrate
  go build -trimpath -o "$OUT/liquor-create-owner" ./cmd/create-owner
  go build -trimpath -o "$OUT/liquor-configure-notifications" ./cmd/configure-notifications )
( cd "$ROOT/frontend" && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null )
echo "built: $(ls "$OUT" | tr '\n' ' ')+ frontend/dist"
