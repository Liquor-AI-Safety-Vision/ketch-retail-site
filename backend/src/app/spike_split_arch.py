"""Throwaway spike: does splitting decode from inference lift the ceiling?

Everything measured so far says the current shape is limited by the GIL and
not by the GPU. Moving pose preprocessing off the batch-runner thread cut
queue_wait 60% and detect_pose 22% and left throughput unchanged, which is
what a GIL ceiling looks like. The proposed fix is to put decoding in
processes that hold no CUDA context, so CPU can scale without adding the
context contention that costs 19ms at 2 contexts, 45ms at 4 and 153ms at 8.

This spike exists to price that fix before committing to the rewrite. It is
deliberately not the product: no tracker, no ReID, no till, no faces, no
alerts, no database. Decode, pose, count. Both modes are stripped the same
way, so the only thing that differs between them is the process topology.

    current   W processes, each running its cameras on threads, each holding
              its own CUDA context. What ships today, minus business logic.

    split     D decode processes holding no CUDA context, doing decode and
              preprocessing, handing blobs to one inference process through
              shared memory. Only a slot index crosses the queue; measured
              at 0.98ms per frame when the producer writes in place, against
              39.52ms for pickling an array.

Run both at 16 cameras and compare. A win here justifies the rewrite; an
overlap with the noise band does not.

    python backend/src/app/spike_split_arch.py --mode current
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
BLOB_BYTES = int(np.prod(BLOB_SHAPE)) * 4  # float32


# --------------------------------------------------------------------------
# shared pieces
# --------------------------------------------------------------------------

def _load_specs(manifest: str, limit: int | None):
    from backend.src.infra.local_config import camera_specs_from_manifest

    specs = camera_specs_from_manifest(manifest)
    return specs[:limit] if limit else specs


def _build_session(model_path: str):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    sess = ort.InferenceSession(
        model_path,
        sess_options=opts,
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    return sess, sess.get_inputs()[0].name


def _preprocess(frame_bgr: np.ndarray) -> tuple[np.ndarray, tuple, float, int, int]:
    """Letterbox and normalise, exactly as the running pipeline does."""
    from backend.src.runtime.shared_inference import _letterbox

    lb, ratio, dx, dy = _letterbox(frame_bgr, IMGSZ)
    blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
    blob = np.ascontiguousarray(blob, np.float32) / np.float32(255.0)
    return blob, frame_bgr.shape[:2], ratio, dx, dy


def _decode_output(out: np.ndarray, meta: tuple) -> int:
    """Turn raw pose output into a detection count, doing the real work.

    The box arithmetic is kept because it is CPU work under the GIL, which is
    precisely what this spike is measuring. Dropping it would flatter the
    mode that is GIL-bound.
    """
    (h, w), ratio, dx, dy = meta
    selected = out[out[:, 4] >= CONF]
    if not len(selected):
        return 0
    boxes = selected[:, :4].astype(np.float32, copy=True)
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - dx) / ratio
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - dy) / ratio
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0.0, w - 1.0)
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0.0, h - 1.0)
    keep = ((boxes[:, 2] - boxes[:, 0]) >= 8.0) & ((boxes[:, 3] - boxes[:, 1]) >= 16.0)
    kept = selected[keep]
    if len(kept):
        k = kept[:, 6:].reshape(-1, 17, 3).astype(np.float32, copy=True)
        k[:, :, 0] = (k[:, :, 0] - dx) / ratio
        k[:, :, 1] = (k[:, :, 1] - dy) / ratio
    return int(keep.sum())


def _iter_camera(spec):
    from backend.src.runtime.virtual_video_stream import VirtualVideoStreamSource

    return VirtualVideoStreamSource(spec).iter_frames(pace_realtime=False)


# --------------------------------------------------------------------------
# mode: current
# --------------------------------------------------------------------------

def _current_worker(shard, model_path, ready_q, go_evt, out_q):
    """One process, its own CUDA context, its cameras on threads."""
    from backend.src.runtime.perf import cap_native_threads

    cap_native_threads(1)
    sess, inp = _build_session(model_path)
    lock = __import__("threading").Lock()

    warm = np.zeros((1, *BLOB_SHAPE), np.float32)
    sess.run(None, {inp: warm})

    def run_camera(spec):
        frames = 0
        dets = 0
        for frame in _iter_camera(spec):
            blob, shape, ratio, dx, dy = _preprocess(frame.image_bgr)
            with lock:
                raw = sess.run(None, {inp: blob[None, ...]})[0]
            if raw.ndim == 2:
                raw = raw[None, ...]
            dets += _decode_output(raw[0], (shape, ratio, dx, dy))
            frames += 1
        return frames, dets

    ready_q.put(1)
    go_evt.wait()
    started = time.time()
    with ThreadPoolExecutor(max_workers=len(shard)) as pool:
        totals = list(pool.map(run_camera, shard))
    out_q.put({
        "frames": sum(f for f, _ in totals),
        "detections": sum(d for _, d in totals),
        "started": started,
        "ended": time.time(),
    })


# --------------------------------------------------------------------------
# mode: split
# --------------------------------------------------------------------------

def _decoder_worker(shard, slot_names, free_q, work_q, decoder_id, ready_q, go_evt, out_q):
    """No CUDA context here. Decode and preprocess only, then hand off."""
    from multiprocessing import shared_memory

    from backend.src.runtime.perf import cap_native_threads

    cap_native_threads(1)
    handles = [shared_memory.SharedMemory(name=n) for n in slot_names]
    views = [np.ndarray(BLOB_SHAPE, np.float32, buffer=h.buf) for h in handles]

    def run_camera(spec):
        frames = 0
        for frame in _iter_camera(spec):
            blob, shape, ratio, dx, dy = _preprocess(frame.image_bgr)
            slot = free_q.get()
            np.copyto(views[slot], blob)
            work_q.put((decoder_id, slot, shape, ratio, dx, dy))
            frames += 1
        return frames

    ready_q.put(1)
    go_evt.wait()
    started = time.time()
    with ThreadPoolExecutor(max_workers=len(shard)) as pool:
        frames = sum(pool.map(run_camera, shard))
    out_q.put({"decoder": decoder_id, "frames": frames,
               "started": started, "ended": time.time()})
    for h in handles:
        h.close()


def _inference_worker(model_path, all_slot_names, free_qs, work_q, n_decoders,
                      ready_q, go_evt, out_q):
    """The only process holding a CUDA context.

    Blocks stay mapped for the life of the process: re-opening them per frame
    measured 0.35ms of pure waste.
    """
    from multiprocessing import shared_memory

    from backend.src.runtime.perf import cap_native_threads

    cap_native_threads(1)
    sess, inp = _build_session(model_path)
    warm = np.zeros((1, *BLOB_SHAPE), np.float32)
    sess.run(None, {inp: warm})

    handles = {}
    views = {}
    for did, names in enumerate(all_slot_names):
        for slot, name in enumerate(names):
            h = shared_memory.SharedMemory(name=name)
            handles[(did, slot)] = h
            views[(did, slot)] = np.ndarray(BLOB_SHAPE, np.float32, buffer=h.buf)

    ready_q.put(1)
    go_evt.wait()
    started = time.time()
    frames = 0
    dets = 0
    finished = 0
    infer_s = 0.0
    while finished < n_decoders:
        item = work_q.get()
        if item is None:
            finished += 1
            continue
        did, slot, shape, ratio, dx, dy = item
        blob = views[(did, slot)]
        t0 = time.perf_counter()
        raw = sess.run(None, {inp: blob[None, ...]})[0]
        infer_s += time.perf_counter() - t0
        free_qs[did].put(slot)
        if raw.ndim == 2:
            raw = raw[None, ...]
        dets += _decode_output(raw[0], (shape, ratio, dx, dy))
        frames += 1
    out_q.put({"frames": frames, "detections": dets, "started": started,
               "ended": time.time(), "infer_ms_per_frame": infer_s / max(1, frames) * 1000})
    for h in handles.values():
        h.close()


# --------------------------------------------------------------------------

def _shard(items, n):
    return [items[i::n] for i in range(n)]


def run_current(specs, model_path, workers):
    ctx = mp.get_context("spawn")
    ready_q, out_q = ctx.Queue(), ctx.Queue()
    go_evt = ctx.Event()
    shards = [s for s in _shard(specs, workers) if s]
    procs = [ctx.Process(target=_current_worker,
                         args=(s, model_path, ready_q, go_evt, out_q))
             for s in shards]
    return _orchestrate(procs, ready_q, go_evt, out_q, len(procs))


def run_split(specs, model_path, decoders, slots_per_decoder):
    from multiprocessing import shared_memory

    ctx = mp.get_context("spawn")
    ready_q, out_q = ctx.Queue(), ctx.Queue()
    go_evt = ctx.Event()
    work_q = ctx.Queue(maxsize=decoders * slots_per_decoder)

    shards = [s for s in _shard(specs, decoders) if s]
    decoders = len(shards)

    blocks = []
    all_names = []
    free_qs = []
    for _ in range(decoders):
        names = []
        fq = ctx.Queue()
        for slot in range(slots_per_decoder):
            shm = shared_memory.SharedMemory(create=True, size=BLOB_BYTES)
            blocks.append(shm)
            names.append(shm.name)
            fq.put(slot)
        all_names.append(names)
        free_qs.append(fq)

    procs = []
    for did, shard in enumerate(shards):
        procs.append(ctx.Process(
            target=_decoder_worker,
            args=(shard, all_names[did], free_qs[did], work_q, did, ready_q, go_evt, out_q)))
    infer = ctx.Process(
        target=_inference_worker,
        args=(model_path, all_names, free_qs, work_q, decoders, ready_q, go_evt, out_q))

    try:
        results = _orchestrate(procs + [infer], ready_q, go_evt, out_q,
                               len(procs) + 1, on_decoders_done=(work_q, decoders, procs))
    finally:
        for shm in blocks:
            shm.close()
            shm.unlink()
    return results


def _orchestrate(procs, ready_q, go_evt, out_q, expected, on_decoders_done=None):
    """Start everything, wait until all of it is loaded, then start the clock.

    The same discipline the runtime uses: model loading must not land inside
    the measured window, or the slowest loader sets the rate.
    """
    for p in procs:
        p.start()
    load_started = time.time()
    for _ in range(expected):
        ready_q.get()
    load_s = time.time() - load_started
    go_evt.set()

    results = []
    if on_decoders_done is not None:
        work_q, n_decoders, decoder_procs = on_decoders_done
        for _ in range(n_decoders):
            results.append(out_q.get())      # each decoder reports when done
        for _ in range(n_decoders):
            work_q.put(None)                 # then the inference side can drain
        results.append(out_q.get())
    else:
        for _ in range(expected):
            results.append(out_q.get())

    for p in procs:
        p.join(timeout=60)
        if p.is_alive():
            p.terminate()
    return results, load_s


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--mode", choices=["current", "split"], required=True)
    p.add_argument("--manifest", default=str(ROOT / "backend/config/stores/local_dev_store_16cams.json"))
    p.add_argument("--model", default=str(ROOT / "models/yolo26m-pose.onnx"))
    p.add_argument("--cameras", type=int, default=16)
    p.add_argument("--workers", type=int, default=2, help="current mode: CUDA-holding processes")
    p.add_argument("--decoders", type=int, default=4, help="split mode: decode processes")
    p.add_argument("--slots", type=int, default=8, help="split mode: shared blocks per decoder")
    args = p.parse_args()

    specs = _load_specs(args.manifest, args.cameras)
    print(f"mode={args.mode}  cameras={len(specs)}", file=sys.stderr)

    if args.mode == "current":
        results, load_s = run_current(specs, args.model, args.workers)
    else:
        results, load_s = run_split(specs, args.model, args.decoders, args.slots)

    frames = sum(r.get("frames", 0) for r in results if "decoder" not in r)
    starts = [r["started"] for r in results]
    ends = [r["ended"] for r in results]
    serving = max(ends) - min(starts)
    dets = sum(r.get("detections", 0) for r in results)

    print(f"\n=== {args.mode} ===", file=sys.stderr)
    print(f"model load (before clock) : {load_s:7.1f} s", file=sys.stderr)
    print(f"frames                    : {frames}", file=sys.stderr)
    print(f"detections                : {dets}", file=sys.stderr)
    print(f"serving                   : {serving:7.1f} s  -> {frames / serving:6.2f} FPS",
          file=sys.stderr)
    for r in results:
        if "infer_ms_per_frame" in r:
            print(f"gpu infer                 : {r['infer_ms_per_frame']:.2f} ms/frame",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
