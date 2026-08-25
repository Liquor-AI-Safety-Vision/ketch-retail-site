"""Wire up decode / inference / business as three tiers and run 16 cameras.

This is the production version of the shape spike_split_arch.py measured
with throwaway inline code (174.81 FPS against the current architecture's
136.30, +28.2%, 460 tracks identical on both sides). Here the same shape runs
on the real classes: FrameRing, SharedPoseService and Embedder through
InferenceLane, and the actual uae_tracker.Tracker through business_tier.

One ring per camera, sized for the largest frame this deployment handles.
Each camera is pinned to exactly one decoder thread, one inference lane, and
one business thread -- pinning is what keeps a camera's frames in the order
they were produced without a re-sorting stage, since a queue with a single
consumer preserves write order.

    python backend/src/app/run_split_pipeline.py --cameras 16 \\
        --decoders 4 --inferers 2 --infer-threads 2 --business 2
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

MAX_FRAME_SHAPE = (1080, 1920, 3)


def _shard(items, n):
    return [items[i::n] for i in range(n) if items[i::n]]


def run(specs, *, decoders: int, inferers: int, infer_threads: int, businesses: int,
       pose_model: str, reid_model: str, till_model: str | None, till_imgsz: int,
       conf: float, till_conf: float | None, till_every_n: int, slots_per_camera: int):
    from backend.src.runtime.business_tier import run_business_process
    from backend.src.runtime.decode_tier import run_decoder_process
    from backend.src.runtime.frame_ring import FrameRing
    from backend.src.runtime.inference_server import InferenceConfig, run_inference_process

    ctx = mp.get_context("spawn")

    # One ring per camera. A camera's producer (decoder) and its two
    # consumers (inference, business) all attach to the same ring by name.
    rings: dict = {}
    free_qs: dict = {}
    ring_specs: dict = {}
    for spec in specs:
        pose_imgsz = 640
        ring, free_q = FrameRing.create(
            ctx, slots=slots_per_camera, frame_shape=MAX_FRAME_SHAPE,
            blob_shape=(3, pose_imgsz, pose_imgsz))
        rings[spec.camera_id] = ring
        free_qs[spec.camera_id] = free_q
        ring_specs[spec.camera_id] = ring.spec

    dec_shards = _shard(specs, decoders)
    lane_count = inferers * infer_threads
    lane_shards = _shard(specs, lane_count)
    biz_shards = _shard(specs, businesses)

    decode_qs = {}
    for shard in lane_shards:
        q = ctx.Queue(maxsize=slots_per_camera * len(shard) * 2)
        for spec in shard:
            decode_qs[spec.camera_id] = q

    # One queue per camera, not per shard: several cameras' threads live in
    # the same business process, and each thread must be the sole reader of
    # its own camera's messages. A queue shared between cameras would let one
    # camera's thread steal another's frame off the queue -- decode_qs can
    # share one queue per lane because exactly one thread reads each of
    # those, but business_qs cannot.
    business_qs = {spec.camera_id: ctx.Queue(maxsize=slots_per_camera * 2) for spec in specs}

    ready_q, stats_q = ctx.Queue(), ctx.Queue()
    go_evt = ctx.Event()

    dec_procs = [ctx.Process(
        target=run_decoder_process,
        args=(shard, ring_specs, free_qs, decode_qs),
        kwargs=dict(pose_imgsz=640, pose_dtype_name="float32", conf=conf,
                   till_conf=till_conf, till_every_n=till_every_n,
                   ready_q=ready_q, go_evt=go_evt, stats_q=stats_q))
        for shard in dec_shards]

    infer_config = InferenceConfig(pose_model=pose_model, reid_model=reid_model,
                                   till_model=till_model, till_imgsz=till_imgsz)
    infer_procs = []
    for k in range(inferers):
        my_lanes = lane_shards[k * infer_threads:(k + 1) * infer_threads]
        my_lane_qs = [decode_qs[shard[0].camera_id] for shard in my_lanes]
        my_ring_specs = {spec.camera_id: ring_specs[spec.camera_id]
                        for shard in my_lanes for spec in shard}
        infer_procs.append(ctx.Process(
            target=run_inference_process,
            args=(infer_config, my_lane_qs, business_qs, my_ring_specs,
                 ready_q, go_evt, stats_q)))

    biz_procs = []
    for shard in biz_shards:
        my_ring_specs = {spec.camera_id: ring_specs[spec.camera_id] for spec in shard}
        my_free_qs = {spec.camera_id: free_qs[spec.camera_id] for spec in shard}
        my_business_qs = {spec.camera_id: business_qs[spec.camera_id] for spec in shard}
        biz_procs.append(ctx.Process(
            target=run_business_process,
            args=(shard, my_ring_specs, my_free_qs, my_business_qs, ready_q, go_evt, stats_q)))

    all_procs = dec_procs + infer_procs + biz_procs
    for p in all_procs:
        p.start()
    load_started = time.time()
    for _ in all_procs:
        ready_q.get()
    load_s = time.time() - load_started
    go_evt.set()

    # Shutdown walks downstream one tier at a time. A decoder finishes on its
    # own when its cameras' streams end; only then is it safe to stop the
    # lanes reading from it, and only once those have drained is it safe to
    # stop the business workers reading from them. Stopping early would
    # discard frames still in flight.
    all_stats = []
    for _ in dec_procs:
        all_stats.append(stats_q.get())
    for q in {id(q): q for q in decode_qs.values()}.values():
        q.put(None)

    for _ in infer_procs:
        all_stats.append(stats_q.get())
    for q in business_qs.values():
        q.put(None)

    for _ in biz_procs:
        all_stats.append(stats_q.get())

    for p in all_procs:
        p.join(timeout=120)
        if p.is_alive():
            p.terminate()
    for ring in rings.values():
        ring.close()

    return all_stats, load_s


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--manifest", default=str(ROOT / "backend/config/stores/local_dev_store_16cams.json"))
    p.add_argument("--pose-model", default=str(ROOT / "models/yolo26m-pose.onnx"))
    p.add_argument("--reid-model", default=str(ROOT / "models/osnet_x0_25_msmt17.onnx"))
    p.add_argument("--cameras", type=int, default=16)
    p.add_argument("--decoders", type=int, default=4)
    p.add_argument("--inferers", type=int, default=2)
    p.add_argument("--infer-threads", type=int, default=2)
    p.add_argument("--business", type=int, default=2)
    p.add_argument("--slots-per-camera", type=int, default=6)
    p.add_argument("--conf", type=float, default=0.35)
    args = p.parse_args()

    from backend.src.infra.local_config import camera_specs_from_manifest

    specs = camera_specs_from_manifest(args.manifest)[:args.cameras]
    print(f"cameras={len(specs)} decoders={args.decoders} "
         f"inferers={args.inferers}x{args.infer_threads} business={args.business}",
         file=sys.stderr)

    stats, load_s = run(
        specs, decoders=args.decoders, inferers=args.inferers,
        infer_threads=args.infer_threads, businesses=args.business,
        pose_model=args.pose_model, reid_model=args.reid_model,
        till_model=None, till_imgsz=640, conf=args.conf,
        till_conf=None, till_every_n=1, slots_per_camera=args.slots_per_camera)

    biz_stats = [s for group in stats for s in group if hasattr(s, "tracks_created")]
    frames = sum(s.frames for s in biz_stats)
    tracks = sum(s.tracks_created for s in biz_stats)
    starts = [s.started for s in biz_stats]
    ends = [s.ended for s in biz_stats]
    serving = max(ends) - min(starts) if starts else 0.0

    print(f"\n=== split pipeline ===", file=sys.stderr)
    print(f"model load (before clock) : {load_s:7.1f} s", file=sys.stderr)
    print(f"frames                    : {frames}", file=sys.stderr)
    print(f"tracks created            : {tracks}", file=sys.stderr)
    print(f"serving                   : {serving:7.1f} s  -> {frames / serving:6.2f} FPS",
         file=sys.stderr)


if __name__ == "__main__":
    main()
