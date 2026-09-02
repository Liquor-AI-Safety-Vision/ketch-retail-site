"""Does the till FSM still catch a theft when the camera runs at 5 fps?

The FSM's confirm windows come from an appendix that counted FRAMES at 10 fps
-- HAND_INSIDE_CONFIRM = 3, DWELL_CONFIRM = 5, and so on. The counter cameras
in the manifest run at 5, and _configure_fsm_globals converts by scaling:
frames_10fps * fps / 10. So at 5 fps every window is half as many frames,
covering the same wall-clock duration.

That conversion is arithmetically obvious and behaviourally unverified. Half
the frames means half the evidence: a confirm window of 2 frames instead of 4
is a shorter chance to observe, and a wrist that dips into the drawer for
three tenths of a second is either sampled twice or once depending on where
the sampling lands. Whether the alert still fires is a measurement.

So this runs the real CounterCashTheftPipeline -- real pose model, real till
segmentation, real tracker, real FSM -- over the same clip twice, once
sampled to 10 fps and once to 5, and reports where each run got to.

    python3 dev/tools/fsm_fps_ab.py --videos a.mp4 b.mp4 --fps 10 5

Reporting the alert count alone would be useless when the answer is zero: a
zero can mean the drawer was never seen open, the person was never on the
staff side of the divider, the hand never reached the drawer, or the dwell
never confirmed. Those are four different problems and only one of them is
about frame rate, so the FSM's state ladder is traced per frame and the
furthest state reached is reported next to the count.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.src.common.logging_setup import configure_logging  # noqa: E402
from backend.src.common.paths import zones_dir  # noqa: E402
from backend.src.domain.models import (  # noqa: E402
    CameraPipelineBindingSpec,
    CameraStreamSpec,
)


def _spec(video: Path, fps: float, duration_s: float,
          start_s: float = 0.0) -> CameraStreamSpec:
    """One camera, this clip, at this frame rate. Everything else as shipped.

    start_s shifts the sampling PHASE, which is the point of having it here.
    The source clips are 24 fps; sampling to 5 takes one frame in 4.8, so two
    runs that start a twentieth of a second apart see different frames of the
    same motion. If the alert survives at one phase and not another, the
    frame rate is the reason -- and a single run at phase zero would have
    reported success either way.
    """
    return CameraStreamSpec(
        company_id="fsm_ab",
        store_id="fsm_ab",
        store_display_name="FSM fps A/B",
        store_timezone="America/New_York",
        camera_id=f"{video.stem}@{fps:g}fps+{start_s:g}s",
        display_name=video.stem,
        camera_type="counter",
        source_kind="video_file",
        source_uri=str(video),
        source_path=str(video),
        strategy="trim",
        start_s=start_s,
        target_duration_s=duration_s,
        target_fps=fps,
        runtime_profile={"target_fps": fps},
        # The manifest's own counter perception block.
        perception_profile={"person_detector": {"conf_thres": 0.5}},
        simulation_profile={},
        pipeline_bindings=[],
        raw_config={},
    )


def _binding(params_extra: dict | None) -> CameraPipelineBindingSpec:
    """counter_generated_test.json's params for these very clips."""
    params = {
        "suppress_if_customer_recent": True,
        "customer_recent_window_s": 3.0,
        "zones_path": str(zones_dir() / "top_side_view_zones.json"),
        "till_conf": 0.7,
        "till_confirm_frames": 5,
        "alert_clip": {"pre_s": 15, "post_s": 15},
    }
    if params_extra:
        params.update(params_extra)
    return CameraPipelineBindingSpec(
        pipeline_id="counter_cash_theft_v1", enabled=True, params=params)


class _CountingStore:
    """Records alerts instead of writing them.

    A real SqliteAlertStore would work, but it needs stores/cameras seeded and
    leaves a database behind per run; nothing being measured here depends on
    the write actually landing.
    """

    def __init__(self):
        self.alerts = []

    def create_alert(self, **kw):
        self.alerts.append(kw)
        return f"alert_{len(self.alerts)}"

    def add_alert_media(self, **kw):
        return None


class _NullMedia:
    def save_snapshot(self, *a, **k):
        return "snapshot.jpg"

    def save_clip(self, *a, **k):
        return "clip.mp4"


def run_one(video: Path, fps: float, duration_s: float, params_extra=None,
            start_s: float = 0.0) -> dict:
    from backend.src.pipelines.counter_cash_theft import CounterCashTheftPipeline
    from backend.src.runtime.rtsp_stream import open_stream_source

    spec = _spec(video, fps, duration_s, start_s)
    binding = _binding(params_extra)
    spec.pipeline_bindings = [binding]
    store = _CountingStore()

    pipeline = CounterCashTheftPipeline(
        spec, binding, store, _NullMedia(), run_id="fsm_ab",
        cpu_only=False, shared_inference=None)

    # The FSM constants this run will actually use, read back after
    # _configure_fsm_globals rather than recomputed here -- the point is to
    # report what ran, not what was intended.
    source = open_stream_source(spec)

    ladder = []          # furthest state each track reached
    till_open_frames = 0
    staff_frames = 0
    frames = 0
    started = time.time()

    # The counters each confirm window is compared against, at their peak.
    # "Did it alert" is a yes/no; how far the deciding counter got past its
    # threshold is the margin, and the margin is what a halved frame budget
    # actually spends. A run that confirms at exactly the threshold alerts
    # today and stops alerting the first time one pose detection is missed.
    peak = {"inside_run": 0, "outside_run": 0, "body_dwell": 0}
    alert_frame = None

    for frame in source.iter_frames(pace_realtime=False):
        out = pipeline.process_frame(frame)
        frames += 1
        if out.get("alerts_emitted") and alert_frame is None:
            alert_frame = frame.frame_idx
        if pipeline.last_till_open:
            till_open_frames += 1
        for tid, event in pipeline.events_by_track.items():
            ladder.append((tid, event.state))
            peak["inside_run"] = max(peak["inside_run"], *event.inside_run.values())
            peak["outside_run"] = max(peak["outside_run"], event.outside_run)
            peak["body_dwell"] = max(peak["body_dwell"], *event.body_dwell.values())
        staff_frames += int(
            pipeline.timing.stats["fsm_logic::tracks_considered"].total_s > 0
            if "fsm_logic::tracks_considered" in pipeline.timing.stats else 0)

    pipeline.finalize()
    elapsed = time.time() - started

    F = pipeline.F
    state_names = {getattr(F, n): n for n in
                   ("IDLE", "DRAWER_OPEN", "HAND_IN_DRAWER", "HAND_EXITED",
                    "HAND_TO_BODY", "CONCEALED", "ENDED")
                   if hasattr(F, n)}
    order = ["IDLE", "DRAWER_OPEN", "HAND_IN_DRAWER", "HAND_EXITED",
             "HAND_TO_BODY", "CONCEALED", "ENDED"]
    reached = Counter(state_names.get(s, str(s)) for _tid, s in ladder)
    furthest = max((order.index(n) for n in reached if n in order), default=-1)

    return {
        "video": video.name,
        "fps": fps,
        "start_s": start_s,
        "frames": frames,
        "wall_s": round(elapsed, 1),
        "alerts": len(store.alerts),
        "till_open_frames": till_open_frames,
        "till_state_changes": pipeline.metrics.get("till_state_changes", 0),
        "state_transitions": pipeline.metrics.get("state_transitions", 0),
        "suppressed_events": pipeline.metrics.get("suppressed_events", 0),
        "max_live_tracks": pipeline.metrics.get("max_live_tracks", 0),
        "detections_total": pipeline.metrics.get("detections_total", 0),
        "fsm_tracks_considered": round(
            pipeline.timing.stats["fsm_logic::tracks_considered"].total_s, 1)
        if "fsm_logic::tracks_considered" in pipeline.timing.stats else 0.0,
        "furthest_state": order[furthest] if furthest >= 0 else "none",
        "states_seen": dict(reached),
        "alert_frame": alert_frame,
        "alert_at_s": round(alert_frame / fps, 2) if alert_frame is not None else None,
        # peak counter vs the threshold it had to clear. 1.0 means it
        # confirmed on the last frame available to it and nothing to spare.
        "margins": {
            "hand_inside": [peak["inside_run"], F.HAND_INSIDE_CONFIRM],
            "hand_exit": [peak["outside_run"], F.HAND_EXIT_CONFIRM],
            "body_dwell": [peak["body_dwell"], F.DWELL_CONFIRM],
        },
        "fsm_constants": {
            "FPS": F.FPS,
            "HAND_INSIDE_CONFIRM": F.HAND_INSIDE_CONFIRM,
            "HAND_EXIT_CONFIRM": F.HAND_EXIT_CONFIRM,
            "POSE_GRACE": F.POSE_GRACE,
            "DWELL_CONFIRM": F.DWELL_CONFIRM,
            "STATE_TIMEOUT": F.STATE_TIMEOUT,
            "POST_EVENT": F.POST_EVENT,
            "TRAJ_WINDOW": F.TRAJ_WINDOW,
            "TRAJ_LOOKBACK": F.TRAJ_LOOKBACK,
            "DRAWER_IN": F.DRAWER_IN,
            "DEEP_IN": F.DEEP_IN,
            "WITHDRAW_DELTA": F.WITHDRAW_DELTA,
            "POCKET_RADIUS": F.POCKET_RADIUS,
        },
    }


def main() -> None:
    configure_logging("WARNING")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--videos", nargs="+", required=True)
    p.add_argument("--fps", nargs="+", type=float, default=[10.0, 5.0])
    p.add_argument("--duration", type=float, default=8.0)
    p.add_argument("--start-s", nargs="+", type=float, default=[0.0],
                   help="sampling phases to try; 24 fps source, so 0.042 is one source frame")
    p.add_argument("--out", default="dev/outputs/fsm_fps_ab")
    p.add_argument("--params-json", default=None,
                   help="extra pipeline params as JSON, e.g. '{\"fsm\":{...}}'")
    args = p.parse_args()

    extra = json.loads(args.params_json) if args.params_json else None
    out_dir = REPO / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for video in args.videos:
        path = Path(video)
        if not path.is_absolute():
            path = REPO / path
        for fps in args.fps:
            for start_s in args.start_s:
                print(f"--- {path.name} @ {fps:g} fps  phase +{start_s:g}s", flush=True)
                r = run_one(path, fps, args.duration, extra, start_s)
                results.append(r)
                print(f"    frames={r['frames']} alerts={r['alerts']} "
                      f"till_open={r['till_open_frames']} "
                      f"furthest={r['furthest_state']} ({r['wall_s']}s)", flush=True)

    tag = "-".join(f"{f:g}" for f in args.fps)
    if len(args.start_s) > 1:
        tag += "_phases"
    out = out_dir / f"fsm_fps_ab_{tag}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\n{out}")


if __name__ == "__main__":
    main()
