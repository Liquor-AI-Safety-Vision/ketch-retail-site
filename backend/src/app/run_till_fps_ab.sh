#!/usr/bin/env bash
# Compare drawer detection across till sampling rates on the counter-generated clips.
# Each rate runs the same 4 videos; results land in backend/data/tillfps_<rate>.json
set -u
cd /workspace/liquir_store
source /venv/main/bin/activate 2>/dev/null

for RATE in "$@"; do
  tag="${RATE/./p}"
  python3 -u backend/src/app/run_local_backend.py \
    --manifest backend/config/stores/counter_generated_test.json \
    --db-path "backend/data/tillfps_${tag}.sqlite" \
    --gallery-db "backend/data/tillfps_${tag}.sqlite" \
    --media-root "backend/data/tillfps_${tag}_alerts" \
    --concurrency-mode hybrid --hybrid-workers 1 --pose-max-batch 1 \
    --gpu --native-threads 1 \
    --till-fps "$RATE" \
    > "backend/data/tillfps_${tag}.json" 2> "backend/data/tillfps_${tag}.err"
  echo "RATE=${RATE} done exit=$?"
done
