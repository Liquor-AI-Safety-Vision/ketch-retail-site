"""Fourth bisect: one buffer reused, or a fresh array every call?

Every fast measurement so far reused a single preallocated input array;
every slow one built a new one. That is the last uncontrolled variable
between 6.5ms and 56ms, and it is not an obviously innocent one: a fresh
host allocation each call means the CUDA driver copies from pageable memory
it has never seen before, every time, instead of from a range it has already
faulted in.

Four variants, identical in every other respect -- same session, same
thread, same shape, same dtype, same values:

  P  one buffer, reused                       (the 6.5ms case)
  Q  fresh np.empty + fill, every call
  R  fresh array from the real preprocess     (what _infer_batch does)
  S  real preprocess, then copied into one reused buffer   (the candidate fix)

If S is as fast as P, the fix is to preprocess into a buffer the session
keeps, and it applies to every Shared*Service, not just till.

    python backend/tools/diag_till_bisect4.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "scripts", ROOT / "backend/tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

TILL_MODEL = str(ROOT / "models/till_seg_v4_640.onnx")
REPS = 20


def _time(fn, label: str):
    for _ in range(3):
        fn()
    t0 = time.perf_counter()
    for _ in range(REPS):
        fn()
    per = (time.perf_counter() - t0) / REPS
    print(f"  {label:<56} {per * 1e3:8.2f} ms", file=sys.stderr)
    return per


def main() -> None:
    import cv2
    import onnxruntime as ort

    from diag_infer_modules import _real_frames

    from backend.src.runtime.shared_inference import _letterbox, _preferred_providers

    frames = _real_frames(8)
    so = ort.SessionOptions()
    so.log_severity_level = 3
    sess = ort.InferenceSession(TILL_MODEL, so, providers=_preferred_providers(False))
    inp = sess.get_inputs()[0].name

    def preprocess(frame):
        lb, _r, _dx, _dy = _letterbox(frame, 640)
        blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
        return np.ascontiguousarray(blob, np.float32) / np.float32(255.0)

    reused = np.stack([preprocess(frames[0])], axis=0)
    counter = [0]

    def nxt():
        i = counter[0] % len(frames)
        counter[0] += 1
        return frames[i]

    _time(lambda: sess.run(None, {inp: reused}), "P  one buffer, reused")

    def fresh_empty():
        buf = np.empty((1, 3, 640, 640), np.float32)
        buf[:] = reused
        sess.run(None, {inp: buf})

    _time(fresh_empty, "Q  fresh np.empty + fill, every call")

    def fresh_real():
        batch = np.stack([preprocess(nxt())], axis=0)
        sess.run(None, {inp: batch})

    _time(fresh_real, "R  fresh array from the real preprocess (what the code does)")

    def into_reused():
        np.copyto(reused[0], preprocess(nxt()))
        sess.run(None, {inp: reused})

    _time(into_reused, "S  real preprocess copied into one reused buffer")

    # Same four, but timing only the sess.run part, so preprocessing cost
    # cannot be mistaken for transfer cost.
    print("  --- sess.run only ---", file=sys.stderr)

    def run_only(make_batch, label):
        batches = [make_batch() for _ in range(REPS)]
        for _ in range(3):
            sess.run(None, {inp: batches[0]})
        t0 = time.perf_counter()
        for b in batches:
            sess.run(None, {inp: b})
        per = (time.perf_counter() - t0) / REPS
        print(f"  {label:<56} {per * 1e3:8.2f} ms", file=sys.stderr)

    run_only(lambda: reused, "P' sess.run on the same buffer every time")
    run_only(lambda: np.stack([preprocess(nxt())], axis=0),
             "R' sess.run on a distinct fresh array every time")


if __name__ == "__main__":
    main()
