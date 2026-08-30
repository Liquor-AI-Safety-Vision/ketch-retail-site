from __future__ import annotations

import argparse
import json
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.src.runtime.perf import cap_native_threads


def _early_native_threads(argv: list[str], default: int = 1) -> int:
    """Read --native-threads out of argv before argparse exists.

    The cap has to be applied before numpy, cv2 or onnxruntime load, because
    that is when the BLAS and OpenMP pools size themselves and the only time
    they read the environment (see perf.py). argparse cannot run that early
    -- it needs imports of its own and the parser is built in main() -- so
    this reads the one flag that must be known sooner. Getting the ordering
    wrong is not a small mistake: it measured 5x on every inference call.
    """
    for i, arg in enumerate(argv):
        if arg == "--native-threads" and i + 1 < len(argv):
            value = argv[i + 1]
        elif arg.startswith("--native-threads="):
            value = arg.split("=", 1)[1]
        else:
            continue
        try:
            return max(1, int(value))
        except ValueError:
            break
    return default


cap_native_threads(_early_native_threads(sys.argv))

from backend.src.infra.local_config import camera_specs_from_manifest  # noqa: E402
from backend.src.runtime.supervisor import RuntimeSupervisor  # noqa: E402
from backend.src.common.paths import backend_root
from backend.src.pipelines.registry import COUNTER_CASH_THEFT


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


def _print_throughput_summary(results: list[dict], started_at: float) -> None:
    """Report throughput with and without model loading.

    Models are loaded before any camera starts serving frames, so folding that
    one-off cost into the rate understates what the running system sustains.
    """
    frames = sum(int(r.get("frames_processed", 0)) for r in results)
    starts = [r["processing_started_at"] for r in results if r.get("processing_started_at")]
    ends = [r["processing_ended_at"] for r in results if r.get("processing_ended_at")]
    total_s = time.time() - started_at
    print(f"\n=== throughput ===", file=sys.stderr)
    print(f"frames                 : {frames}", file=sys.stderr)
    print(f"wall-clock incl. load  : {total_s:7.1f} s  -> {frames / total_s:6.2f} FPS", file=sys.stderr)
    if starts and ends:
        load_s = min(starts) - started_at
        serving_s = max(ends) - min(starts)
        print(f"model load (before any) : {load_s:7.1f} s", file=sys.stderr)
        print(
            f"serving frames only    : {serving_s:7.1f} s  -> {frames / serving_s:6.2f} FPS",
            file=sys.stderr,
        )
    print("", file=sys.stderr)


def main():
    started_at = time.time()
    p = argparse.ArgumentParser(description="Run local backend runtime on virtual camera streams.")
    p.add_argument(
        "--manifest",
        default=str(backend_root() / "config/stores/local_dev_store.json"),
    )
    p.add_argument(
        "--db-path",
        default=str(backend_root() / "data/local_dev.sqlite"),
    )
    p.add_argument(
        "--gallery-db",
        default=str(backend_root() / "data/local_dev.sqlite"),
    )
    p.add_argument(
        "--media-root",
        default=str(backend_root() / "data/alerts"),
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
        choices=["seq", "thread", "process", "hybrid"],
        default="seq",
        help=(
            "Run cameras sequentially, with threads, with one process per camera, "
            "or hybrid: a fixed number of GPU/inference-owning processes "
            "(see --hybrid-workers), each running its shard of cameras on threads."
        ),
    )
    p.add_argument(
        "--hybrid-workers",
        type=int,
        default=2,
        help=(
            "Number of processes in hybrid mode, independent of camera count. "
            "Each one holds its own CUDA context, and pose latency scales with the "
            "context count (measured on one L40S: 19ms at 2, 45ms at 4, 153ms at 8), "
            "so raise this only if the GIL, not the GPU, is the limit."
        ),
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
    p.add_argument(
        "--pose-max-batch",
        type=int,
        default=1,
        help=(
            "Max pose batch. With padding on, every run uses exactly this shape. "
            "Batching across cameras loses to the GPU contention it adds here: "
            "measured 30.2 FPS at 1 vs 25.6 at 4 on a 16-camera L40S run."
        ),
    )
    p.add_argument(
        "--pose-batch-wait-s",
        type=float,
        default=0.005,
        help="How long a pose batch waits to fill before dispatching.",
    )
    p.add_argument(
        "--no-pose-pad-batch",
        action="store_true",
        help="Disable fixed-shape padding for pose (lets batch size vary per run).",
    )
    p.add_argument(
        "--decode-only",
        action="store_true",
        help="Only read and downsample sources without running any pipeline modules.",
    )
    p.add_argument(
        "--till-fps",
        type=float,
        help=(
            "Override how often the cash drawer is sampled, in Hz. Defaults to the "
            "camera's target_fps (every frame). The confirm streak is rescaled so "
            "the debounce window keeps the same length in seconds."
        ),
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
    if a.till_fps is not None:
        for spec in specs:
            for binding in spec.pipeline_bindings:
                if binding.pipeline_id == COUNTER_CASH_THEFT:
                    binding.params["till_fps"] = a.till_fps
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
        skip_pipelines=a.decode_only,
        shared_inference_config={
            "pose_num_workers": a.shared_pose_workers,
            "pose_max_batch": a.pose_max_batch,
            "pose_batch_wait_s": a.pose_batch_wait_s,
            "pose_pad_batch": not a.no_pose_pad_batch,
        },
        hybrid_workers=a.hybrid_workers,
    )
    results = supervisor.run(
        camera_ids=selected,
        pace_realtime=a.pace_realtime,
        limit_frames=limit_frames,
        limit_seconds=limit_seconds,
        concurrency_mode=a.concurrency_mode,
    )
    _print_throughput_summary(results, started_at)
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
