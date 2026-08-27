#!/usr/bin/env bash
# Tier ladder at 16 cameras: decode only -> decode+infer -> full stack.
# Isolates which tier binds, the same way the L40S ladder did.
#
# Must live in a file, not an `ssh root@box "...pkill -f diag_split_tiers..."`
# one-liner: that command line contains the pattern, so pkill matches the
# shell running it and kills itself before nohup ever detaches.
set -u
cd /workspace/liquir_store
OUT=outputs/bench16_4070
mkdir -p "$OUT"

pkill -9 -f spawn_main         >/dev/null 2>&1
pkill -9 -f diag_split_tiers   >/dev/null 2>&1
pkill -9 -f 'nvidia-smi.*-l 1' >/dev/null 2>&1
sleep 3

n=$(pgrep -f 'spawn_main|diag_split_tiers' 2>/dev/null | grep -vc "^$$\$")
if [ "${n:-0}" -gt 0 ]; then
  echo "ABORT: $n orphan(s) alive" > "$OUT/ladder_progress"
  exit 1
fi

rm -f "$OUT/ladder.out" "$OUT/ladder.err"
echo "ladder started $(date -u +%H:%M:%S)" > "$OUT/ladder_progress"

PYTHONPATH=/workspace/liquir_store timeout 2400 /venv/main/bin/python \
  backend/tools/diag_split_tiers.py --layer ladder \
  --cameras 16 --duration 180 \
  --decoders 8 --inferers 2 --infer-threads 8 --business 4 \
  > "$OUT/ladder.out" 2> "$OUT/ladder.err"
rc=$?

echo "ladder done rc=$rc $(date -u +%H:%M:%S)" >> "$OUT/ladder_progress"
grep -E '=== layer|wall +:' "$OUT/ladder.err" >> "$OUT/ladder_progress" 2>/dev/null

pkill -9 -f spawn_main >/dev/null 2>&1
echo "ALL DONE" >> "$OUT/ladder_progress"
