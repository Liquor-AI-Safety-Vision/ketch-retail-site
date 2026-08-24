"""Compare runtime sweep results side by side.

The runner prints progress lines before the JSON payload, so each file is
scanned for the line where the JSON array starts.

    python backend/src/app/compare_sweeps.py backend/data/sweep_*.json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

PHASES = (
    "detect_pose",
    "detect_pose::infer",
    "detect_pose::queue_wait",
    "detect_pose::batch_wait",
    "embed_reid",
    "till_infer",
)


def load(path: Path) -> list[dict]:
    """Return the first JSON array in the file, ignoring surrounding log output.

    Runs print progress lines before the payload, and an aborted run can leave
    trailing text after it, so decode one value and drop whatever follows.
    """
    lines = path.read_text(errors="replace").splitlines()
    decoder = json.JSONDecoder()
    for idx, line in enumerate(lines):
        if line.strip() != "[":
            continue
        blob = "\n".join(lines[idx:])
        try:
            value, _end = decoder.raw_decode(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and value:
            return value
    raise ValueError(f"{path}: no JSON array found")


def summarize(path: Path, elapsed_s: float | None) -> dict:
    data = load(path)
    agg: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    boots: list[float] = []
    batches: list[float] = []
    for cam in data:
        for pm in cam["pipeline_metrics"].values():
            boots.append(pm.get("bootstrap_ms", 0.0) / 1000.0)
            if pm.get("pose_batch_size_avg") is not None:
                batches.append(pm["pose_batch_size_avg"])
            for key, val in pm.get("timing", {}).items():
                if "tracks_considered" in key:
                    continue  # a counter, not a duration
                agg[key][0] += val["total_ms"]
                agg[key][1] += val["count"]
    frames = sum(cam["frames_processed"] for cam in data)
    return {
        "name": path.stem,
        "frames": frames,
        "elapsed_s": elapsed_s,
        "fps": (frames / elapsed_s) if elapsed_s else None,
        "boot_mean": sum(boots) / len(boots) if boots else 0.0,
        "boot_max": max(boots) if boots else 0.0,
        "batch_avg": sum(batches) / len(batches) if batches else 0.0,
        "phases": {k: (v[0] / v[1] if v[1] else 0.0) for k, v in agg.items()},
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("files", nargs="+")
    p.add_argument(
        "--elapsed",
        action="append",
        default=[],
        help="name=seconds, e.g. sweep_pad_b1=170",
    )
    a = p.parse_args()
    elapsed = {}
    for item in a.elapsed:
        k, v = item.split("=", 1)
        elapsed[k] = float(v)

    rows = [summarize(Path(f), elapsed.get(Path(f).stem)) for f in a.files]

    print(f"{'run':<22}{'frames':>8}{'elapsed':>9}{'FPS':>8}{'boot_max':>10}{'batch':>7}")
    for r in rows:
        fps = f"{r['fps']:.2f}" if r["fps"] else "-"
        el = f"{r['elapsed_s']:.0f}" if r["elapsed_s"] else "-"
        print(
            f"{r['name']:<22}{r['frames']:>8}{el:>9}{fps:>8}"
            f"{r['boot_max']:>10.1f}{r['batch_avg']:>7.2f}"
        )

    print(f"\n{'phase avg_ms':<28}" + "".join(f"{r['name'][-12:]:>14}" for r in rows))
    for phase in PHASES:
        cells = "".join(f"{r['phases'].get(phase, 0.0):>14.1f}" for r in rows)
        print(f"{phase:<28}{cells}")


if __name__ == "__main__":
    main()
