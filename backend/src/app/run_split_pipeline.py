"""Wire up decode / inference / business / face as four tiers and run a store.

This is the production version of the shape spike_split_arch.py first
measured with throwaway inline code (174.81 FPS against the then-current
architecture's 136.30, +28.2%). Here it runs the real pipelines --
CounterCashTheftPipeline, StorageIntrusionPipeline, TrackingBaselinePipeline
-- with till, face identity, zones, FSM, clip buffering and SQLite alerts,
not just pose+ReID+tracking.

Each camera is pinned to exactly one decoder thread, one inference lane, and
one business thread. Pinning is what keeps a camera's frames in the order
they were produced without a re-sorting stage, since a queue with a single
consumer preserves write order -- and till_confirm specifically needs
consecutive samples, so losing that order would silently break its debounce.

Shutdown walks the tiers in the order frames actually flow through them:
decoders finish on their own (their streams end) -> their lanes' queues get
a stop signal -> inference lanes finish -> business queues get a stop signal
-> business workers drain any outstanding face answers before finishing ->
only then does the face queue get its stop signal. Stopping face earlier
would drop answers business workers are still waiting on; stopping business
earlier would drop the tail of perception.

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

# Before anything imports numpy, cv2 or onnxruntime -- see perf.py. Every
# tier's process is spawned from here and inherits this environment at
# interpreter startup, which is the only moment the BLAS and OpenMP pools
# read it. Capping inside each process entry point instead was a no-op that
# cost 5x on every inference call.
from backend.src.runtime.perf import cap_native_threads  # noqa: E402

cap_native_threads(1)

MAX_FRAME_SHAPE = (1080, 1920, 3)

# The decoder writes pose blobs of this shape and dtype into every ring slot,
# and the inference lanes verify at startup that the pose model agrees (a
# mismatch would otherwise read the right bytes as the wrong type and find
# nothing, on every frame, without raising). They are named here because the
# ring, the decoder and the check all have to be given the same values --
# three literals in three places is how they drift.
#
# models_fp16/ exists but is not reachable from this driver yet: switching to
# it means setting POSE_BLOB_DTYPE = "float16" here, which flows to the ring's
# blob_dtype and the decoder's pose_dtype_name together. Untested, so it is
# left on float32 rather than offered as a flag that has never been run.
POSE_IMGSZ = 640
POSE_BLOB_DTYPE = "float32"

# Total grace for every tier to exit before they are terminated, shared
# across all of them rather than applied per process.
SHUTDOWN_GRACE_S = 30.0
DEFAULT_POSE_MODEL = str(ROOT / "models/yolo26m-pose.onnx")
DEFAULT_REID_MODEL = str(ROOT / "models/osnet_x0_25_msmt17.onnx")
DEFAULT_TILL_MODEL = str(ROOT / "models/till_seg_v4_640.onnx")
DEFAULT_FACE_MODEL = str(ROOT / "models/yolov6s_face.onnx")
DEFAULT_ARCFACE_MODEL = str(ROOT / "models/ms1mv3_arcface_r18.onnx")


def _shard(items, n):
    return [items[i::n] for i in range(n) if items[i::n]]


def _default_conf_for(spec) -> float:
    """Each pipeline class falls back to a different conf_thres when the
    manifest omits one -- 0.45 for storage_intrusion, 0.35 otherwise. The
    decoder decides conf before any pipeline object exists, so it has to
    know the same defaults rather than picking one arbitrarily."""
    for binding in spec.pipeline_bindings:
        if binding.pipeline_id in {"storage_intrusion_v1", "cold_storage_intrusion_v1"}:
            return 0.45
    return 0.35


def _camera_decode_config(spec):
    from backend.src.runtime.decode_tier import CameraDecodeConfig

    conf = float(spec.perception_profile.get("person_detector", {}).get(
        "conf_thres", _default_conf_for(spec)))
    for binding in spec.pipeline_bindings:
        if binding.pipeline_id != "counter_cash_theft_v1" or not binding.enabled:
            continue
        from backend.src.pipelines.till_schedule import compute_till_schedule

        params = binding.params
        schedule = compute_till_schedule(
            spec.target_fps, int(params.get("till_confirm_frames", 5)), params.get("till_fps"))
        return CameraDecodeConfig(
            conf=conf, till_conf=float(params.get("till_conf", 0.70)), till_every_n=schedule.every_n)
    return CameraDecodeConfig(conf=conf)


def _camera_face_config(spec):
    from backend.src.runtime.inference_server import FaceCameraConfig

    for binding in spec.pipeline_bindings:
        if binding.pipeline_id not in {"storage_intrusion_v1", "cold_storage_intrusion_v1"}:
            continue
        face_cfg = spec.perception_profile.get("face_identity", {})
        if not face_cfg.get("enabled", False):
            return None
        return FaceCameraConfig(
            face_conf=float(face_cfg.get("face_conf", 0.20)),
            crop_expand=float(face_cfg.get("crop_expand", 0.12)),
            iou_thres=float(face_cfg.get("iou_thres", 0.45)),
            match_threshold=float(face_cfg.get("match_threshold", 0.30)),
            match_margin=float(face_cfg.get("match_margin", 0.10)),
        )
    return None


def run(specs, *, decoders: int, inferers: int, infer_threads: int, businesses: int,
       pose_model: str, reid_model: str, till_model: str, till_imgsz: int,
       face_model: str, arcface_model: str, db_path: str, media_root: str,
       gallery_db_path: str, slots_per_camera: int,
       pose_num_workers: int = 1, pose_max_batch: int = 1,
       pose_blob_dtype: str = POSE_BLOB_DTYPE):
    from backend.src.runtime.business_tier import run_business_process
    from backend.src.runtime.decode_tier import run_decoder_process
    from backend.src.runtime.frame_ring import FrameRing
    from backend.src.runtime.inference_server import InferenceConfig, run_face_process, run_inference_process

    ctx = mp.get_context("spawn")

    # Validated before a single byte of shared memory is allocated. The
    # earlier version checked the lane count after building every ring, so
    # the raise leaked all of them -- and it only checked lanes, while
    # decoders and business workers shard through the same _shard() that
    # silently drops empty shards. Asking for more of any of them than there
    # are cameras produces a run of a different shape than the one requested,
    # which still reports a perfectly believable FPS.
    _validate_topology(len(specs), decoders=decoders, inferers=inferers,
                       infer_threads=infer_threads, businesses=businesses,
                       slots_per_camera=slots_per_camera)

    camera_decode_cfg = {spec.camera_id: _camera_decode_config(spec) for spec in specs}
    camera_face_cfg = {spec.camera_id: cfg for spec in specs
                       if (cfg := _camera_face_config(spec)) is not None}
    needs_till = any(cfg.till_conf is not None for cfg in camera_decode_cfg.values())

    # One ring per camera. A camera's producer (decoder) and its two
    # consumers (inference, business) all attach to the same ring by name.
    rings: dict = {}
    free_qs: dict = {}
    ring_specs: dict = {}

    # The allocation loop is inside the try, not above it. Sixteen cameras is
    # sixteen separate allocations of roughly 90MB each, and the one that
    # fails is rarely the first: run out of shared memory on camera 12 and
    # the eleven already mapped have an owner that never reaches close(), so
    # nothing ever unlinks them. FrameRing.create() cleans up a partial
    # allocation within a single ring; nothing was cleaning up a partial
    # allocation across rings.
    try:
        for spec in specs:
            ring, free_q = FrameRing.create(
                ctx, slots=slots_per_camera, frame_shape=MAX_FRAME_SHAPE,
                blob_shape=(3, POSE_IMGSZ, POSE_IMGSZ), blob_dtype=pose_blob_dtype)
            rings[spec.camera_id] = ring
            free_qs[spec.camera_id] = free_q
            ring_specs[spec.camera_id] = ring.spec

        return _run(specs, ctx=ctx, rings=rings, free_qs=free_qs, ring_specs=ring_specs,
                    camera_decode_cfg=camera_decode_cfg, camera_face_cfg=camera_face_cfg,
                    needs_till=needs_till, decoders=decoders, inferers=inferers,
                    infer_threads=infer_threads, businesses=businesses,
                    pose_model=pose_model, reid_model=reid_model, till_model=till_model,
                    till_imgsz=till_imgsz, face_model=face_model, arcface_model=arcface_model,
                    db_path=db_path, media_root=media_root, gallery_db_path=gallery_db_path,
                    slots_per_camera=slots_per_camera, pose_num_workers=pose_num_workers,
                    pose_max_batch=pose_max_batch, pose_blob_dtype=pose_blob_dtype)
    finally:
        # The rings are owned here, so this is the only place that can unlink
        # them. Without the finally, any failure between allocation and the
        # end of the run leaves 16 cameras' worth of shared memory behind --
        # which is what produces the "leaked shared_memory objects" warning
        # at interpreter exit.
        for ring in rings.values():
            ring.close()


def _validate_topology(n_cameras: int, *, decoders: int, inferers: int,
                       infer_threads: int, businesses: int, slots_per_camera: int) -> None:
    """Fail fast on a topology that cannot be built as asked.

    _shard() drops empty shards, so every one of these counts silently
    degrades rather than failing: ask for 4 inference processes on 2 cameras
    and 2 of them start, load models, report ready and then sit idle for the
    whole run. The number that comes out is real, it is just not a number for
    the topology in the command line. That already happened once here.
    """
    counts = {"decoders": decoders, "inferers": inferers,
              "infer_threads": infer_threads, "businesses": businesses,
              "slots_per_camera": slots_per_camera}
    for name, value in counts.items():
        if value < 1:
            raise ValueError(f"{name}={value}: must be at least 1")
    if n_cameras < 1:
        raise ValueError("no cameras selected")

    lanes = inferers * infer_threads
    for name, value in (("decoders", decoders), ("businesses", businesses),
                        (f"lanes (inferers*infer_threads = {inferers}*{infer_threads})", lanes)):
        if value > n_cameras:
            raise ValueError(
                f"{name} = {value} for only {n_cameras} cameras; each needs at least one "
                f"camera or the extras sit idle and the run measures a different topology "
                f"than the one requested")


def _run(specs, *, ctx, rings, free_qs, ring_specs, camera_decode_cfg, camera_face_cfg,
         needs_till, decoders, inferers, infer_threads, businesses, pose_model, reid_model,
         till_model, till_imgsz, face_model, arcface_model, db_path, media_root,
         gallery_db_path, slots_per_camera, pose_num_workers=1, pose_max_batch=1,
         pose_blob_dtype=POSE_BLOB_DTYPE):
    """Start the tiers, run them, shut them down in the order frames flow.

    Split out from run() so the rings run() owns are closed in its finally
    no matter how this returns.
    """
    from backend.src.runtime.business_tier import run_business_process
    from backend.src.runtime.decode_tier import run_decoder_process
    from backend.src.runtime.inference_server import (
        InferenceConfig, run_face_process, run_inference_process)
    from backend.src.runtime.tier_status import await_message

    dec_shards = _shard(specs, decoders)
    lane_shards = _shard(specs, inferers * infer_threads)
    biz_shards = _shard(specs, businesses)

    decode_qs = {}
    for shard in lane_shards:
        q = ctx.Queue(maxsize=slots_per_camera * len(shard) * 2)
        for spec in shard:
            decode_qs[spec.camera_id] = q

    # One queue per camera, not per shard: several cameras' threads live in
    # the same business process, and each thread must be the sole reader of
    # its own camera's messages -- a shared queue would let one camera's
    # thread steal another's message off the queue.
    business_qs = {spec.camera_id: ctx.Queue(maxsize=slots_per_camera * 4) for spec in specs}

    face_q = ctx.Queue(maxsize=256) if camera_face_cfg else None

    ready_q, stats_q = ctx.Queue(), ctx.Queue()
    go_evt = ctx.Event()

    dec_procs = [ctx.Process(
        target=run_decoder_process, name=f"decode-{i}",
        args=(shard, ring_specs, free_qs, decode_qs, camera_decode_cfg),
        kwargs=dict(pose_imgsz=POSE_IMGSZ, pose_dtype_name=pose_blob_dtype,
                   ready_q=ready_q, go_evt=go_evt, stats_q=stats_q))
        for i, shard in enumerate(dec_shards)]

    infer_config = InferenceConfig(pose_model=pose_model, reid_model=reid_model,
                                   till_model=till_model, till_imgsz=till_imgsz,
                                   pose_num_workers=pose_num_workers,
                                   pose_max_batch=pose_max_batch)
    infer_procs = []
    for k in range(inferers):
        my_lanes = lane_shards[k * infer_threads:(k + 1) * infer_threads]
        my_lane_qs = [decode_qs[shard[0].camera_id] for shard in my_lanes]
        my_ring_specs = {spec.camera_id: ring_specs[spec.camera_id]
                        for shard in my_lanes for spec in shard}
        infer_procs.append(ctx.Process(
            target=run_inference_process, name=f"inference-{k}",
            args=(infer_config, my_lane_qs, business_qs, my_ring_specs,
                 ready_q, go_evt, stats_q, needs_till)))

    face_proc = None
    if camera_face_cfg:
        face_proc = ctx.Process(
            target=run_face_process, name="face",
            args=(face_model, arcface_model, gallery_db_path, camera_face_cfg, face_q,
                 business_qs, ready_q, go_evt, stats_q))

    biz_procs = []
    for i, shard in enumerate(biz_shards):
        my_ring_specs = {spec.camera_id: ring_specs[spec.camera_id] for spec in shard}
        my_free_qs = {spec.camera_id: free_qs[spec.camera_id] for spec in shard}
        my_business_qs = {spec.camera_id: business_qs[spec.camera_id] for spec in shard}
        biz_procs.append(ctx.Process(
            target=run_business_process, name=f"business-{i}",
            args=(shard, my_ring_specs, my_free_qs, my_business_qs, db_path, media_root,
                 gallery_db_path, face_q, ready_q, go_evt, stats_q)))

    all_procs = dec_procs + infer_procs + ([face_proc] if face_proc else []) + biz_procs
    for p in all_procs:
        p.start()

    # Every get() below goes through await_message, which gives up when a
    # tier process dies instead of waiting on a message it will never send.
    # A bare get() here is what turned an unreadable video, a mismatched
    # model dtype and a dropped face request into the same symptom: a run
    # that stops producing output and never returns.
    try:
        load_started = time.time()
        for _ in all_procs:
            await_message(ready_q, all_procs, what="tier ready", timeout_s=900.0)
        load_s = time.time() - load_started
        go_evt.set()

        # extend, not append. A decoder process reports one list of
        # DecoderStats (one per camera in its shard) and an inference process
        # one list of LaneStats (one per lane), exactly as a business process
        # reports a list of BusinessStats. Appending buried those lists one
        # level deep while business was flattened, so all_stats held a mix of
        # lists and objects -- and every consumer that filters it by type,
        # including the tier tool's own report, silently found no decoder and
        # no lane stats at all. That is how "decoders done: 0.0s" and a
        # missing per-model breakdown got past review: the data was collected
        # and thrown away at the last step.
        all_stats = []
        for _ in dec_procs:
            all_stats.extend(await_message(stats_q, all_procs, what="decoder stats"))
        for q in {id(q): q for q in decode_qs.values()}.values():
            q.put(None)

        for _ in infer_procs:
            all_stats.extend(await_message(stats_q, all_procs, what="inference stats"))

        # Business workers are told perception is over now, but they keep
        # running -- draining any FaceAnswer messages still due -- until every
        # request they sent has been answered. Only after all of them finish is
        # it safe to stop the face tier.
        for q in business_qs.values():
            q.put(None)

        # Each business process reports one list of BusinessStats, one entry per
        # camera in its shard -- flatten to a single list of BusinessStats, not
        # a list of lists, so callers get what the name promises.
        biz_stats = []
        for _ in biz_procs:
            biz_stats.extend(await_message(stats_q, all_procs, what="business stats"))
        all_stats.extend(biz_stats)

        if face_proc is not None:
            face_q.put(None)
            all_stats.append(await_message(stats_q, all_procs, what="face stats"))
    finally:
        # Reached on the failure path too, where some tiers are still blocked
        # on queues nobody will ever feed. Ask, then insist: a process left
        # alive here holds its ring mapping open and the owner's unlink then
        # cannot free the block.
        #
        # One shared deadline, not a per-process timeout. Sequential 30s
        # joins cost 30s x process count in the case that matters most --
        # failing before go_evt.set(), where every remaining tier is parked
        # on the barrier and none of them will ever exit on their own. Nine
        # tiers would take four and a half minutes to clean up after an
        # error that was already reported, which reads as the hang this was
        # supposed to remove.
        deadline = time.monotonic() + SHUTDOWN_GRACE_S
        for p in all_procs:
            p.join(timeout=max(0.0, deadline - time.monotonic()))
        for p in all_procs:
            if p.is_alive():
                p.terminate()
        for p in all_procs:
            p.join(timeout=5)

    return biz_stats, all_stats, load_s


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--manifest", default=str(ROOT / "backend/config/stores/local_dev_store_16cams.json"))
    p.add_argument("--db-path", default=str(ROOT / "backend/data/split_run.sqlite"))
    p.add_argument("--gallery-db", default=str(ROOT / "backend/data/local_dev.sqlite"))
    p.add_argument("--media-root", default=str(ROOT / "backend/data/split_alerts"))
    p.add_argument("--pose-model", default=DEFAULT_POSE_MODEL)
    p.add_argument("--reid-model", default=DEFAULT_REID_MODEL)
    p.add_argument("--till-model", default=DEFAULT_TILL_MODEL)
    p.add_argument("--till-imgsz", type=int, default=640)
    p.add_argument("--face-model", default=DEFAULT_FACE_MODEL)
    p.add_argument("--arcface-model", default=DEFAULT_ARCFACE_MODEL)
    p.add_argument("--cameras", type=int, default=16)
    p.add_argument("--decoders", type=int, default=4)
    p.add_argument("--inferers", type=int, default=2)
    p.add_argument("--infer-threads", type=int, default=2)
    p.add_argument("--business", type=int, default=2)
    p.add_argument("--slots-per-camera", type=int, default=8)
    args = p.parse_args()

    from backend.src.infra.local_config import camera_specs_from_manifest

    specs = camera_specs_from_manifest(args.manifest)[:args.cameras]
    print(f"cameras={len(specs)} decoders={args.decoders} "
         f"inferers={args.inferers}x{args.infer_threads} business={args.business}",
         file=sys.stderr)

    started_at = time.time()
    biz_stats, _all_stats, load_s = run(
        specs, decoders=args.decoders, inferers=args.inferers,
        infer_threads=args.infer_threads, businesses=args.business,
        pose_model=args.pose_model, reid_model=args.reid_model,
        till_model=args.till_model, till_imgsz=args.till_imgsz,
        face_model=args.face_model, arcface_model=args.arcface_model,
        db_path=args.db_path, media_root=args.media_root,
        gallery_db_path=args.gallery_db, slots_per_camera=args.slots_per_camera)

    frames = sum(s.frames_processed for s in biz_stats)
    alerts = sum(s.alerts_emitted for s in biz_stats)
    starts = [s.processing_started_at for s in biz_stats]
    ends = [s.processing_ended_at for s in biz_stats]
    serving = max(ends) - min(starts) if starts else 0.0

    print(f"\n=== split pipeline ===", file=sys.stderr)
    print(f"model load (before clock) : {load_s:7.1f} s", file=sys.stderr)
    print(f"frames                    : {frames}", file=sys.stderr)
    print(f"alerts                    : {alerts}", file=sys.stderr)
    print(f"serving                   : {serving:7.1f} s  -> {frames / serving:6.2f} FPS",
         file=sys.stderr)
    for s in _all_stats:
        print(f"  {s!r}", file=sys.stderr)

    import json

    results = [{
        "status": "completed", "camera_id": s.camera_id, "camera_type": s.camera_type,
        "frames_processed": s.frames_processed, "alerts_emitted": s.alerts_emitted,
        "events_emitted": s.events_emitted, "processing_started_at": s.processing_started_at,
        "processing_ended_at": s.processing_ended_at, "pipeline_metrics": s.pipeline_metrics,
    } for s in biz_stats]
    print(json.dumps(results, default=str))
    print(f"\nwall-clock incl. load : {time.time() - started_at:7.1f} s", file=sys.stderr)


if __name__ == "__main__":
    main()
