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

A run whose cameras use strategy=forever has no stream that ends, so it is
ended from outside: the first SIGINT (Ctrl+C) or SIGTERM sets stop_evt, each
decoder returns after its current frame, and the same ordered shutdown above
runs from there -- alerts still in the outbox are flushed, not lost. A second
signal stops waiting and tears the tiers down.

    python ai/src/app/run_split_pipeline.py --cameras 16 \\
        --decoders 4 --inferers 2 --infer-threads 2 --business 2

--manifest takes one or more paths, one per store: the tiers shard a flat
list of CameraStreamSpec and never look at which store a spec came from, so
running several stores is just concatenating their manifests --

    python ai/src/app/run_split_pipeline.py \\
        --manifest config/stores/store_a.json config/stores/store_b.json

camera_specs_from_manifests refuses the merge if any camera_id repeats across
the given manifests, since rings and queues are keyed by it.
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Before anything imports numpy, cv2 or onnxruntime -- see perf.py. Every
# tier's process is spawned from here and inherits this environment at
# interpreter startup, which is the only moment the BLAS and OpenMP pools
# read it. Capping inside each process entry point instead was a no-op that
# cost 5x on every inference call.
from ai.src.runtime.perf import cap_native_threads  # noqa: E402
from ai.src.common.paths import ai_root, model_path
from ai.src.pipelines.registry import (
    COUNTER_CASH_THEFT, ENTRANCE_FACE_CAPTURE, FACE_TARGET_IDS, INTRUSION_IDS)
from ai.src.common.logging_setup import configure_logging

cap_native_threads(1)

# Height 1440, not 1080: the entrance camera is 1280x1440. Every slot is sized
# for the largest frame any camera may send; the check that refuses a frame
# larger than this is in decode_tier._run_camera.
MAX_FRAME_SHAPE = (1440, 1920, 3)

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
DEFAULT_POSE_MODEL = model_path("yolo26m-pose.onnx")
DEFAULT_REID_MODEL = model_path("osnet_x0_25_msmt17.onnx")
DEFAULT_TILL_MODEL = model_path("till_seg_v4_640.onnx")
DEFAULT_FACE_MODEL = model_path("yolov6s_face.onnx")
DEFAULT_ARCFACE_MODEL = model_path("ms1mv3_arcface_r18.onnx")


def _shard(items, n):
    return [items[i::n] for i in range(n) if items[i::n]]


def _default_conf_for(spec) -> float:
    """Each pipeline class falls back to a different conf_thres when the
    manifest omits one -- 0.45 for storage_intrusion, 0.35 otherwise. The
    decoder decides conf before any pipeline object exists, so it has to
    know the same defaults rather than picking one arbitrarily."""
    for binding in spec.pipeline_bindings:
        if binding.pipeline_id in INTRUSION_IDS or binding.pipeline_id == ENTRANCE_FACE_CAPTURE:
            return 0.45
    return 0.35


def _camera_decode_config(spec):
    from ai.src.runtime.decode_tier import CameraDecodeConfig

    conf = float(spec.perception_profile.get("person_detector", {}).get(
        "conf_thres", _default_conf_for(spec)))
    for binding in spec.pipeline_bindings:
        if binding.pipeline_id != COUNTER_CASH_THEFT or not binding.enabled:
            continue
        from ai.src.pipelines.till_schedule import compute_till_schedule

        params = binding.params
        schedule = compute_till_schedule(
            spec.target_fps, int(params.get("till_confirm_frames", 5)), params.get("till_fps"))
        return CameraDecodeConfig(
            conf=conf, till_conf=float(params.get("till_conf", 0.70)), till_every_n=schedule.every_n)
    return CameraDecodeConfig(conf=conf)


def _camera_face_config(spec):
    from ai.src.runtime.inference_server import FaceCameraConfig

    for binding in spec.pipeline_bindings:
        if binding.pipeline_id not in FACE_TARGET_IDS:
            continue
        face_cfg = spec.perception_profile.get("face_identity", {})
        # Entrance has no reason to exist without faces, so it does not wait
        # for face_identity.enabled; the intrusion pipelines still do.
        if not face_cfg.get("enabled", False) and binding.pipeline_id != ENTRANCE_FACE_CAPTURE:
            return None
        return FaceCameraConfig(
            face_conf=float(face_cfg.get("face_conf", 0.20)),
            crop_expand=float(face_cfg.get("crop_expand", 0.12)),
            iou_thres=float(face_cfg.get("iou_thres", 0.45)),
            match_threshold=float(face_cfg.get("match_threshold", 0.30)),
            match_margin=float(face_cfg.get("match_margin", 0.10)),
            # Storage doors ask "is this staff?", the entrance asks "is this a
            # known shoplifter?" -- never both, see SharedFaceIdentifier.
            match_is_staff=binding.pipeline_id != ENTRANCE_FACE_CAPTURE,
        )
    return None


def run(specs, *, decoders: int, inferers: int, infer_threads: int, businesses: int,
       pose_model: str, reid_model: str, till_model: str, till_imgsz: int,
       face_model: str, arcface_model: str, db_path: str, media_root: str,
       gallery_db_path: str, slots_per_camera: int,
       pose_num_workers: int = 1, pose_max_batch: int = 1,
       pose_blob_dtype: str = POSE_BLOB_DTYPE, reid_shared: bool = False,
       reid_num_workers: int = 1, storage_mode: str = "local-db", stop_evt=None):
    from ai.src.runtime.business_tier import run_business_process
    from ai.src.runtime.decode_tier import run_decoder_process
    from ai.src.runtime.frame_ring import FrameRing
    from ai.src.runtime.inference_server import InferenceConfig, run_face_process, run_inference_process

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

        # After rings, before any tier process exists -- the earliest point
        # every spec's fields (store_id, display_name, ...) are guaranteed
        # available and the latest point that still runs before a business
        # worker's start_run() could hit an unpopulated stores/cameras
        # table. A ring allocation failure above never reaches this line, so
        # it never depends on a spec having more than what ring allocation
        # itself touches.
        from ai.src.infra.sqlite_alert_store import seed_store_topology

        # gallery_db_path is untouched by storage_mode -- face identity stays
        # on its local SQLite gallery regardless -- but it still defaults to
        # db_path itself (see main()), so this seed always runs.
        seed_store_topology(db_path, specs)
        if storage_mode == "cloud-db":
            import os

            from ai.src.infra.postgres_alert_store import seed_store_topology_pg

            seed_store_topology_pg(os.environ["DATABASE_URL"], specs)

        return _run(specs, ctx=ctx, rings=rings, free_qs=free_qs, ring_specs=ring_specs,
                    camera_decode_cfg=camera_decode_cfg, camera_face_cfg=camera_face_cfg,
                    needs_till=needs_till, decoders=decoders, inferers=inferers,
                    infer_threads=infer_threads, businesses=businesses,
                    pose_model=pose_model, reid_model=reid_model, till_model=till_model,
                    till_imgsz=till_imgsz, face_model=face_model, arcface_model=arcface_model,
                    db_path=db_path, media_root=media_root, gallery_db_path=gallery_db_path,
                    slots_per_camera=slots_per_camera, pose_num_workers=pose_num_workers,
                    pose_max_batch=pose_max_batch, pose_blob_dtype=pose_blob_dtype,
                    reid_shared=reid_shared, reid_num_workers=reid_num_workers,
                    storage_mode=storage_mode, stop_evt=stop_evt)
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
         pose_blob_dtype=POSE_BLOB_DTYPE, reid_shared: bool = False,
         reid_num_workers: int = 1, storage_mode: str = "local-db", stop_evt=None):
    """Start the tiers, run them, shut them down in the order frames flow.

    Split out from run() so the rings run() owns are closed in its finally
    no matter how this returns.
    """
    from ai.src.runtime.business_tier import run_business_process
    from ai.src.runtime.decode_tier import run_decoder_process
    from ai.src.runtime.inference_server import (
        InferenceConfig, run_face_process, run_inference_process)
    from ai.src.runtime.tier_status import await_message

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
                   ready_q=ready_q, go_evt=go_evt, stats_q=stats_q, stop_evt=stop_evt))
        for i, shard in enumerate(dec_shards)]

    infer_config = InferenceConfig(pose_model=pose_model, reid_model=reid_model,
                                   till_model=till_model, till_imgsz=till_imgsz,
                                   pose_num_workers=pose_num_workers,
                                   pose_max_batch=pose_max_batch, reid_shared=reid_shared,
                                   reid_num_workers=reid_num_workers)
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
                 gallery_db_path, face_q, ready_q, go_evt, stats_q, storage_mode)))

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
            # No deadline: decoders report when their streams end, so this
            # wait is the run itself -- minutes for a benchmark, indefinitely
            # for strategy=forever. The 600s default here used to kill any run
            # longer than ten minutes. A dead tier still raises immediately.
            all_stats.extend(await_message(stats_q, all_procs, what="decoder stats",
                                           timeout_s=None))
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


def _stop_on_signal(stop_evt) -> None:
    """First SIGINT/SIGTERM: stop cleanly. Second: stop waiting.

    SIGTERM matters as much as Ctrl+C: it is what a process manager, `kill`
    or run_forever.sh sends, and without a handler it kills the parent on the
    spot -- no drain, no flush, tiers left to the parent-watchdog.
    """
    import signal

    def handle(signum, _frame):
        if stop_evt.is_set():
            print("second stop signal: tearing the tiers down without draining",
                  file=sys.stderr)
            signal.signal(signal.SIGINT, signal.default_int_handler)
            raise KeyboardInterrupt
        print(f"{signal.Signals(signum).name}: stopping after the current frame and "
              f"flushing alerts; send it again to stop immediately", file=sys.stderr)
        stop_evt.set()

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)


def _load_dotenv(path: Path) -> None:
    """No python-dotenv dependency for one file with no comments/quoting to
    speak of -- os.environ wins on conflict, so an operator's real shell
    export still overrides whatever is checked into .env."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--manifest", nargs="+",
                   default=[str(ai_root() / "config/stores/local_dev_store_16cams.json")],
                   help="one manifest per store; every camera_id across all of them "
                        "must be unique, since rings and queues are keyed by it")
    p.add_argument("--db-path", default=str(ai_root() / "data/split_run.sqlite"))
    # Defaults to db_path itself, not a second file: persons/face_embeddings
    # now live in the same schema as stores/cameras/alerts (see
    # local_dev_schema.sql), and persons.store_id is a real foreign key only
    # when both actually share one file. Pass --gallery-db explicitly to
    # keep them apart -- e.g. a gallery seeded once and reused across many
    # disposable benchmark databases -- but that gives up the FK.
    p.add_argument("--gallery-db", default=None)
    p.add_argument("--media-root", default=str(ai_root() / "data/split_alerts"))
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
    p.add_argument(
        "--storage-mode", choices=["local-db", "cloud-db"], default="local-db",
        help="local-db: SQLite (--db-path) + video files under --media-root, today's "
             "default. cloud-db: NeonDB (Postgres) + Cloudflare R2, read from "
             "DATABASE_URL/R2_* in .env.")
    args = p.parse_args()

    if args.storage_mode == "cloud-db":
        _load_dotenv(ROOT / ".env")
        missing = [k for k in ("DATABASE_URL", "R2_ENDPOINT", "R2_BUCKET",
                               "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
                  if not os.environ.get(k)]
        if missing:
            raise RuntimeError(
                f"--storage-mode cloud-db needs {missing} in the environment or in "
                f"{ROOT / '.env'}; none of these are hardcoded anywhere on purpose")

    if args.gallery_db is None:
        # cloud-db keeps staff in Neon beside the stores they belong to, rather
        # than in a SQLite file that lives and dies with one GPU box.
        args.gallery_db = (os.environ["DATABASE_URL"] if args.storage_mode == "cloud-db"
                           else args.db_path)

    from ai.src.infra.local_config import camera_specs_from_manifests

    specs = camera_specs_from_manifests(args.manifest)[:args.cameras]
    stores = sorted({spec.store_id for spec in specs})
    print(f"stores={stores} cameras={len(specs)} decoders={args.decoders} "
         f"inferers={args.inferers}x{args.infer_threads} business={args.business}",
         file=sys.stderr)

    stop_evt = mp.get_context("spawn").Event()
    _stop_on_signal(stop_evt)

    started_at = time.time()
    biz_stats, _all_stats, load_s = run(
        specs, decoders=args.decoders, inferers=args.inferers,
        infer_threads=args.infer_threads, businesses=args.business,
        pose_model=args.pose_model, reid_model=args.reid_model,
        till_model=args.till_model, till_imgsz=args.till_imgsz,
        face_model=args.face_model, arcface_model=args.arcface_model,
        db_path=args.db_path, media_root=args.media_root,
        gallery_db_path=args.gallery_db, slots_per_camera=args.slots_per_camera,
        storage_mode=args.storage_mode, stop_evt=stop_evt)

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

    # Per camera, measured from its first processed frame to its last: did it
    # keep its target rate, and did each frame finish inside one frame interval?
    from ai.src.runtime.decode_tier import DecoderStats

    dec_by_cam = {d.camera_id: d for d in _all_stats if isinstance(d, DecoderStats)}
    print(f"\n{'camera':24} {'target':>6} {'fps':>6} {'p50':>6} {'p95':>6} {'p99':>6} "
          f"{'max':>6} {'budget':>7} {'late':>9} {'maxgap':>7} {'grab/src':>9}", file=sys.stderr)
    for s in sorted(biz_stats, key=lambda b: b.camera_id):
        lat = s.latency_ms or {}
        dec = dec_by_cam.get(s.camera_id)
        grab = (f"{dec.grab_s / dec.source_frames * 1000:7.1f}ms"
                if dec and dec.source_frames else "      -")
        print(f"{s.camera_id:24} {s.target_fps:6.1f} {lat.get('fps', 0):6.2f} "
              f"{lat.get('p50', 0):5.0f}ms{lat.get('p95', 0):5.0f}ms{lat.get('p99', 0):5.0f}ms"
              f"{lat.get('max', 0):5.0f}ms {lat.get('interval_ms', 0):5.0f}ms "
              f"{lat.get('late', 0):4d}/{lat.get('frames', 0):<4d} {lat.get('max_gap_ms', 0):5.0f}ms "
              f"{grab}", file=sys.stderr)

    import json

    results = [{
        "status": "completed", "camera_id": s.camera_id, "camera_type": s.camera_type,
        "frames_processed": s.frames_processed, "alerts_emitted": s.alerts_emitted,
        "events_emitted": s.events_emitted, "processing_started_at": s.processing_started_at,
        "processing_ended_at": s.processing_ended_at, "pipeline_metrics": s.pipeline_metrics,
        "target_fps": s.target_fps, "latency_ms": s.latency_ms,
    } for s in biz_stats]
    print(json.dumps(results, default=str))
    print(f"\nwall-clock incl. load : {time.time() - started_at:7.1f} s", file=sys.stderr)


if __name__ == "__main__":
    main()
