#!/usr/bin/env bash
# Run the 16-camera benchmark N times and keep each result as a regression record.
#
# Repeats are the point. A single run cannot separate a real change from the
# spread between identical runs, and every verdict from here on is measured
# against that spread, so the baseline has to establish it rather than assume
# it. Each run gets its own database: a reused one accumulates alerts across
# runs and makes the behaviour comparison meaningless.
#
#   ./backend/tools/bench/run_regression_bench.sh baseline 3
#   python backend/src/app/regression.py diff baseline-1 baseline-2
set -euo pipefail

# Every path below is repo-relative, so anchor to the repo rather than trusting
# the caller's cwd -- a bench that silently reads the wrong manifest is worse
# than one that fails.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO"
export PYTHONPATH="$REPO"

NAME="${1:?usage: run_regression_bench.sh <name> [repeats]}"
REPEATS="${2:-3}"
MANIFEST="${MANIFEST:-backend/config/stores/local_dev_store_16cams.json}"
OUT="dev/outputs/regression"

mkdir -p "$OUT" backend/data

for i in $(seq 1 "$REPEATS"); do
    db="backend/data/bench_${NAME}_${i}.sqlite"
    json="$OUT/${NAME}-${i}.run.json"
    rm -f "$db"
    echo "=== ${NAME} run ${i}/${REPEATS} ==="
    date +"start %H:%M:%S"
    python backend/src/app/run_local_backend.py \
        --manifest "$MANIFEST" \
        --db-path "$db" \
        --gallery-db backend/data/local_dev.sqlite \
        --gpu \
        --concurrency-mode hybrid \
        --hybrid-workers 2 \
        --native-threads 1 \
        > "$json" 2> "$OUT/${NAME}-${i}.err"
    date +"end   %H:%M:%S"
    grep -E "serving frames only|model load|^frames" "$OUT/${NAME}-${i}.err" || true
    grep -i "WARNING" "$OUT/${NAME}-${i}.err" | sort -u | head -3 || true
    python backend/src/app/regression.py record "$json" --name "${NAME}-${i}"
    echo
done
