"""Is an input tensor backed by shared memory slower to infer on?

The lane's per-component costs do not match the same components measured
standalone: pose 27ms against 8.3ms, ReID 21ms against 9.4ms -- but till
14ms against 14.2ms, a dead match. And the gap is there with a single lane
running alone, so it is not contention between lanes.

The one thing that separates till from the other two is where its input
comes from. Till receives the frame and letterboxes it into a fresh array,
so the array handed to sess.run is ordinary heap memory. Pose is handed
ring.blob_slot(slot) and passes it to sess.run untouched; ReID crops
straight out of ring.frame_slot(slot). Both of those are views into a
multiprocessing.shared_memory mapping.

That is worth a direct test, because it decides a real design question: the
ring exists to avoid copying frames between processes, but if inference on a
mapped buffer is slower than one memcpy would cost, the ring should hand the
lane a copy and keep its zero-copy benefit only for the transport.

  heap   ordinary np array                       (the standalone case)
  shm    a view into shared_memory               (what the lane does)
  copy   shm copied to heap, then inferred       (the candidate fix)

Alternated in rounds so drift cannot be mistaken for a difference.

    python ai/tools/diag_shm_input.py
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[3]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import sys
import time
from multiprocessing import shared_memory
from pathlib import Path

ROOT = _Path(__file__).resolve().parents[3]
for p in (ROOT, ROOT / "scripts", ai_root() / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ai.src.runtime.perf import cap_native_threads  # noqa: E402

cap_native_threads(1)

import numpy as np  # noqa: E402
from ai.src.common.paths import model_path

POSE_MODEL = model_path("yolo26m-pose.onnx")
REPS = 30
ROUNDS = 3


def main() -> None:
    import onnxruntime as ort

    from diag_infer_modules import _real_frames

    from ai.src.runtime.shared_inference import SharedInferenceRegistry, _preferred_providers

    frames = _real_frames(8)
    registry = SharedInferenceRegistry(cpu_only=False, config={
        "pose_num_workers": 1, "pose_max_batch": 1, "pose_batch_wait_s": 0.005,
        "pose_pad_batch": True})
    pose = registry.get_pose(POSE_MODEL)
    blobs, metas = [], []
    for f in frames:
        b, m = pose.preprocess(f)
        blobs.append(b)
        metas.append(m)
    registry.close()

    sess = ort.InferenceSession(POSE_MODEL, providers=_preferred_providers(False))
    inp = sess.get_inputs()[0].name
    shape = (1,) + blobs[0].shape
    dtype = blobs[0].dtype

    heap = [np.stack([b], axis=0).astype(dtype) for b in blobs]

    # The same bytes, but living in a shared_memory block the way a ring slot
    # does -- same size, same alignment story, same mapping mechanism.
    nbytes = int(np.prod(shape)) * dtype.itemsize
    shms = [shared_memory.SharedMemory(create=True, size=nbytes) for _ in blobs]
    shm_views = []
    for blk, h in zip(shms, heap):
        view = np.ndarray(shape, dtype=dtype, buffer=blk.buf)
        view[:] = h
        shm_views.append(view)

    scratch = np.empty(shape, dtype=dtype)

    def run_heap(i):
        sess.run(None, {inp: heap[i % len(heap)]})

    def run_shm(i):
        sess.run(None, {inp: shm_views[i % len(shm_views)]})

    def run_copy(i):
        np.copyto(scratch, shm_views[i % len(shm_views)])
        sess.run(None, {inp: scratch})

    variants = [("heap  ordinary array", run_heap),
                ("shm   shared-memory view", run_shm),
                ("copy  shm -> heap, then run", run_copy)]

    for _name, fn in variants:
        for i in range(5):
            fn(i)

    try:
        print(f"  input {shape} {dtype}, {nbytes / 1e6:.1f} MB per tensor", file=sys.stderr)
        for rnd in range(ROUNDS):
            print(f"  --- round {rnd + 1} ---", file=sys.stderr)
            for name, fn in variants:
                t0 = time.perf_counter()
                for i in range(REPS):
                    fn(i)
                per = (time.perf_counter() - t0) / REPS
                print(f"    {name:<32} {per * 1e3:7.2f} ms", file=sys.stderr)

        # A plain memcpy of the same buffer, for scale: if 'copy' beats 'shm'
        # by more than this, the copy is not what is being paid for.
        t0 = time.perf_counter()
        for i in range(REPS):
            np.copyto(scratch, shm_views[i % len(shm_views)])
        print(f"  memcpy alone {(time.perf_counter() - t0) / REPS * 1e3:7.2f} ms", file=sys.stderr)
    finally:
        for blk in shms:
            blk.close()
            blk.unlink()


if __name__ == "__main__":
    main()
