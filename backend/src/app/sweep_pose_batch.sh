#!/usr/bin/env bash
# A/B sweep for the fixed-shape pose batching fix.
# Each config runs the same 16-camera manifest; results land in backend/data/sweep_*.json
set -u
cd /workspace/liquir_store
source /venv/main/bin/activate 2>/dev/null

LIMIT="${LIMIT:-60}"
WORKERS="${WORKERS:-4}"

run() {
  local tag="$1"; shift
  local start end
  start=$(date +%s)
  python3 -u backend/src/app/run_local_backend.py \
    --manifest backend/config/stores/local_dev_store_16cams.json \
    --db-path "backend/data/sweep_${tag}.sqlite" \
    --gallery-db "backend/data/sweep_${tag}.sqlite" \
    --media-root "backend/data/sweep_${tag}_alerts" \
    --limit-seconds "$LIMIT" --concurrency-mode hybrid --hybrid-workers "$WORKERS" \
    --gpu --native-threads 1 "$@" \
    > "backend/data/sweep_${tag}.json" 2> "backend/data/sweep_${tag}.err"
  end=$(date +%s)
  echo "TAG=${tag} ELAPSED_S=$((end-start))"
}

run "$@"
