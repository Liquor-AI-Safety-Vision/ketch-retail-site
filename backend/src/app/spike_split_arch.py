"""Throwaway spike: does splitting decode from inference lift the ceiling?

Everything measured so far says the current shape is limited by the GIL and
not by the GPU. Moving pose preprocessing off the batch-runner thread cut
queue_wait 60% and detect_pose 22% and left throughput unchanged, which is
what a GIL ceiling looks like. The fix on the table is to put decoding and
business logic in processes that hold no CUDA context, so CPU can scale
without adding the context contention that costs 19ms at 2 contexts, 45ms at
4 and 153ms at 8.

An earlier version of this spike ran pose only and came back inconclusive.
It deserved to: stripping the pipeline to pose deleted the tracker and the
ReID that produce most of the GIL pressure, and it fed a single inference
process whose serialised 5.70ms per frame capped it at 175 FPS regardless of
how many decoders pushed at it. It measured the split against its own
ceiling instead of against the GIL.

This version fixes both. The workload is pose, ReID and tracking, which is
about 80% of the real pipeline's frame time, and identical in both modes.
The split runs two inference processes, which is what the architecture
document allows for, so the ceiling is the same two CUDA contexts the
current shape already pays for.

    current   W processes, each running its cameras on threads, each holding
              a CUDA context and doing everything. What ships today.

    split     three tiers. Decoders hold no CUDA context and do decode and
              pose preprocessing. Inference processes hold the only CUDA
              contexts and do pose and ReID. Business processes hold no CUDA
              context and do tracking. Frames cross tiers through shared
              memory: only a slot index goes on the queue, measured at
              0.98ms per frame against 39.52ms for pickling the array.

Cameras are pinned to one inference process and one business process, so
each camera's frames stay ordered and its tracker state stays in one place
without a re-sorting stage.

    python backend/src/app/spike_split_arch.py --mode current --workers 2
    python backend/src/app/spike_split_arch.py --mode split --decoders 4
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

IMGSZ = 640
CONF = 0.35
BLOB_SHAPE = (3, IMGSZ, IMGSZ)
BLOB_BYTES = int(np.prod(BLOB_SHAPE)) * 4          # float32
MAX_FRAME = (1080, 1920, 3)
FRAME_BYTES = int(np.prod(MAX_FRAME))              # uint8
SLOT_BYTES = BLOB_BYTES + FRAME_BYTES


# --------------------------------------------------------------------------
# stage bodies, shared by both modes so the work is identical
# --------------------------------------------------------------------------

def _build_pose(model_path: str):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    sess = ort.InferenceSession(
        model_path, sess_options=opts,
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    sess.run(None, {sess.get_inputs()[0].name: np.zeros((1, *BLOB_SHAPE), np.float32)})
    return sess, sess.get_inputs()[0].name


def _build_reid(model_path: str):
    import stream_track as S

    S.REID_MODEL = model_path
    S.ORT_PROVIDERS = None
    return S.Embedder()


def _preprocess(frame_bgr: np.ndarray):
    """Letterbox and normalise, exactly as the running pipeline does."""
    from backend.src.runtime.shared_inference import _letterbox

    lb, ratio, dx, dy = _letterbox(frame_bgr, IMGSZ)
    blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
    blob = np.ascontiguousarray(blob, np.float32) / np.float32(255.0)
    return blob, (frame_bgr.shape[:2], ratio, dx, dy)


def _decode_pose(out: np.ndarray, meta) -> tuple[np.ndarray, list]:
    """Raw pose output to boxes and keypoints, the real arithmetic.

    Kept in full because it is CPU work under the GIL, which is what this
    spike measures. Dropping it would flatter whichever mode is GIL-bound.
    """
    (h, w), ratio, dx, dy = meta
    selected = out[out[:, 4] >= CONF]
    if not len(selected):
        return np.empty((0, 5), np.float32), []
    boxes = selected[:, :4].astype(np.float32, copy=True)
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - dx) / ratio
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - dy) / ratio
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0.0, w - 1.0)
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0.0, h - 1.0)
    keep = ((boxes[:, 2] - boxes[:, 0]) >= 8.0) & ((boxes[:, 3] - boxes[:, 1]) >= 16.0)
    boxes = boxes[keep]
    scores = selected[keep, 4:5].astype(np.float32, copy=False)
    k = selected[keep, 6:].reshape(-1, 17, 3).astype(np.float32, copy=True)
    k[:, :, 0] = (k[:, :, 0] - dx) / ratio
    k[:, :, 1] = (k[:, :, 1] - dy) / ratio
    return np.concatenate([boxes, scores], axis=1), [row for row in k]


def _new_tracker(h: int, w: int, fps: float):
    from uae_tracker import Tracker

    return Tracker(img_h=h, img_w=w, fps=fps)


def _track(tracker, boxes, feats, kpts, frame_idx):
    from uae_tracker import Detection

    dets = [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i]) for i in range(len(boxes))]
    live, _removed = tracker.update(dets, frame_idx)
    return len(live), int(getattr(tracker, "_next_id", 0))


def _iter_camera(spec):
    from backend.src.runtime.virtual_video_stream import VirtualVideoStreamSource

    return VirtualVideoStreamSource(spec).iter_frames(pace_realtime=False)


# --------------------------------------------------------------------------
# mode: current
# --------------------------------------------------------------------------

def _current_worker(shard, pose_path, reid_path, ready_q, go_evt, out_q):
    """One process, one CUDA context, its cameras on threads, doing it all."""
    from backend.src.runtime.perf import cap_native_threads

    cap_native_threads(1)
    sess, inp = _build_pose(pose_path)
    # Embedder.__call__ writes into a shared self._pad_batch, so one instance
    # cannot be shared across camera threads without corrupting features. The
    # real runtime clones it per camera for exactly this reason. Built here,
    # before the clock, so the extra loads do not land in the measurement.
    embs = {spec.camera_id: _build_reid(reid_path) for spec in shard}

    def run_camera(spec):
        emb = embs[spec.camera_id]
        tracker = None
        frames = tracks = 0
        for frame in _iter_camera(spec):
            blob, meta = _preprocess(frame.image_bgr)
            raw = sess.run(None, {inp: blob[None, ...]})[0]
            if raw.ndim == 2:
                raw = raw[None, ...]
            boxes, kpts = _decode_pose(raw[0], meta)
            feats = emb(frame.image_bgr, boxes)
            if tracker is None:
                h, w = frame.image_bgr.shape[:2]
                tracker = _new_tracker(h, w, spec.target_fps)
            _live, tracks = _track(tracker, boxes, feats, kpts, frame.frame_idx)
            frames += 1
        return frames, tracks

    ready_q.put(1)
    go_evt.wait()
    started = time.time()
    with ThreadPoolExecutor(max_workers=len(shard)) as pool:
        totals = list(pool.map(run_camera, shard))
    out_q.put({"role": "worker", "frames": sum(f for f, _ in totals),
               "tracks": sum(t for _, t in totals),
               "started": started, "ended": time.time()})


# --------------------------------------------------------------------------
# mode: split, three tiers
# --------------------------------------------------------------------------

def _attach(names):
    from multiprocessing import shared_memory

    handles, blobs, frames = [], [], []
    for name in names:
        shm = shared_memory.SharedMemory(name=name)
        handles.append(shm)
        blobs.append(np.ndarray(BLOB_SHAPE, np.float32, buffer=shm.buf))
        frames.append(np.ndarray(MAX_FRAME, np.uint8, buffer=shm.buf, offset=BLOB_BYTES))
    return handles, blobs, frames


def _decode_worker(shard, slot_names, free_q, did, ready_q, go_evt, out_q):
    """No CUDA context. Decode and pose preprocessing only."""
    from backend.src.runtime.perf import cap_native_threads

    cap_native_threads(1)
    handles, blobs, frames = _attach(slot_names)

    def run_camera(spec, pose_q):
        n = 0
        for frame in _iter_camera(spec):
            blob, meta = _preprocess(frame.image_bgr)
            h, w = frame.image_bgr.shape[:2]
            slot = free_q.get()
            np.copyto(blobs[slot], blob)
            np.copyto(frames[slot][:h, :w], frame.image_bgr)
            pose_q.put((did, slot, spec.camera_id, frame.frame_idx, (h, w), meta))
            n += 1
        return n

    ready_q.put(1)
    go_evt.wait()
    started = time.time()
    with ThreadPoolExecutor(max_workers=len(shard)) as pool:
        total = sum(pool.map(lambda pair: run_camera(*pair), shard))
    out_q.put({"role": "decoder", "frames": total, "started": started, "ended": time.time()})
    for h in handles:
        h.close()


def _infer_worker(pose_path, reid_path, all_names, free_qs, my_pose_qs, track_qs,
                  n_decoders, ready_q, go_evt, out_q):
    """The processes holding the only CUDA contexts.

    Threaded, and that turns out to matter more than anything else here. A
    single-threaded inference process runs its own CPU work -- decoding pose
    output, cropping for ReID -- in series behind its GPU calls, so the GPU
    idles through all of it. With threads, one thread's CPU work overlaps
    another's GPU call, because both onnxruntime and OpenCV release the GIL.
    Measured single-threaded, the split lost to the current design outright.

    Each thread owns its own queue and its own embedder. The queue keeps each
    camera's frames in order, since a camera is pinned to one thread; the
    embedder is private because Embedder.__call__ writes into a shared
    self._pad_batch and would otherwise be raced.

    Blocks stay mapped for the life of the process: re-opening them per frame
    measured 0.35ms of pure waste.
    """
    from backend.src.runtime.perf import cap_native_threads

    cap_native_threads(1)
    sess, inp = _build_pose(pose_path)
    embs = [_build_reid(reid_path) for _ in my_pose_qs]

    maps = {}
    handles = []
    for did, names in enumerate(all_names):
        h, blobs, frames = _attach(names)
        handles.extend(h)
        maps[did] = (blobs, frames)

    stats = [None] * len(my_pose_qs)

    def serve(k):
        pose_q = my_pose_qs[k]
        emb = embs[k]
        done = 0
        finished = 0
        infer_s = 0.0
        while finished < n_decoders:
            item = pose_q.get()
            if item is None:
                finished += 1
                continue
            did, slot, cam_id, frame_idx, (h, w), meta = item
            blobs, frames = maps[did]
            t0 = time.perf_counter()
            raw = sess.run(None, {inp: blobs[slot][None, ...]})[0]
            if raw.ndim == 2:
                raw = raw[None, ...]
            boxes, kpts = _decode_pose(raw[0], meta)
            feats = emb(frames[slot][:h, :w], boxes)
            infer_s += time.perf_counter() - t0
            free_qs[did].put(slot)
            track_qs[cam_id].put((cam_id, frame_idx, h, w, boxes, feats, kpts))
            done += 1
        stats[k] = (done, infer_s)

    ready_q.put(1)
    go_evt.wait()
    started = time.time()
    with ThreadPoolExecutor(max_workers=len(my_pose_qs)) as pool:
        list(pool.map(serve, range(len(my_pose_qs))))
    done = sum(s[0] for s in stats if s)
    busy = sum(s[1] for s in stats if s)
    out_q.put({"role": "infer", "frames": done, "started": started,
               "ended": time.time(),
               "gpu_ms_per_frame": busy / max(1, done) * 1000,
               "threads": len(my_pose_qs)})
    for shm in handles:
        shm.close()


def _track_worker(cam_fps, track_q, n_producers, ready_q, go_evt, out_q):
    """No CUDA context. Tracking only, one camera's state never leaves here."""
    from backend.src.runtime.perf import cap_native_threads

    cap_native_threads(1)
    trackers = {}
    ready_q.put(1)
    go_evt.wait()
    started = time.time()
    frames = 0
    finished = 0
    tracks = 0
    while finished < n_producers:
        item = track_q.get()
        if item is None:
            finished += 1
            continue
        cam_id, frame_idx, h, w, boxes, feats, kpts = item
        tracker = trackers.get(cam_id)
        if tracker is None:
            tracker = trackers[cam_id] = _new_tracker(h, w, cam_fps)
        _track(tracker, boxes, feats, kpts, frame_idx)
        frames += 1
    tracks = sum(int(getattr(t, "_next_id", 0)) for t in trackers.values())
    out_q.put({"role": "track", "frames": frames, "tracks": tracks,
               "started": started, "ended": time.time()})


# --------------------------------------------------------------------------

def _shard(items, n):
    return [items[i::n] for i in range(n)]


def run_current(specs, pose_path, reid_path, workers):
    ctx = mp.get_context("spawn")
    ready_q, out_q = ctx.Queue(), ctx.Queue()
    go_evt = ctx.Event()
    shards = [s for s in _shard(specs, workers) if s]
    procs = [ctx.Process(target=_current_worker,
                         args=(s, pose_path, reid_path, ready_q, go_evt, out_q))
             for s in shards]
    return _orchestrate(ctx, procs, ready_q, go_evt, out_q, len(procs), [])


def run_split(specs, pose_path, reid_path, decoders, inferers, infer_threads, trackers, slots):
    from multiprocessing import shared_memory

    ctx = mp.get_context("spawn")
    ready_q, out_q = ctx.Queue(), ctx.Queue()
    go_evt = ctx.Event()

    dec_shards = [s for s in _shard(specs, decoders) if s]
    decoders = len(dec_shards)

    # Every camera is pinned to one inference *thread* and one tracker, so its
    # frames stay in order and its tracker state stays in one place. That is
    # what removes the need for a re-sorting stage: a queue read by a single
    # consumer preserves the order it was written in.
    lanes = inferers * infer_threads
    pose_qs = [ctx.Queue(maxsize=slots * decoders) for _ in range(lanes)]
    track_qs_by_idx = [ctx.Queue(maxsize=2048) for _ in range(trackers)]
    cam_pose_q = {}
    cam_track_q = {}
    for i, spec in enumerate(specs):
        cam_pose_q[spec.camera_id] = pose_qs[i % lanes]
        cam_track_q[spec.camera_id] = track_qs_by_idx[i % trackers]

    blocks, all_names, free_qs = [], [], []
    for _ in range(decoders):
        names, fq = [], ctx.Queue()
        for slot in range(slots):
            shm = shared_memory.SharedMemory(create=True, size=SLOT_BYTES)
            blocks.append(shm)
            names.append(shm.name)
            fq.put(slot)
        all_names.append(names)
        free_qs.append(fq)

    procs = []
    for did, shard in enumerate(dec_shards):
        pairs = [(spec, cam_pose_q[spec.camera_id]) for spec in shard]
        procs.append(ctx.Process(target=_decode_worker,
                                 args=(pairs, all_names[did], free_qs[did],
                                       did, ready_q, go_evt, out_q)))
    infer_procs = [
        ctx.Process(target=_infer_worker,
                    args=(pose_path, reid_path, all_names, free_qs,
                          pose_qs[k * infer_threads:(k + 1) * infer_threads],
                          cam_track_q, decoders, ready_q, go_evt, out_q))
        for k in range(inferers)
    ]
    fps = float(specs[0].target_fps or 5.0)
    track_procs = [
        ctx.Process(target=_track_worker,
                    args=(fps, track_qs_by_idx[k], lanes, ready_q, go_evt, out_q))
        for k in range(trackers)
    ]

    stages = [(procs, pose_qs, decoders), (infer_procs, track_qs_by_idx, lanes)]
    try:
        return _orchestrate(ctx, procs + infer_procs + track_procs, ready_q, go_evt,
                            out_q, len(procs) + len(infer_procs) + len(track_procs),
                            stages)
    finally:
        for shm in blocks:
            shm.close()
            shm.unlink()


def _orchestrate(ctx, procs, ready_q, go_evt, out_q, expected, stages):
    """Start everything, wait until all of it is loaded, then start the clock.

    The same discipline the runtime uses: model loading must not land inside
    the measured window, or the slowest loader sets the rate. Shutdown walks
    the tiers in order, so each one drains only after the tier feeding it has
    finished.
    """
    for p in procs:
        p.start()
    load_started = time.time()
    for _ in range(expected):
        ready_q.get()
    load_s = time.time() - load_started
    go_evt.set()

    results = []
    for stage_procs, downstream_qs, sentinels_each in stages:
        for _ in stage_procs:
            results.append(out_q.get())
        for q in downstream_qs:
            for _ in range(sentinels_each):
                q.put(None)
    while len(results) < expected:
        results.append(out_q.get())

    for p in procs:
        p.join(timeout=120)
        if p.is_alive():
            p.terminate()
    return results, load_s


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--mode", choices=["current", "split"], required=True)
    p.add_argument("--manifest", default=str(ROOT / "backend/config/stores/local_dev_store_16cams.json"))
    p.add_argument("--pose-model", default=str(ROOT / "models/yolo26m-pose.onnx"))
    p.add_argument("--reid-model", default=str(ROOT / "models/osnet_x0_25_msmt17.onnx"))
    p.add_argument("--cameras", type=int, default=16)
    p.add_argument("--workers", type=int, default=2, help="current: CUDA-holding processes")
    p.add_argument("--decoders", type=int, default=4, help="split: decode processes")
    p.add_argument("--inferers", type=int, default=2, help="split: CUDA-holding processes")
    p.add_argument("--infer-threads", type=int, default=2,
                   help="split: threads per inference process, so CPU overlaps GPU")
    p.add_argument("--trackers", type=int, default=2, help="split: business processes")
    p.add_argument("--slots", type=int, default=8, help="split: shared blocks per decoder")
    args = p.parse_args()

    from backend.src.infra.local_config import camera_specs_from_manifest

    specs = camera_specs_from_manifest(args.manifest)[:args.cameras]
    print(f"mode={args.mode} cameras={len(specs)}", file=sys.stderr)

    if args.mode == "current":
        results, load_s = run_current(specs, args.pose_model, args.reid_model, args.workers)
        contexts = args.workers
    else:
        results, load_s = run_split(specs, args.pose_model, args.reid_model,
                                    args.decoders, args.inferers, args.infer_threads,
                                    args.trackers, args.slots)
        contexts = args.inferers

    def total(role, key):
        return sum(r.get(key, 0) for r in results if r["role"] == role)

    role = "worker" if args.mode == "current" else "track"
    frames = total(role, "frames")
    tracks = total(role, "tracks")
    serving = max(r["ended"] for r in results) - min(r["started"] for r in results)

    print(f"\n=== {args.mode} ===", file=sys.stderr)
    print(f"cuda contexts             : {contexts}", file=sys.stderr)
    print(f"model load (before clock) : {load_s:7.1f} s", file=sys.stderr)
    print(f"frames                    : {frames}", file=sys.stderr)
    print(f"tracks created            : {tracks}", file=sys.stderr)
    print(f"serving                   : {serving:7.1f} s  -> {frames / serving:6.2f} FPS",
          file=sys.stderr)
    for r in results:
        if "gpu_ms_per_frame" in r:
            print(f"gpu per inference process : {r['gpu_ms_per_frame']:.2f} ms/frame "
                  f"over {r['frames']} frames", file=sys.stderr)


if __name__ == "__main__":
    main()
