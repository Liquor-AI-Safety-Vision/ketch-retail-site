"""Measure how much of each camera's footage is actually still.

This decides whether motion gating is worth building before any of it gets
built. The idea only pays if the storage cameras really do spend most of
their time looking at an empty room, and that is an assumption until it is
counted. If the still fraction comes back low, the feature is dead here and
a day is saved.

Frames come from the same source the runtime uses, at the same sample rate,
so the counts describe the frames the pipeline would actually have skipped.
Each frame is shrunk to a thumbnail, compared against the previous one, and
called still when almost no pixels moved.

What this measures is an upper bound. The real gate has to be stricter than
"nothing moved": a person standing motionless in a storeroom moves no pixels
either, so the shipped rule also requires that no track is currently alive,
and forces a full frame through every couple of seconds regardless. Both
only ever skip fewer frames than reported here.

    python ai/src/app/diag_motion.py --manifest <path>
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[3]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = _Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.src.infra.local_config import camera_specs_from_manifest  # noqa: E402
from ai.src.runtime.virtual_video_stream import VirtualVideoStreamSource  # noqa: E402
from ai.src.common.paths import ai_root, outputs_dir

# The thumbnail is deliberately tiny: this comparison has to be negligible
# next to the ~26ms of pose and ReID it is trying to avoid.
THUMB_W, THUMB_H = 160, 90

# A pixel counts as moved when it shifts by more than this, on 0-255. Below
# roughly this much, sensor noise and compression artefacts dominate.
PIXEL_DELTA = 12

# Candidate gates, as the fraction of thumbnail pixels that moved.
GATES = (0.001, 0.002, 0.005, 0.01, 0.02)


def _thumb(frame_bgr: np.ndarray) -> np.ndarray:
    small = cv2.resize(frame_bgr, (THUMB_W, THUMB_H), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def scan(spec) -> dict:
    source = VirtualVideoStreamSource(spec)
    prev = None
    moved: list[float] = []
    for frame in source.iter_frames(pace_realtime=False):
        cur = _thumb(frame.image_bgr)
        if prev is not None:
            delta = cv2.absdiff(cur, prev)
            moved.append(float(np.count_nonzero(delta > PIXEL_DELTA)) / delta.size)
        prev = cur

    if not moved:
        return {"camera_id": spec.camera_id, "camera_type": spec.camera_type, "frames": 0}

    arr = np.asarray(moved, np.float32)
    gates = {}
    for gate in GATES:
        still = arr < gate
        gates[str(gate)] = {
            "still_pct": round(float(still.mean()) * 100.0, 1),
            "longest_still_run_s": round(_longest_run(still) / spec.target_fps, 1),
        }
    return {
        "camera_id": spec.camera_id,
        "camera_type": spec.camera_type,
        "frames": len(moved),
        "moved_median": round(float(np.median(arr)), 5),
        "moved_p90": round(float(np.percentile(arr, 90)), 5),
        "gates": gates,
    }


def _longest_run(mask: np.ndarray) -> int:
    best = run = 0
    for value in mask:
        run = run + 1 if value else 0
        best = max(best, run)
    return best


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--manifest", default=str(ai_root() / "config/stores/local_dev_store_16cams.json"))
    p.add_argument("--gate", type=float, default=0.002, help="Gate to headline in the summary.")
    p.add_argument("--out", default=str(outputs_dir() / "motion" / "motion_scan.json"))
    args = p.parse_args()

    specs = camera_specs_from_manifest(args.manifest)
    rows = []
    for spec in specs:
        row = scan(spec)
        rows.append(row)
        if row["frames"]:
            head = row["gates"][str(args.gate)]
            print(f"{row['camera_id']:<18s} {row['camera_type']:<14s} "
                  f"frames={row['frames']:5d}  still={head['still_pct']:5.1f}%  "
                  f"longest still run={head['longest_still_run_s']:5.1f}s  "
                  f"median moved={row['moved_median']:.4f}")
        else:
            print(f"{row['camera_id']:<18s} no frames")

    print()
    by_type: dict[str, list] = defaultdict(list)
    for row in rows:
        if row["frames"]:
            by_type[row["camera_type"]].append(row)

    total_frames = sum(r["frames"] for r in rows if r["frames"])
    print(f"{'camera_type':<16s} {'cams':>4s} {'frames':>7s}  still % at each gate")
    print(f"{'':16s} {'':>4s} {'':>7s}  " + "  ".join(f"{g:>7.3f}" for g in GATES))
    for ctype, group in sorted(by_type.items()):
        frames = sum(r["frames"] for r in group)
        cells = []
        for gate in GATES:
            weighted = sum(r["gates"][str(gate)]["still_pct"] * r["frames"] for r in group) / frames
            cells.append(f"{weighted:7.1f}")
        print(f"{ctype:<16s} {len(group):>4d} {frames:>7d}  " + "  ".join(cells))

    print()
    for gate in GATES:
        skipped = sum(r["gates"][str(gate)]["still_pct"] / 100.0 * r["frames"]
                      for r in rows if r["frames"])
        print(f"gate {gate:<6.3f}  would skip {skipped / total_frames * 100:5.1f}% of all frames "
              f"({int(skipped)} of {total_frames})")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"gates": list(GATES), "pixel_delta": PIXEL_DELTA,
                               "thumb": [THUMB_W, THUMB_H], "cameras": rows}, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
