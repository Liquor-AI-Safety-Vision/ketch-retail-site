"""Turn a benchmark run into a record, and judge one record against another.

Every change from here on has to clear the same bar before it is kept, and
the bar is not throughput alone. Motion gating and conditional ReID can both
buy frames by quietly dropping people, and neither shows up in the frame
rate: the run still finishes, still emits alerts, still looks healthy. So a
record carries three things, and a change has to hold all three.

    speed        aggregate FPS while serving frames, model load excluded
    behaviour    alerts per camera, and the typed breakdown
    identity     tracks created and their average lifetime

Identity is the one that needs explaining. Without ground-truth labels there
is no ID-switch count, but there is a usable proxy: when re-identification
fails, the tracker abandons tracks and mints new ids for the same people, so
tracks_created rises while lifetime falls. A change that trades identity for
speed moves those two even when FPS and alert counts both look fine.

    # after a run, keep it
    python backend/src/app/regression.py record run.json --name baseline
    # after a change, judge it
    python backend/src/app/regression.py diff baseline motion-gate

Runs vary by 3-4% between identical repeats, so a verdict needs more than a
nudge: MARGIN_PCT is the width of that noise band, doubled.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.src.app.compare_sweeps import load  # noqa: E402

# Repeats of an unchanged build land within 3-4% of each other, so anything
# inside this band is noise wearing a result's clothes.
MARGIN_PCT = 5.0

STORE = ROOT / "outputs" / "regression"

# Counters that ride along in the timing dict but are not durations.
NON_TIMING = ("tracks_considered", "::calls")


def build_record(results: list[dict]) -> dict:
    """Reduce a run's per-camera JSON to the numbers a verdict rests on."""
    frames = sum(int(c.get("frames_processed", 0)) for c in results)
    starts = [c["processing_started_at"] for c in results if c.get("processing_started_at")]
    ends = [c["processing_ended_at"] for c in results if c.get("processing_ended_at")]
    serving_s = (max(ends) - min(starts)) if starts and ends else None

    phases: dict[str, list] = defaultdict(lambda: [0.0, 0])
    behaviour: dict[str, dict] = {}
    tracks_created = 0
    track_frames = 0
    for cam in results:
        cam_id = cam["camera_id"]
        typed: dict[str, int] = {}
        for pm in cam.get("pipeline_metrics", {}).values():
            tracks_created += int(pm.get("tracks_created", 0))
            track_frames += int(pm.get("track_frames_total", 0))
            for key in ("unknown_alerts", "staff_outside_schedule_alerts", "till_state_changes"):
                if key in pm:
                    typed[key] = int(pm[key])
            for key, val in pm.get("timing", {}).items():
                if any(marker in key for marker in NON_TIMING):
                    continue
                phases[key][0] += val["total_ms"]
                phases[key][1] += val["count"]
        behaviour[cam_id] = {
            "alerts": int(cam.get("alerts_emitted", 0)),
            "frames": int(cam.get("frames_processed", 0)),
            **typed,
        }

    return {
        "frames": frames,
        "serving_s": serving_s,
        "fps": (frames / serving_s) if serving_s else None,
        "cameras": len(results),
        "behaviour": behaviour,
        "identity": {
            "tracks_created": tracks_created,
            "track_frames_total": track_frames,
            "lifetime_avg": (track_frames / tracks_created) if tracks_created else 0.0,
        },
        "phases": {k: round(v[0] / v[1], 4) if v[1] else 0.0 for k, v in sorted(phases.items())},
    }


def _pct(new: float, old: float) -> float:
    return ((new - old) / old * 100.0) if old else 0.0


def judge(base: dict, cand: dict) -> tuple[str, list[str]]:
    """Return a verdict and the reasons behind it.

    A change is kept only when it is faster by more than the noise band and
    neither behaviour nor identity moved. Anything else is reported as what
    it is rather than rounded in the hopeful direction.
    """
    notes: list[str] = []
    fatal = False

    if base["behaviour"] != cand["behaviour"]:
        fatal = True
        changed = sorted(
            cam for cam in set(base["behaviour"]) | set(cand["behaviour"])
            if base["behaviour"].get(cam) != cand["behaviour"].get(cam)
        )
        notes.append(f"behaviour changed on {len(changed)} camera(s): {', '.join(changed[:6])}")
        for cam in changed[:6]:
            notes.append(f"    {cam}: {base['behaviour'].get(cam)} -> {cand['behaviour'].get(cam)}")

    created = _pct(cand["identity"]["tracks_created"], base["identity"]["tracks_created"])
    lifetime = _pct(cand["identity"]["lifetime_avg"], base["identity"]["lifetime_avg"])
    notes.append(f"tracks created {created:+.1f}%, average lifetime {lifetime:+.1f}%")
    if created > MARGIN_PCT or lifetime < -MARGIN_PCT:
        fatal = True
        notes.append("    identity fragmented: the tracker is losing and re-creating people")

    fps_delta = _pct(cand["fps"] or 0.0, base["fps"] or 0.0)
    notes.append(f"FPS {base['fps']:.2f} -> {cand['fps']:.2f} ({fps_delta:+.1f}%)")

    if fatal:
        return "REJECT", notes
    if fps_delta > MARGIN_PCT:
        return "ACCEPT", notes
    if fps_delta < -MARGIN_PCT:
        notes.append("    slower than baseline")
        return "REJECT", notes
    notes.append(f"    inside the +/-{MARGIN_PCT:.0f}% noise band, not a result")
    return "INCONCLUSIVE", notes


def cmd_record(args: argparse.Namespace) -> None:
    record = build_record(load(Path(args.run_json)))
    STORE.mkdir(parents=True, exist_ok=True)
    out = STORE / f"{args.name}.json"
    out.write_text(json.dumps(record, indent=2, sort_keys=True))
    print(f"{out}")
    print(f"  cameras {record['cameras']}  frames {record['frames']}  FPS {record['fps']:.2f}")
    print(f"  tracks created {record['identity']['tracks_created']}"
          f"  lifetime {record['identity']['lifetime_avg']:.1f} frames")
    total_alerts = sum(b["alerts"] for b in record["behaviour"].values())
    print(f"  alerts {total_alerts}")


def cmd_diff(args: argparse.Namespace) -> None:
    base = json.loads((STORE / f"{args.base}.json").read_text())
    cand = json.loads((STORE / f"{args.candidate}.json").read_text())
    verdict, notes = judge(base, cand)
    print(f"{args.base} -> {args.candidate}: {verdict}")
    for note in notes:
        print(f"  {note}")

    if args.phases:
        print("\n  phase (ms/call)          base    cand    delta")
        keys = sorted(set(base["phases"]) | set(cand["phases"]))
        for key in keys:
            b = base["phases"].get(key, 0.0)
            c = cand["phases"].get(key, 0.0)
            if max(b, c) < 0.05:
                continue
            print(f"  {key:24s} {b:7.2f} {c:7.2f} {_pct(c, b):+7.1f}%")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="Store a run's JSON output as a named record.")
    rec.add_argument("run_json")
    rec.add_argument("--name", required=True)
    rec.set_defaults(func=cmd_record)

    dif = sub.add_parser("diff", help="Judge one record against another.")
    dif.add_argument("base")
    dif.add_argument("candidate")
    dif.add_argument("--phases", action="store_true", help="Also print the per-phase breakdown.")
    dif.set_defaults(func=cmd_diff)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
