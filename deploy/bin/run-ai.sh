#!/bin/bash
# The AI service. Waits for every camera stream, then runs the split pipeline
# against Neon + R2 (ai/.env). On SIGTERM it flushes pending alerts and clips
# before exiting, so supervisor gives it time (stopwaitsecs).
. "$(dirname "$0")/common.sh"
cd "$ROOT"
"$PYTHON" deploy/render.py --wait
# TCP only: MediaMTX refuses UDP (1080p frames over UDP lose packets even on
# loopback, which corrupts decoding), and OpenCV would otherwise try UDP first.
# Process-wide is right here: the AI only ever reads from the local MediaMTX.
export OPENCV_FFMPEG_CAPTURE_OPTIONS="rtsp_transport;tcp"
CAMERAS=$("$PYTHON" -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["cameras"]))' "$DEPLOY/runtime/ai_manifest.json")
# shellcheck disable=SC2086
exec "$PYTHON" ai/src/app/run_split_pipeline.py \
  --manifest "$DEPLOY/runtime/ai_manifest.json" --storage-mode cloud-db \
  --cameras "$CAMERAS" $AI_ARGS
