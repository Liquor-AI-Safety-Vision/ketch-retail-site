"""Measure each inference module on its own, before blaming the architecture.

The tier ladder (diag_split_tiers.py) put the whole 14.53 FPS inside the
inference tier: both lanes were busy ~100% of the wall clock, spending
roughly 45ms on pose, 41ms on ReID and 40ms on till per frame. Those are
CPU-shaped numbers on a GPU that should do a 640x640 pose model in single
digits, so the question is no longer "which tier" but "which model, at which
batch size, with how many callers".

Everything here runs one model at a time against pre-built inputs, so no
decode, no queue and no other model can contaminate the number. Two things
get swept:

  raw    the ONNX session alone at a fixed batch size -- the floor, what the
         GPU can do with no Python around it
  service the real Shared*Service with its batch runner and N submitting
         threads -- what a lane actually gets

The gap between them is what the batch runner costs, and how the batch size
and submitter count trade against each other. The current split tier runs
pose with max_batch=1, which by construction can never batch anything; this
is the measurement that says whether that is the whole story.

    python ai/tools/diag_infer_modules.py --module pose
    python ai/tools/diag_infer_modules.py --module all
"""
from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# numpy is imported below rather than above on purpose: the BLAS pool sizes
# itself at import and never re-reads the environment, so the cap has to come
# first or it does nothing at all -- see perf.py.
from ai.src.runtime.perf import cap_native_threads  # noqa: E402

cap_native_threads(1)

import numpy as np  # noqa: E402
from ai.src.common.paths import ai_root, model_path
from ai.src.common.logging_setup import configure_logging

POSE_MODEL = model_path("yolo26m-pose.onnx")
REID_MODEL = model_path("osnet_x0_25_msmt17.onnx")
TILL_MODEL = model_path("till_seg_v4_640.onnx")


def _real_frames(n: int, camera_id: str = "counter_1"):
    """Real footage, not noise: detection count drives ReID cost, and noise
    would either find nothing or find nonsense, either way measuring the
    wrong thing."""
    import copy

    from ai.src.infra.local_config import camera_specs_from_manifest
    from ai.src.runtime.virtual_video_stream import VirtualVideoStreamSource

    specs = camera_specs_from_manifest(str(ai_root() / "config/stores/local_dev_store_16cams.json"))
    spec = copy.deepcopy(next(s for s in specs if s.camera_id == camera_id))
    spec.target_duration_s = max(4.0, n / max(1.0, spec.target_fps))
    frames = []
    for frame in VirtualVideoStreamSource(spec).iter_frames(pace_realtime=False):
        frames.append(frame.image_bgr.copy())
        if len(frames) >= n:
            break
    return frames


def _bench_threads(call, n_threads: int, n_calls: int, label: str):
    """n_threads submitters, n_calls each. Reports aggregate throughput and
    the latency one caller sees -- they diverge exactly when batching helps."""
    latencies: list[list[float]] = [[] for _ in range(n_threads)]
    barrier = threading.Barrier(n_threads + 1)

    def worker(t: int):
        barrier.wait()
        for i in range(n_calls):
            t0 = time.perf_counter()
            call(t, i)
            latencies[t].append(time.perf_counter() - t0)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    barrier.wait()
    started = time.perf_counter()
    for t in threads:
        t.join()
    wall = time.perf_counter() - started

    flat = [x for per in latencies for x in per]
    total = n_threads * n_calls
    print(f"    {label:<46} {total / wall:8.2f} calls/s   "
          f"latency p50={statistics.median(flat) * 1e3:6.1f}ms "
          f"p90={sorted(flat)[int(len(flat) * 0.9)] * 1e3:6.1f}ms", file=sys.stderr)
    return total / wall


def bench_pose_raw(blobs, batches=(1, 2, 4, 8, 16)):
    """The session with nothing around it, at fixed batch shapes."""
    import onnxruntime as ort

    from ai.src.runtime.shared_inference import _preferred_providers

    sess = ort.InferenceSession(POSE_MODEL, providers=_preferred_providers(False))
    inp = sess.get_inputs()[0].name
    dtype = np.float16 if sess.get_inputs()[0].type == "tensor(float16)" else np.float32
    print(f"  raw pose session ({sess.get_providers()[0]})", file=sys.stderr)
    for b in batches:
        batch = np.stack([blobs[i % len(blobs)] for i in range(b)]).astype(dtype)
        for _ in range(3):  # warm the shape: the CUDA EP re-plans per new shape
            sess.run(None, {inp: batch})
        t0 = time.perf_counter()
        reps = 20
        for _ in range(reps):
            sess.run(None, {inp: batch})
        wall = time.perf_counter() - t0
        per_call = wall / reps
        print(f"    batch={b:<3} {per_call * 1e3:7.2f} ms/call -> "
              f"{b / per_call:8.2f} frames/s  ({per_call / b * 1e3:6.2f} ms/frame)",
              file=sys.stderr)


def bench_pose_service(frames, blobs, metas, sweeps):
    from ai.src.runtime.shared_inference import SharedInferenceRegistry

    print("  SharedPoseService (batch runner + N submitting threads)", file=sys.stderr)
    for max_batch, workers, n_threads in sweeps:
        registry = SharedInferenceRegistry(cpu_only=False, config={
            "pose_num_workers": workers, "pose_max_batch": max_batch,
            "pose_batch_wait_s": 0.005, "pose_pad_batch": True})
        try:
            pose = registry.get_pose(POSE_MODEL)
            # Warm up: the first call through a new shape pays the EP's planning cost.
            for i in range(4):
                pose.detect_preprocessed(blobs[i % len(blobs)], conf=0.5, **metas[i % len(metas)])

            def call(t, i, _pose=pose):
                k = (t * 7 + i) % len(blobs)
                _pose.detect_preprocessed(blobs[k], conf=0.5, **metas[k])

            _bench_threads(call, n_threads, 25,
                           f"max_batch={max_batch} workers={workers} threads={n_threads}")
        finally:
            registry.close()


def bench_till_service(frames, sweeps):
    from ai.src.runtime.shared_inference import SharedInferenceRegistry

    print("  SharedTillService (letterboxes inside the runner thread)", file=sys.stderr)
    for max_batch, workers, n_threads in sweeps:
        registry = SharedInferenceRegistry(cpu_only=False, config={
            "till_num_workers": workers, "till_max_batch": max_batch,
            "till_batch_wait_s": 0.005})
        try:
            till = registry.get_till(TILL_MODEL, imgsz=640)
            for i in range(4):
                till.detect(frames[i % len(frames)], conf=0.7)

            def call(t, i, _till=till):
                _till.detect(frames[(t * 7 + i) % len(frames)], conf=0.7)

            _bench_threads(call, n_threads, 25,
                           f"max_batch={max_batch} workers={workers} threads={n_threads}")
        finally:
            registry.close()


def bench_reid(frames, boxes_per_frame, reid_model, thread_counts=(1, 2, 4)):
    """One private Embedder per thread -- sharing one is a data race on its
    pad buffer, found in the spike, so this measures the shape we actually run."""
    from ai.src.perception import stream_track as S

    print(f"  Embedder ({reid_model}, private instance per thread)", file=sys.stderr)
    for n_threads in thread_counts:
        S.REID_MODEL = reid_model
        S.ORT_PROVIDERS = None
        embs = [S.Embedder() for _ in range(n_threads)]
        for e in embs:
            for i in range(3):
                e(frames[i % len(frames)], boxes_per_frame[i % len(boxes_per_frame)])

        def call(t, i, _embs=embs):
            k = (t * 7 + i) % len(frames)
            _embs[t](frames[k], boxes_per_frame[k])

        _bench_threads(call, n_threads, 25, f"threads={n_threads}")


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--module", default="all", choices=["pose-raw", "pose", "till", "reid", "all"])
    p.add_argument("--frames", type=int, default=24)
    p.add_argument("--reid-model", default=REID_MODEL,
                   help="pair with the fixed-batch model to reproduce the old "
                        "cost, or the -dyn export to measure the fix")
    args = p.parse_args()

    frames = _real_frames(args.frames)
    print(f"{len(frames)} real frames, {frames[0].shape}", file=sys.stderr)

    from ai.src.runtime.shared_inference import SharedInferenceRegistry

    # Blobs + metas once, so the sweep never pays for preprocessing.
    prep_reg = SharedInferenceRegistry(cpu_only=False, config={
        "pose_num_workers": 1, "pose_max_batch": 1, "pose_batch_wait_s": 0.005,
        "pose_pad_batch": True})
    blobs, metas, boxes_per_frame = [], [], []
    try:
        pose = prep_reg.get_pose(POSE_MODEL)
        for f in frames:
            blob, meta = pose.preprocess(f)
            blobs.append(blob)
            metas.append(meta)
        for blob, meta in zip(blobs, metas):
            r = pose.detect_preprocessed(blob, conf=0.5, **meta)
            boxes_per_frame.append(r.boxes)
    finally:
        prep_reg.close()
    print(f"detections per frame: {[len(b) for b in boxes_per_frame]}", file=sys.stderr)

    sweeps = [(1, 1, 1), (1, 1, 2), (8, 2, 2), (8, 2, 4), (8, 2, 8), (8, 2, 16)]

    if args.module in {"pose-raw", "all"}:
        bench_pose_raw(blobs)
    if args.module in {"pose", "all"}:
        bench_pose_service(frames, blobs, metas, sweeps)
    if args.module in {"till", "all"}:
        bench_till_service(frames, [(1, 1, 1), (1, 1, 2), (8, 1, 2), (8, 2, 4), (8, 2, 8)])
    if args.module in {"reid", "all"}:
        bench_reid(frames, boxes_per_frame, args.reid_model)


if __name__ == "__main__":
    main()
