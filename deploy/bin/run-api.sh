#!/bin/bash
# The Go API (dashboard backend). Secrets come from backend/.env; what depends
# on how this server is deployed is set here.
. "$(dirname "$0")/common.sh"
cd "$ROOT/backend"
export PORT=3000 NODE_ENV=production LIVE_VIEW_MODE="$MODE" \
  PUBLIC_API_BASE_URL="$PUBLIC_URL" WEB_ORIGIN="$PUBLIC_URL" \
  TRUST_PROXY=true TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128 \
  REGISTER_ENABLED=false SWAGGER_ENABLED=false
"$DEPLOY/dist/liquor-migrate"
exec "$DEPLOY/dist/liquor-api"
