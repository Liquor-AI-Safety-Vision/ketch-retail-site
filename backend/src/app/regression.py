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
    python backend/src/app/regression.py diff baseline motion-gate   # groups of repeats

Three repeats of the unchanged build settled how much each part can be
trusted, and the answer was not the same for all three. Alerts and identity
came back bit-identical every time. Throughput ranged 113.42 to 123.12 FPS,
an 8.6% spread on identical code, so throughput claims are judged between
groups of repeats rather than between single runs.
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
from backend.src.common.paths import outputs_dir
from backend.src.common.logging_setup import configure_logging

# Alerts and identity came back bit-identical across three repeats of an
# unchanged build, so this tolerance exists only to absorb a track that
# starts one frame either side of a boundary, not measurement spread.
IDENTITY_MARGIN_PCT = 2.0

STORE = outputs_dir() / "regression"

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


def judge(base: list[dict], cand: list[dict]) -> tuple[str, list[str]]:
    """Return a verdict on a group of runs against another group.

    The three parts are judged on very different evidence, because the
    baseline showed they behave very differently. Alerts and identity came
    back bit-identical across three repeats: 24 alerts, 396 tracks, the same
    per-camera split every time. Throughput did not, spreading 113.42 to
    123.12 FPS on an unchanged build. So any movement in the first two is
    real and disqualifying, while movement in the third has to clear a bar
    that a single fast run cannot fake.

    That bar is non-parametric, which three samples can actually support:
    the candidate's *worst* run must beat the baseline's *best* run. A real
    win of the size these phases predict clears it comfortably; noise of the
    size measured here essentially never does. A mean-versus-mean test with
    n=3 would have accepted an 8.6% swing that was nothing at all.
    """
    notes: list[str] = []
    fatal = False

    base_fps = sorted(r["fps"] for r in base)
    cand_fps = sorted(r["fps"] for r in cand)

    for label, group in (("baseline", base), ("candidate", cand)):
        if len({json.dumps(r["behaviour"], sort_keys=True) for r in group}) > 1:
            fatal = True
            notes.append(f"{label} is not self-consistent: alerts differ between its own repeats")

    if base[0]["behaviour"] != cand[0]["behaviour"]:
        fatal = True
        changed = sorted(
            cam for cam in set(base[0]["behaviour"]) | set(cand[0]["behaviour"])
            if base[0]["behaviour"].get(cam) != cand[0]["behaviour"].get(cam)
        )
        notes.append(f"alerts changed on {len(changed)} camera(s)")
        for cam in changed[:6]:
            notes.append(f"    {cam}: {base[0]['behaviour'].get(cam)} -> {cand[0]['behaviour'].get(cam)}")

    created = _pct(cand[0]["identity"]["tracks_created"], base[0]["identity"]["tracks_created"])
    lifetime = _pct(cand[0]["identity"]["lifetime_avg"], base[0]["identity"]["lifetime_avg"])
    notes.append(f"tracks created {created:+.1f}%, average lifetime {lifetime:+.1f}%")
    if created > IDENTITY_MARGIN_PCT or lifetime < -IDENTITY_MARGIN_PCT:
        fatal = True
        notes.append("    identity fragmented: the tracker is losing and re-creating people")

    notes.append(
        f"FPS baseline {base_fps[0]:.2f}-{base_fps[-1]:.2f} "
        f"-> candidate {cand_fps[0]:.2f}-{cand_fps[-1]:.2f} "
        f"(mean {sum(base_fps)/len(base_fps):.2f} -> {sum(cand_fps)/len(cand_fps):.2f})"
    )

    if fatal:
        return "REJECT", notes
    if cand_fps[0] > base_fps[-1]:
        notes.append(f"    every candidate run beat every baseline run "
                     f"({cand_fps[0]:.2f} > {base_fps[-1]:.2f})")
        return "ACCEPT", notes
    if cand_fps[-1] < base_fps[0]:
        notes.append("    every candidate run lost to every baseline run")
        return "REJECT", notes
    notes.append("    the two ranges overlap, so this is not distinguishable from noise")
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


def load_group(name: str) -> list[dict]:
    """Load every repeat stored under a name, or the single record by that name.

    "baseline" picks up baseline-1, baseline-2, baseline-3; "baseline-2"
    picks up just that one. A verdict on a single pair is possible but weak,
    so this warns rather than silently pretending one run is enough.
    """
    exact = STORE / f"{name}.json"
    if exact.exists():
        return [json.loads(exact.read_text())]
    # The raw run output sits beside the records as <name>-N.run.json; only
    # the reduced records are comparable.
    paths = sorted(p for p in STORE.glob(f"{name}-[0-9]*.json") if not p.name.endswith(".run.json"))
    if not paths:
        raise SystemExit(f"no records named {name} or {name}-N under {STORE}")
    return [json.loads(p.read_text()) for p in paths]


def cmd_diff(args: argparse.Namespace) -> None:
    base = load_group(args.base)
    cand = load_group(args.candidate)
    if len(base) < 2 or len(cand) < 2:
        print(f"warning: comparing {len(base)} baseline run(s) against {len(cand)} "
              f"candidate run(s); throughput spread was 8.6% on an unchanged build, "
              f"so fewer than 3 repeats cannot settle a throughput claim.")
    verdict, notes = judge(base, cand)
    print(f"{args.base} -> {args.candidate}: {verdict}")
    for note in notes:
        print(f"  {note}")

    if args.phases:
        print("\n  phase (ms/call)          base    cand    delta")
        keys = sorted(set(base[0]["phases"]) | set(cand[0]["phases"]))
        for key in keys:
            b = base[0]["phases"].get(key, 0.0)
            c = cand[0]["phases"].get(key, 0.0)
            if max(b, c) < 0.05:
                continue
            print(f"  {key:24s} {b:7.2f} {c:7.2f} {_pct(c, b):+7.1f}%")


def main() -> None:
    configure_logging()
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
