"""What does it cost to ask another process instead of calling in-process?

This is the gate on the whole rewrite. If a request that crosses a process
boundary costs materially more than the same call made locally, then moving
the models out is paying rent on every frame, and the CPU scaling it buys has
to cover that before it buys anything. Better to find that out here, against
one camera and nothing else running, than after the runtime has been
restructured around it.

Both sides are deliberately serialised: one caller, one lane, no overlap.
That is the worst case and the clearest one. In the running system sixteen
cameras overlap, so a caller waiting on a reply is not idle time for the
machine, but this measurement should not lean on that to look good.

    python backend/src/app/diag_transport.py
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import statistics
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def _frames(video: Path, count: int, start_ms: float) -> list[np.ndarray]:
    import cv2

    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, start_ms)
    out = []
    for _ in range(count):
        ok, frame = cap.read()
        if not ok:
            break
        out.append(np.ascontiguousarray(frame))
    cap.release()
    if not out:
        raise SystemExit(f"no frames read from {video}")
    return out


def _time(fn, frames, warmup: int, runs: int) -> tuple[float, float]:
    for i in range(warmup):
        fn(frames[i % len(frames)])
    samples = []
    for i in range(runs):
        start = time.perf_counter()
        fn(frames[i % len(frames)])
        samples.append((time.perf_counter() - start) * 1000.0)
    return statistics.mean(samples), statistics.median(samples)


def _bench_local(args, frames, boxes_by_frame):
    """Build the sessions in this process and time the calls made directly."""
    import stream_track as S

    from backend.src.runtime.shared_inference import SharedInferenceRegistry

    registry = SharedInferenceRegistry(cpu_only=False, config={
        "pose_num_workers": 1, "pose_max_batch": 1,
        "pose_batch_wait_s": 0.005, "pose_pad_batch": True,
        "ort_intra_threads": args.ort_threads,
        "ort_allow_spinning": not args.no_spin})

    def _build():
        S.REID_MODEL = args.reid_model
        S.ORT_PROVIDERS = None
        return S.Embedder()

    local_pose = registry.get_pose(args.pose_model)
    local_reid = registry.get_reid_embedder(args.reid_model, build=_build)

    def local_pose_call(frame):
        result = local_pose.detect(frame, conf=0.35)
        boxes_by_frame[id(frame)] = result.boxes
        return result

    lp_mean, _ = _time(local_pose_call, frames, args.warmup, args.runs)
    lr_mean, _ = _time(
        lambda f: local_reid(f, boxes_by_frame.get(id(f), np.empty((0, 5), np.float32))),
        frames, args.warmup, args.runs)
    detections = int(sum(len(b) for b in boxes_by_frame.values()))
    registry.close()
    return lp_mean, lr_mean, detections


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--pose-model", default=str(ROOT / "models/yolo26m-pose.onnx"))
    p.add_argument("--reid-model", default=str(ROOT / "models/osnet_x0_25_msmt17.onnx"))
    p.add_argument("--video", default=str(ROOT / "videos/cctv_counter/cash_till_open_2.mp4"))
    p.add_argument("--start-ms", type=float, default=60_000.0)
    p.add_argument("--frames", type=int, default=8)
    p.add_argument("--runs", type=int, default=200)
    p.add_argument("--warmup", type=int, default=30)
    p.add_argument("--no-spin", action="store_true",
                   help="Stop onnxruntime's thread pools from spin-waiting between calls.")
    p.add_argument("--no-models", action="store_true",
                   help="Diagnostics: run the server with nothing loaded, to price the boundary alone.")
    p.add_argument("--skip-local", action="store_true",
                   help="Do not build local sessions first, so the caller holds no CUDA context.")
    p.add_argument("--ort-threads", type=int, default=0,
                   help="Cap onnxruntime's intra-op pool. 0 leaves its default of one per core.")
    args = p.parse_args()

    from backend.src.runtime.frame_ring import FrameRing
    from backend.src.runtime.inference_server import (
        ClientChannel, InferenceClient, ServerConfig, serve,
    )
    from backend.src.runtime.shared_inference import SharedInferenceRegistry

    frames = _frames(Path(args.video), args.frames, args.start_ms)
    shape = frames[0].shape
    print(f"frame {shape[1]}x{shape[0]}, {len(frames)} distinct, {args.runs} runs each, "
          f"ort_intra_threads={args.ort_threads or 'default'}\n")

    # ---- local, the way it works today -----------------------------------
    boxes_by_frame = {}
    lp_mean = lr_mean = 0.0
    detections = 0
    if not args.skip_local:
        lp_mean, lr_mean, detections = _bench_local(args, frames, boxes_by_frame)

    # ---- remote, through the server --------------------------------------
    ctx = mp.get_context("spawn")
    ring, free_q = FrameRing.create(ctx, slots=4, frame_shape=shape)
    channel = ClientChannel(camera_id="bench", ring_spec=ring.spec,
                            reply_q=ctx.Queue(), lane=0)
    request_q, ready_q, stats_q = ctx.Queue(), ctx.Queue(), ctx.Queue()
    go_evt = ctx.Event()
    config = ServerConfig(pose_model=args.pose_model, reid_model=args.reid_model,
                          cpu_only=False, ort_intra_threads=args.ort_threads,
                          load_models=not args.no_models,
                          ort_allow_spinning=not args.no_spin)
    proc = ctx.Process(target=serve,
                       args=(config, [channel], [request_q], ready_q, go_evt, stats_q))
    proc.start()
    try:
        ready_q.get(timeout=300)
        go_evt.set()
        client = InferenceClient(channel, free_q, request_q)
        remote_pose = client.get_pose(args.pose_model)
        remote_reid = client.get_reid_embedder(args.reid_model)

        ping = client.get_ping()
        pg_mean, _pg_med = _time(ping, frames, args.warmup, args.runs)

        rp_mean = rr_mean = 0.0
        if not args.no_models:
            rp_mean, _ = _time(lambda f: remote_pose.detect(f, conf=0.35),
                               frames, args.warmup, args.runs)
            def _remote_reid(frame):
                return remote_reid(frame, boxes_by_frame.get(
                    id(frame), np.empty((0, 5), np.float32)))

            rr_mean, _ = _time(_remote_reid, frames, args.warmup, args.runs)
        client.close()
        request_q.put(None)
    finally:
        proc.join(timeout=120)
        if proc.is_alive():
            proc.terminate()
        ring.close()

    print(f"transport alone (ping, no model behind it): {pg_mean:.2f} ms\n")
    print(f"{'call':<12s} {'local':>10s} {'remote':>10s} {'overhead':>10s} {'':>8s}")
    for name, lm, rm in (("pose", lp_mean, rp_mean), ("reid", lr_mean, rr_mean)):
        over = rm - lm
        pct = (over / lm * 100.0) if lm else 0.0
        print(f"{name:<12s} {lm:8.2f}ms {rm:8.2f}ms {over:8.2f}ms {pct:+7.1f}%")

    print(f"\n{detections} detections across the sampled frames"
          + ("" if detections else "  <-- ReID had nothing to do, this number is meaningless"))
    print(f"per-frame overhead if a frame makes one pose and one reid call: "
          f"{(rp_mean - lp_mean) + (rr_mean - lr_mean):.2f} ms")


if __name__ == "__main__":
    main()
