"""Find which tier of the split pipeline is slow, by running one tier at a time.

The full four-tier run measured 14.80 FPS on 4 cameras with no error in the
log at all. A silent slowdown like that cannot be reasoned about from the
top, because every tier is a suspect and they all run concurrently. So this
builds the pipeline up one layer at a time, using the real tier code, and
reports the throughput of each layer:

    L1  decode              decoders -> (drained here)
    L2  decode + infer      decoders -> inference -> (drained here)
    L3  decode + infer + business    the full run

Each layer adds exactly one tier, so the drop between two layers is that
tier's cost and nothing else. Draining happens in this process: a drain
thread reads whatever the layer's last tier produces and immediately returns
the ring slot to the free list, standing in for the tier that would normally
consume it. That keeps backpressure realistic -- the producing tier still
blocks on a full ring exactly as it would in production -- while adding no
work of its own.

    python ai/tools/diag_split_tiers.py --layer decode --cameras 4 --duration 20
    python ai/tools/diag_split_tiers.py --layer all --cameras 4 --duration 20
"""
from __future__ import annotations

import argparse
import copy
import multiprocessing as mp
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Before numpy/cv2/onnxruntime load anywhere, including in the tier
# processes this spawns -- see perf.py.
from ai.src.runtime.perf import cap_native_threads  # noqa: E402
from ai.src.common.paths import ai_root, model_path
from ai.src.common.logging_setup import configure_logging

cap_native_threads(1)

MAX_FRAME_SHAPE = (1080, 1920, 3)


def _drain(q, free_qs, stop_flag, counter):
    """Stand in for the tier that would consume this layer's output.

    Returns the slot immediately. Nothing else -- the point is to measure the
    tiers above it without this one contributing any cost of its own.
    """
    while True:
        item = q.get()
        if item is None:
            return
        slot = getattr(item, "slot", None)
        cam = getattr(item, "camera_id", None)
        if slot is not None and cam in free_qs:
            free_qs[cam].put(slot)
        counter[0] += 1


def run_full(specs, *, decoders, inferers, infer_threads, businesses, models, paths,
             slots_per_camera, pose_num_workers=1, pose_max_batch=1,
             pose_blob_dtype="float32", reid_shared=False, reid_num_workers=1):
    """Layer 3 is the real driver, called directly -- not a copy of it.

    An earlier version of this file built the full stack itself, and drifted
    from the driver in three ways at once: it never started a face tier
    (passing face_q=None to business workers whose cameras had face identity
    enabled), it validated only the lane count and not the decoder or
    business counts, and it had its own copies of the blob shape and dtype.
    Every one of those makes the layer-3 number a measurement of something
    the driver does not do, which is the specific failure this whole tool
    exists to avoid.

    There is nothing to gain from a second implementation here. Layer 3 *is*
    the driver; layers 1 and 2 are the partial stacks the driver cannot be
    asked for.
    """
    from ai.src.app.run_split_pipeline import run

    started = time.time()
    biz_stats, all_stats, load_s = run(
        specs, decoders=decoders, inferers=inferers, infer_threads=infer_threads,
        businesses=businesses, pose_model=models["pose"], reid_model=models["reid"],
        till_model=models["till"], till_imgsz=640,
        face_model=models["face"], arcface_model=models["arcface"],
        db_path=paths["db"], media_root=paths["media"], gallery_db_path=paths["gallery"],
        slots_per_camera=slots_per_camera, pose_num_workers=pose_num_workers,
        pose_max_batch=pose_max_batch, pose_blob_dtype=pose_blob_dtype,
        reid_shared=reid_shared, reid_num_workers=reid_num_workers)
    wall = time.time() - started - load_s

    frames = sum(s.frames_processed for s in biz_stats)
    kind = type(all_stats[0]).__name__ if all_stats else ""
    dec_stats = [s for s in all_stats if type(s).__name__ == "DecoderStats"]
    return {
        "layer": "all", "load_s": load_s, "wall_s": wall,
        # Measured, not zero. It used to be hard-coded 0.0 because the driver
        # buried its DecoderStats in a nested list and this filter found none
        # -- printing "decoders done: 0.0 s" under a full run that plainly
        # had decoded 11520 frames.
        "decode_wall_s": (max(s.ended for s in dec_stats) - min(s.started for s in dec_stats)
                          if dec_stats else 0.0),
        "frames": frames, "fps": frames / wall if wall else 0.0,
        "dec_stats": dec_stats,
        "lane_stats": [s for s in all_stats if type(s).__name__ == "LaneStats"],
        "biz_stats": biz_stats, "_kind": kind,
    }


def run_layer(specs, layer: str, *, decoders: int, inferers: int, infer_threads: int,
              businesses: int, models: dict, paths: dict, slots_per_camera: int,
              pose_num_workers: int = 1, pose_max_batch: int = 1,
              pose_blob_dtype: str = "float32", reid_shared: bool = False,
              reid_num_workers: int = 1):
    if layer == "all":
        return run_full(specs, decoders=decoders, inferers=inferers,
                        infer_threads=infer_threads, businesses=businesses,
                        models=models, paths=paths, slots_per_camera=slots_per_camera,
                        pose_num_workers=pose_num_workers, pose_max_batch=pose_max_batch,
                        pose_blob_dtype=pose_blob_dtype, reid_shared=reid_shared,
                        reid_num_workers=reid_num_workers)

    from ai.src.app.run_split_pipeline import (
        POSE_BLOB_DTYPE, POSE_IMGSZ, _camera_decode_config, _shard, _validate_topology)
    from ai.src.runtime.decode_tier import run_decoder_process
    from ai.src.runtime.frame_ring import FrameRing
    from ai.src.runtime.inference_server import InferenceConfig, run_inference_process
    from ai.src.runtime.tier_status import await_message

    # The driver's own check, not a partial copy of it. Validating only the
    # lane count left decoders and businesses free to silently under-shard --
    # the same _shard behaviour that already corrupted one topology sweep.
    _validate_topology(len(specs), decoders=decoders, inferers=inferers,
                       infer_threads=infer_threads, businesses=businesses,
                       slots_per_camera=slots_per_camera)

    ctx = mp.get_context("spawn")
    camera_decode_cfg = {spec.camera_id: _camera_decode_config(spec) for spec in specs}

    rings, free_qs, ring_specs = {}, {}, {}

    # Allocation inside the try, same reason as the driver: a failure partway
    # through sixteen rings leaves the earlier ones mapped with no owner
    # alive to unlink them.
    try:
        for spec in specs:
            ring, free_q = FrameRing.create(
                ctx, slots=slots_per_camera, frame_shape=MAX_FRAME_SHAPE,
                blob_shape=(3, POSE_IMGSZ, POSE_IMGSZ), blob_dtype=POSE_BLOB_DTYPE)
            rings[spec.camera_id] = ring
            free_qs[spec.camera_id] = free_q
            ring_specs[spec.camera_id] = ring.spec

        return _run_partial(
            specs, layer, ctx=ctx, rings=rings, free_qs=free_qs, ring_specs=ring_specs,
            camera_decode_cfg=camera_decode_cfg, decoders=decoders, inferers=inferers,
            infer_threads=infer_threads, models=models, slots_per_camera=slots_per_camera,
            run_decoder_process=run_decoder_process, run_inference_process=run_inference_process,
            InferenceConfig=InferenceConfig, await_message=await_message, _shard=_shard,
            POSE_IMGSZ=POSE_IMGSZ, POSE_BLOB_DTYPE=POSE_BLOB_DTYPE)
    finally:
        # await_message now raises rather than hanging, which means the
        # failure path is reachable -- and without this, every one of those
        # failures leaves the ring's shared memory behind. Being able to fail
        # is only an improvement if failing also cleans up.
        for ring in rings.values():
            ring.close()


def _run_partial(specs, layer, *, ctx, rings, free_qs, ring_specs, camera_decode_cfg,
                 decoders, inferers, infer_threads, models, slots_per_camera,
                 run_decoder_process, run_inference_process, InferenceConfig,
                 await_message, _shard, POSE_IMGSZ, POSE_BLOB_DTYPE):
    needs_till = any(cfg.till_conf is not None for cfg in camera_decode_cfg.values())
    dec_shards = _shard(specs, decoders)
    lane_shards = _shard(specs, inferers * infer_threads)

    decode_qs = {}
    for shard in lane_shards:
        q = ctx.Queue(maxsize=slots_per_camera * len(shard) * 2)
        for spec in shard:
            decode_qs[spec.camera_id] = q
    business_qs = {spec.camera_id: ctx.Queue(maxsize=slots_per_camera * 4) for spec in specs}

    ready_q, stats_q = ctx.Queue(), ctx.Queue()
    go_evt = ctx.Event()

    procs = [ctx.Process(
        target=run_decoder_process,
        args=(shard, ring_specs, free_qs, decode_qs, camera_decode_cfg),
        kwargs=dict(pose_imgsz=POSE_IMGSZ, pose_dtype_name=POSE_BLOB_DTYPE,
                    ready_q=ready_q, go_evt=go_evt, stats_q=stats_q))
        for shard in dec_shards]
    n_dec = len(procs)

    infer_procs = []
    if layer == "infer":
        cfg = InferenceConfig(pose_model=models["pose"], reid_model=models["reid"],
                              till_model=models["till"], till_imgsz=640)
        for k in range(inferers):
            my_lanes = lane_shards[k * infer_threads:(k + 1) * infer_threads]
            if not my_lanes:
                continue
            my_lane_qs = [decode_qs[shard[0].camera_id] for shard in my_lanes]
            my_ring_specs = {s.camera_id: ring_specs[s.camera_id]
                             for shard in my_lanes for s in shard}
            infer_procs.append(ctx.Process(
                target=run_inference_process,
                args=(cfg, my_lane_qs, business_qs, my_ring_specs,
                      ready_q, go_evt, stats_q, needs_till)))
    procs += infer_procs

    # No business or face tier here by construction: layers 1 and 2 are the
    # partial stacks, and the full one is the driver itself (see run_full).
    # The queue this layer's last tier writes to is drained in-process, with
    # a thread that returns the slot and does nothing else.
    drained = ({id(q): q for q in decode_qs.values()} if layer == "decode"
               else {id(q): q for q in business_qs.values()})

    drain_threads: list = []
    # Started inside the try, so every path out of here -- including
    # await_message raising, which is the whole reason it exists -- goes
    # through the shutdown below. Previously a tier dying during load left
    # every other tier running, each still holding its ring mapping open, so
    # the owner's unlink freed nothing and the next run started against a
    # machine with the last run's processes still on it.
    try:
        for p in procs:
            p.start()
        load_started = time.time()
        for _ in procs:
            await_message(ready_q, procs, what="tier ready", timeout_s=900.0)
        load_s = time.time() - load_started

        counter = [0]
        drain_threads = [
            threading.Thread(target=_drain, args=(q, free_qs, None, counter), daemon=True)
            for q in drained.values()]
        for t in drain_threads:
            t.start()

        t0 = time.time()
        go_evt.set()

        dec_stats = []
        for _ in range(n_dec):
            # No deadline, as in the driver: this wait lasts as long as the
            # run, and --duration above ten minutes used to be killed by it.
            dec_stats.extend(await_message(stats_q, procs, what="decoder stats",
                                           timeout_s=None))
        decode_done = time.time()

        for q in {id(q): q for q in decode_qs.values()}.values():
            q.put(None)

        lane_stats = []
        for _ in infer_procs:
            lane_stats.extend(await_message(stats_q, procs, what="inference stats"))

        for q in drained.values():
            q.put(None)
        wall = time.time() - t0

        frames = sum(s.frames for s in dec_stats)
        return {
            "layer": layer, "load_s": load_s, "wall_s": wall,
            "decode_wall_s": decode_done - t0, "frames": frames,
            "fps": frames / wall if wall else 0.0,
            "dec_stats": dec_stats, "lane_stats": lane_stats, "biz_stats": [],
        }
    finally:
        for t in drain_threads:
            t.join(timeout=10)
        # One shared deadline, matching the driver: sequential per-process
        # timeouts multiply by process count on the path where every
        # remaining tier is parked on a queue nobody will feed.
        deadline = time.monotonic() + 30.0
        for p in procs:
            p.join(timeout=max(0.0, deadline - time.monotonic()))
        for p in procs:
            if p.is_alive():
                p.terminate()
        for p in procs:
            p.join(timeout=5)


def _report(r):
    print(f"\n=== layer: {r['layer']} ===", file=sys.stderr)
    print(f"  model load   : {r['load_s']:7.1f} s (before clock)", file=sys.stderr)
    print(f"  frames       : {r['frames']}", file=sys.stderr)
    print(f"  wall         : {r['wall_s']:7.1f} s -> {r['fps']:7.2f} FPS", file=sys.stderr)
    print(f"  decoders done: {r['decode_wall_s']:7.1f} s", file=sys.stderr)
    # Work vs blocked, summed over every decoder thread. A decode tier whose
    # time is mostly free_wait/put_wait is not slow, it is waiting on a
    # slower tier -- and reading its FPS as a decode ceiling would be wrong.
    if r["dec_stats"] and hasattr(r["dec_stats"][0], "grab_s"):
        agg = {k: sum(getattr(s, k) for s in r["dec_stats"])
               for k in ("grab_s", "retrieve_s", "frame_copy_s", "letterbox_s",
                         "preprocess_s", "free_wait_s", "put_wait_s")}
        work = sum(agg[k] for k in ("grab_s", "retrieve_s", "frame_copy_s",
                                    "letterbox_s", "preprocess_s"))
        blocked = agg["free_wait_s"] + agg["put_wait_s"]
        total = work + blocked
        print(f"  decode work  : {work:7.1f} s  "
              f"(grab={agg['grab_s']:6.1f} retrieve={agg['retrieve_s']:6.1f} "
              f"copy={agg['frame_copy_s']:6.1f} letterbox={agg['letterbox_s']:6.1f} "
              f"preproc={agg['preprocess_s']:6.1f})", file=sys.stderr)
        print(f"  decode block : {blocked:7.1f} s  "
              f"(free_slot={agg['free_wait_s']:6.1f} lane_q={agg['put_wait_s']:6.1f})"
              f"  -> {blocked / total * 100 if total else 0:.0f}% of decoder time is waiting",
              file=sys.stderr)
    for s in r["dec_stats"]:
        dur = s.ended - s.started
        print(f"    decode {s.camera_id:<16} {s.frames:5d} frames in {dur:6.1f}s "
              f"({s.frames / dur if dur else 0:6.2f} FPS)", file=sys.stderr)
    for s in r["lane_stats"]:
        # busy_s vs wall is the number that matters: a lane busy far less
        # than the wall clock is waiting on its queue, not on the GPU.
        # pose is split into queued vs computing: the shared pose runner has
        # one worker per process, so a lane's pose_s is mostly other lanes'
        # turns when several share a process.
        wait = getattr(s, "pose_queue_wait_s", 0.0)
        print(f"    lane {s.lane:<2} {s.frames:5d} frames busy={s.busy_s:7.1f}s "
              f"(pose={s.pose_s:6.1f} [wait={wait:6.1f} run={s.pose_s - wait:6.1f}] "
              f"reid={s.reid_s:6.1f} till={s.till_s:6.1f}) "
              f"idle={r['wall_s'] - s.busy_s:7.1f}s", file=sys.stderr)
    for s in r["biz_stats"]:
        dur = s.processing_ended_at - s.processing_started_at
        print(f"    biz {s.camera_id:<16} {s.frames_processed:5d} frames in {dur:6.1f}s "
              f"({s.frames_processed / dur if dur else 0:6.2f} FPS) "
              f"alerts={s.alerts_emitted}", file=sys.stderr)


def _roundrobin_by_type(specs: list) -> list:
    """Deal cameras out one type at a time, so every shard gets a mix.

    _shard() strides (items[i::n]) and the manifest lists cameras in blocks
    by type, so with 16 cameras on 16 lanes the first inference process
    receives lanes 0-7 -- which is every counter camera, and therefore all
    of the till work, while the second process runs none of it. That is not
    a bug (nothing idles and the FPS is real), but it is an unbalanced load
    on the tier that is already the bottleneck.

    Dealing round-robin by camera type puts two counters in each half
    instead of four in one, and splits the face-enabled cameras evenly too.
    Whether that is actually faster is a measurement, not an assumption --
    which is why this is a flag rather than the default.
    """
    by_type: dict[str, list] = {}
    for spec in specs:
        by_type.setdefault(spec.camera_id.rsplit("_", 1)[0], []).append(spec)
    ordered, groups = [], list(by_type.values())
    for i in range(max(len(g) for g in groups)):
        for group in groups:
            if i < len(group):
                ordered.append(group[i])
    return ordered


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--layer", default="all", choices=["decode", "infer", "all", "ladder"])
    p.add_argument("--manifest", default=str(ai_root() / "config/stores/local_dev_store_16cams.json"))
    p.add_argument("--cameras", type=int, default=4)
    p.add_argument("--camera-offset", type=int, default=0)
    p.add_argument("--duration", type=float, default=20.0,
                   help="trim each camera to this many simulated seconds")
    p.add_argument("--decoders", type=int, default=2)
    p.add_argument("--inferers", type=int, default=1)
    p.add_argument("--infer-threads", type=int, default=2)
    p.add_argument("--business", type=int, default=2)
    p.add_argument("--slots-per-camera", type=int, default=8)
    p.add_argument("--pose-model", default=None,
                   help="override the pose model; pair with --pose-blob-dtype, "
                        "since the decoder must write the dtype the model expects")
    p.add_argument("--pose-blob-dtype", default="float32", choices=["float32", "float16"])
    p.add_argument("--till-model", default=None,
                   help="override the till segmentation model; the decoder does not "
                        "preprocess for till (the lane crops from the ring itself), so "
                        "unlike --pose-model this needs no matching dtype flag")
    p.add_argument("--reid-shared", action="store_true",
                   help="route ReID through one worker instead of each lane "
                        "calling sess.run independently -- see SharedReidService")
    p.add_argument("--reid-workers", type=int, default=1,
                   help="worker threads inside SharedReidService, only with --reid-shared")
    p.add_argument("--pose-workers", type=int, default=1,
                   help="pose runner threads per inference process")
    p.add_argument("--pose-batch", type=int, default=1,
                   help="max frames the pose runner batches per session.run")
    p.add_argument("--camera-order", default="manifest", choices=["manifest", "roundrobin"],
                   help="roundrobin deals cameras out by type before sharding, so each "
                        "inference process gets a mix instead of whichever block the "
                        "manifest happened to list consecutively")
    args = p.parse_args()

    from ai.src.infra.local_config import camera_specs_from_manifest

    specs = camera_specs_from_manifest(args.manifest)
    specs = specs[args.camera_offset:args.camera_offset + args.cameras]
    specs = [copy.deepcopy(s) for s in specs]
    for s in specs:
        s.target_duration_s = args.duration
    if args.camera_order == "roundrobin":
        specs = _roundrobin_by_type(specs)

    # Face models included because layer 3 runs the real driver, which starts
    # a face tier for any camera whose manifest enables face identity -- and
    # in the 16-camera manifest, eight of them do.
    models = {"pose": args.pose_model or model_path("yolo26m-pose.onnx"),
              "reid": model_path("osnet_x0_25_msmt17.onnx"),
              "till": args.till_model or model_path("till_seg_v4_640.onnx"),
              "face": model_path("yolov6s_face.onnx"),
              "arcface": model_path("ms1mv3_arcface_r18.onnx")}
    paths = {"db": str(ai_root() / "data/diag_split.sqlite"),
             "media": str(ai_root() / "data/diag_split_alerts"),
             "gallery": str(ai_root() / "data/local_dev.sqlite")}

    layers = ["decode", "infer", "all"] if args.layer == "ladder" else [args.layer]
    print(f"cameras={[s.camera_id for s in specs]} duration={args.duration}s", file=sys.stderr)
    for layer in layers:
        r = run_layer(specs, layer, pose_num_workers=args.pose_workers,
                      pose_blob_dtype=args.pose_blob_dtype, reid_shared=args.reid_shared,
                      reid_num_workers=args.reid_workers,
                      pose_max_batch=args.pose_batch,
                      decoders=args.decoders, inferers=args.inferers,
                      infer_threads=args.infer_threads, businesses=args.business,
                      models=models, paths=paths, slots_per_camera=args.slots_per_camera)
        _report(r)


if __name__ == "__main__":
    main()
