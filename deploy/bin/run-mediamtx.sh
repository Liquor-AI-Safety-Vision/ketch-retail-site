#!/bin/bash
# The camera gateway. Renders the config for MODE first (test mode normalises
# the footage once; later starts reuse the cache), then runs MediaMTX.
. "$(dirname "$0")/common.sh"
cd "$ROOT"
"$PYTHON" deploy/render.py
exec "$MEDIAMTX" "$DEPLOY/runtime/mediamtx.yml"
