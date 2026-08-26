"""The same sess.run costs 7.27ms raw and 35.62ms inside SharedTillService.

Both are one call, batch of one, same model, same provider, and the service's
session options come out equal to the defaults for this config, so reasoning
has run out of suspects. This changes one variable at a time between the two
and times each, so whichever step jumps from 7ms to 35ms is the cause:

  A  main thread, random input, plain SessionOptions      (the raw 7.27ms)
  B  main thread, random input, _session_options(cfg)     -> is it the options?
  C  main thread, real letterboxed blob                   -> is it the input?
  D  background thread, random input                      -> is it the thread?
  E  the service's own session object, called directly    -> is it the session?
  F  the service through its batch runner                 (the 35.62ms)

E is the interesting one: it reaches past the batch runner and calls the
service's session by hand, which separates "this session is slow" from "the
machinery around it is slow".

    python backend/tools/diag_till_bisect.py
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "scripts", ROOT / "backend/tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

TILL_MODEL = str(ROOT / "models/till_seg_v4_640.onnx")
REPS = 15


def _time(fn, label: str, warm: int = 3, reps: int = REPS) -> float:
    for _ in range(warm):
        fn()
    t0 = time.perf_counter()
    for _ in range(reps):
        fn()
    per = (time.perf_counter() - t0) / reps
    print(f"  {label:<52} {per * 1e3:8.2f} ms", file=sys.stderr)
    return per


def _time_in_thread(fn, label: str) -> float:
    """Same timing, but the calls happen on a non-main thread -- which is
    where the batch runner actually calls the session from."""
    out = {}

    def body():
        out["per"] = _time(fn, label)

    t = threading.Thread(target=body)
    t.start()
    t.join()
    return out["per"]


def main() -> None:
    import onnxruntime as ort

    from diag_infer_modules import _real_frames

    from backend.src.runtime.shared_inference import (
        SharedInferenceRegistry, _preferred_providers, _session_options)

    frames = _real_frames(4)
    providers = _preferred_providers(False)
    rand = np.random.rand(1, 3, 640, 640).astype(np.float32)

    print(f"providers={providers}", file=sys.stderr)

    sess_a = ort.InferenceSession(TILL_MODEL, providers=providers)
    inp = sess_a.get_inputs()[0].name
    _time(lambda: sess_a.run(None, {inp: rand}), "A  plain options, random input, main thread")

    cfg = {"till_num_workers": 1, "till_max_batch": 1, "till_batch_wait_s": 0.005}
    sess_b = ort.InferenceSession(TILL_MODEL, _session_options(cfg), providers=providers)
    _time(lambda: sess_b.run(None, {inp: rand}), "B  _session_options(cfg), random input, main thread")

    # The exact array the service builds, so a difference here would mean the
    # input's contents or memory layout, not the machinery around it.
    from backend.src.runtime.shared_inference import _letterbox
    import cv2

    lb, _r, _dx, _dy = _letterbox(frames[0], 640)
    blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
    blob = np.ascontiguousarray(blob, np.float32) / np.float32(255.0)
    real = np.stack([blob], axis=0)
    _time(lambda: sess_a.run(None, {inp: real}), "C  plain options, real blob, main thread")

    _time_in_thread(lambda: sess_a.run(None, {inp: rand}), "D  plain options, random input, background thread")

    registry = SharedInferenceRegistry(cpu_only=False, config=cfg)
    try:
        till = registry.get_till(TILL_MODEL, imgsz=640)
        _time(lambda: till.sess.run(None, {till.inp: real}),
              "E  the service's own session, called directly")
        _time_in_thread(lambda: till.sess.run(None, {till.inp: real}),
                        "E' the service's own session, background thread")
        _time(lambda: till.detect(frames[0], conf=0.7),
              "F  the full service call (batch runner + postprocess)")
    finally:
        registry.close()


if __name__ == "__main__":
    main()
