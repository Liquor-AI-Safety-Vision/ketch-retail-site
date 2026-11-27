#!/bin/bash
# One-time (idempotent) server setup, run on the server from the repo root:
#   deploy/install.sh
# Needs: deploy/liquor.env, backend/.env, ai/.env, deploy/dist/ (deploy/build.sh),
# frontend/dist, and for MODE=test the footage under data/videos.
# Installs the Python env and MediaMTX if missing, and registers the four
# services with supervisor (group "liquor").
. "$(dirname "$0")/bin/common.sh"
MEDIAMTX_VERSION="${MEDIAMTX_VERSION:-v1.21.0}"

for f in backend/.env ai/.env deploy/dist/liquor-api deploy/dist/liquor-migrate frontend/dist/index.html; do
  [ -e "$ROOT/$f" ] || { echo "missing $f" >&2; exit 1; }
done
chmod 600 "$ROOT/backend/.env" "$ROOT/ai/.env" "$DEPLOY/liquor.env"

# Python for the AI service (and render.py).
if [ ! -x "$PYTHON" ]; then
  python3 -m venv "$(dirname "$(dirname "$PYTHON")")"
fi
"$PYTHON" -m pip install -q --upgrade pip
"$PYTHON" -m pip install -q -r "$ROOT/ai/requirements.txt"
if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
  "$PYTHON" -m pip install -q "onnxruntime-gpu>=1.20,<1.23"
  # cuDNN/cuBLAS for onnxruntime's CUDA provider, as wheels next to it.
  "$PYTHON" -m pip install -q "nvidia-cudnn-cu12>=9,<10" "nvidia-cublas-cu12>=12,<13"
else
  "$PYTHON" -m pip install -q "onnxruntime>=1.20,<1.23"
fi
"$PYTHON" -c 'import onnxruntime as o; print("onnxruntime", o.__version__, o.get_available_providers())'

# MediaMTX (single static binary).
if ! [ -x "$MEDIAMTX" ]; then
  arch=$(uname -m); case "$arch" in x86_64) arch=amd64;; aarch64) arch=arm64v8;; esac
  mkdir -p /opt/mediamtx
  curl -fsSL "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_${arch}.tar.gz" \
    | tar -xz -C /opt/mediamtx mediamtx
  MEDIAMTX=/opt/mediamtx/mediamtx
fi
"$MEDIAMTX" --version
[ -x "$CADDY" ] || { echo "caddy not found: install it or set CADDY= in deploy/liquor.env" >&2; exit 1; }
command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || { echo "ffmpeg/ffprobe are required" >&2; exit 1; }

# Supervisor services.
mkdir -p "$DEPLOY/runtime/logs"
sed -e "s#@ROOT@#$ROOT#g" "$DEPLOY/supervisor/liquor.conf" > /etc/supervisor/conf.d/liquor.conf
supervisorctl reread
supervisorctl update
echo "installed. start with: deploy/liquorctl start"
