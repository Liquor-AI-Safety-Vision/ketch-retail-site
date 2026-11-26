#!/usr/bin/env bash
# n=3, 16 cameras, 4070. Topology = the L40S optimum (8 dec, 2x8 lanes, 4 biz).
#
# Every guard here exists because of a specific failure in the 2026-08-27 round:
#   - pkill -f <script> misses multiprocessing children (they are `spawn_main`)
#   - orphans from a killed run starved the CPU and invalidated a whole round
#   - stale .err files from a previous run were misread as new results
#   - one orphaned nvidia-smi kept writing another run's VRAM log
set -u
# Repo root from this script's own location, not a hardcoded box path: the
# bench now runs from a checkout wherever it sits, and ai/ is the only
# directory that has to be present for the pipeline itself.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO"
OUT=ai/dev/outputs/bench16_4070
mkdir -p "$OUT"

cleanup() {
  pkill -9 -f spawn_main            >/dev/null 2>&1
  pkill -9 -f diag_split_tiers      >/dev/null 2>&1
  pkill -9 -f 'nvidia-smi.*-l 1'    >/dev/null 2>&1
  sleep 3
}

orphans() {
  # exclude this script and the ssh/bash wrapper from the count
  pgrep -f 'spawn_main|diag_split_tiers' 2>/dev/null | grep -v "^$$\$" | wc -l
}

cleanup
n=$(orphans)
if [ "$n" -gt 0 ]; then
  echo "ABORT: $n orphan(s) still alive before starting"
  pgrep -af 'spawn_main|diag_split_tiers'
  exit 1
fi
echo "clean start, 0 orphans" > "$OUT/n3_progress"

for i in 1 2 3; do
  cleanup
  n=$(orphans)
  if [ "$n" -gt 0 ]; then
    echo "ABORT before run $i: $n orphan(s)" >> "$OUT/n3_progress"
    exit 1
  fi

  # stale files from a killed run read exactly like fresh results
  rm -f "$OUT/n3_$i.out" "$OUT/n3_$i.err" "$OUT/n3_vram_$i.csv"

  nvidia-smi --query-gpu=memory.used,utilization.gpu \
             --format=csv,noheader,nounits -l 1 > "$OUT/n3_vram_$i.csv" 2>/dev/null &
  VPID=$!

  echo "run $i started $(date -u +%H:%M:%S)" >> "$OUT/n3_progress"
  PYTHONPATH="$REPO" timeout 1800 /venv/main/bin/python \
    ai/tools/diag_split_tiers.py --layer all \
    --cameras 16 --duration 180 \
    --decoders 8 --inferers 2 --infer-threads 8 --business 4 \
    > "$OUT/n3_$i.out" 2> "$OUT/n3_$i.err"
  rc=$?

  kill "$VPID" >/dev/null 2>&1
  wait "$VPID" 2>/dev/null

  fps=$(grep -oE 'wall .*-> +[0-9.]+ FPS' "$OUT/n3_$i.err" | grep -oE '[0-9.]+ FPS' | head -1)
  vram=$(cut -d, -f1 "$OUT/n3_vram_$i.csv" 2>/dev/null | sort -n | tail -1)
  alerts=$(grep -oE 'alerts=[0-9]+' "$OUT/n3_$i.err" | cut -d= -f2 | paste -sd+ | bc 2>/dev/null)
  echo "run $i done rc=$rc fps=$fps vram_peak=${vram}MiB alerts=${alerts} $(date -u +%H:%M:%S)" \
    >> "$OUT/n3_progress"
done

cleanup
echo "ALL DONE $(date -u +%H:%M:%S)" >> "$OUT/n3_progress"
