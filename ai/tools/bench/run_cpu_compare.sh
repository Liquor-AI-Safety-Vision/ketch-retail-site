#!/usr/bin/env bash
# Why is the box already saturated at 4 cameras?
#
# Measures CPU actually consumed, from cgroup cpu.stat usage_usec, not from
# top/nproc -- the quota here is 15.36 cores while nproc reports 16, so any
# percentage computed against nproc would be wrong.
#
#   cpu_cores_used = delta_usage_usec / 1e6 / wall_s
#   saturation     = cpu_cores_used / quota_cores
#
# Ladder at each camera count so decode can be separated from inference:
# if decode-only already takes the full wall, decode is the wall; if decode
# alone is fast and only slows down under the full stack, the contention is
# with inference, which is a different fix.
set -u
# Repo root from this script's own location, not a hardcoded box path: the
# bench now runs from a checkout wherever it sits, and ai/ is the only
# directory that has to be present for the pipeline itself.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO"
OUT=ai/dev/outputs/bench16_4070
mkdir -p "$OUT"
P="$OUT/cpu_compare_progress"

QUOTA=$(awk '{printf "%.2f", $1/$2}' /sys/fs/cgroup/cpu.max)
echo "cgroup quota = $QUOTA cores (nproc=$(nproc))" > "$P"

usage_usec() { awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat; }

cleanup() {
  pkill -9 -f spawn_main       >/dev/null 2>&1
  pkill -9 -f diag_split_tiers >/dev/null 2>&1
  sleep 3
}

run_case() {
  local tag="$1" manifest="$2" ncam="$3" dec="$4" inf="$5" thr="$6" biz="$7"

  cleanup
  local n
  n=$(pgrep -f 'spawn_main|diag_split_tiers' 2>/dev/null | grep -vc "^$$\$")
  if [ "${n:-0}" -gt 0 ]; then echo "ABORT $tag: $n orphan(s)" >> "$P"; exit 1; fi

  rm -f "$OUT/cpu_$tag.err" "$OUT/cpu_$tag.out"

  local u0 t0 u1 t1
  u0=$(usage_usec); t0=$(date +%s.%N)

  PYTHONPATH="$REPO" timeout 3000 /venv/main/bin/python \
    ai/tools/diag_split_tiers.py --layer ladder \
    --manifest "$manifest" --cameras "$ncam" --duration 180 \
    --decoders "$dec" --inferers "$inf" --infer-threads "$thr" --business "$biz" \
    > "$OUT/cpu_$tag.out" 2> "$OUT/cpu_$tag.err"
  local rc=$?

  u1=$(usage_usec); t1=$(date +%s.%N)

  # Whole-ladder totals: three layers ran back to back, so this is the CPU
  # cost of all three. Per-layer walls come from the .err below.
  awk -v u0="$u0" -v u1="$u1" -v t0="$t0" -v t1="$t1" -v q="$QUOTA" -v tag="$tag" -v rc="$rc" \
    'BEGIN{ w=t1-t0; c=(u1-u0)/1e6; printf "%s rc=%d ladder_wall=%.1fs cpu_used=%.1f core-s avg_cores=%.2f sat=%.0f%%\n", tag, rc, w, c, c/w, 100*(c/w)/q }' \
    >> "$P"

  grep -E '=== layer|wall +:|frames +:' "$OUT/cpu_$tag.err" | sed "s/^/    [$tag] /" >> "$P" 2>/dev/null
}

# 4 cameras: the exact config A topology the 5.96x number came from
run_case cam4  ai/config/stores/store_1.json               4 2 1 2 2
# 16 cameras: the L40S optimum
run_case cam16 ai/config/stores/local_dev_store_16cams.json 16 8 2 8 4

cleanup
echo "ALL DONE" >> "$P"
