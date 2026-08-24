from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from backend.src.runtime.perf import cap_native_threads
from backend.src.infra.local_config import camera_specs_from_manifest
from backend.src.runtime.supervisor import RuntimeSupervisor


def _parse_camera_float_map(raw: str | None) -> dict[str, float]:
    out: dict[str, float] = {}
    if not raw:
        return out
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        camera_id, value = item.split("=", 1)
        out[camera_id.strip()] = float(value.strip())
    return out


def main():
    p = argparse.ArgumentParser(description="Run local backend runtime on virtual camera streams.")
    p.add_argument(
        "--manifest",
        default=str(ROOT / "backend/config/stores/local_dev_store.json"),
    )
    p.add_argument(
        "--db-path",
        default=str(ROOT / "backend/data/local_dev.sqlite"),
    )
    p.add_argument(
        "--gallery-db",
        default=str(ROOT / "backend/data/local_dev.sqlite"),
    )
    p.add_argument(
        "--media-root",
        default=str(ROOT / "backend/data/alerts"),
    )
    p.add_argument(
        "--camera-ids",
        help="Comma-separated subset of camera ids to run.",
    )
    p.add_argument(
        "--limit-frames",
        type=int,
        help="Stop each camera after this many processed frames.",
    )
    p.add_argument(
        "--limit-seconds",
        type=float,
        help="Stop each camera after this many seconds of target stream time.",
    )
    p.add_argument(
        "--smoke-test",
        action="store_true",
        help="Shortcut for a short local validation run. Defaults to 15 seconds per camera if no other limit is set.",
    )
    p.add_argument(
        "--pace-realtime",
        action="store_true",
        help="If set, replay videos at wall-clock speed instead of as fast as possible.",
    )
    p.add_argument(
        "--gpu",
        action="store_true",
        help="Reserved for Linux/CUDA runs. Local MacBook flow should omit this flag.",
    )
    p.add_argument(
        "--concurrency-mode",
        choices=["seq", "thread", "process"],
        default="seq",
        help="Run cameras sequentially, with threads, or with processes.",
    )
    p.add_argument(
        "--native-threads",
        type=int,
        default=1,
        help="Cap native threads per worker for OpenCV/BLAS style libraries.",
    )
    p.add_argument(
        "--camera-starts",
        help="Per-camera source start override, e.g. storage_1=55,counter_1=52",
    )
    p.add_argument(
        "--camera-durations",
        help="Per-camera target duration override, e.g. storage_1=20,counter_1=25",
    )
    p.add_argument(
        "--shared-pose-workers",
        type=int,
        default=2,
        help="Number of shared pose workers in thread mode.",
    )
    a = p.parse_args()
    cap_native_threads(a.native_threads)

    specs = camera_specs_from_manifest(a.manifest)
    start_overrides = _parse_camera_float_map(a.camera_starts)
    duration_overrides = _parse_camera_float_map(a.camera_durations)
    for spec in specs:
        if spec.camera_id in start_overrides:
            spec.start_s = start_overrides[spec.camera_id]
        if spec.camera_id in duration_overrides:
            spec.target_duration_s = duration_overrides[spec.camera_id]
    selected = None
    if a.camera_ids:
        selected = [part.strip() for part in a.camera_ids.split(",") if part.strip()]

    limit_frames = a.limit_frames
    limit_seconds = a.limit_seconds
    if a.smoke_test and limit_frames is None and limit_seconds is None:
        limit_seconds = 15.0

    supervisor = RuntimeSupervisor(
        specs,
        db_path=a.db_path,
        media_root=a.media_root,
        gallery_db_path=a.gallery_db,
        cpu_only=not a.gpu,
        native_threads=a.native_threads,
        shared_inference_config={
            "pose_num_workers": a.shared_pose_workers,
        },
    )
    results = supervisor.run(
        camera_ids=selected,
        pace_realtime=a.pace_realtime,
        limit_frames=limit_frames,
        limit_seconds=limit_seconds,
        concurrency_mode=a.concurrency_mode,
    )
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
