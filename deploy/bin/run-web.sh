#!/bin/bash
# Caddy: dashboard files, /api -> Go, /live -> MediaMTX HLS (checked by Go).
. "$(dirname "$0")/common.sh"
cd "$ROOT"
"$PYTHON" deploy/render.py --caddy
exec "$CADDY" run --config "$DEPLOY/runtime/Caddyfile" --adapter caddyfile
