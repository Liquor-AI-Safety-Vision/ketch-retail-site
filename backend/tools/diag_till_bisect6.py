"""Sixth bisect: A/B the two sessions against each other, alternating.

The numbers have stopped being comparable across scripts. Timing the exact
same work gave 10.14ms in one process (bisect4) and 53.6ms in another
(bisect5), with no code difference between them -- so at least one earlier
comparison was really measuring drift between two runs, not the thing it
claimed to measure.

The only sound way left is to put every variant in one process and alternate
between them in rounds, so any drift hits all of them equally:

  X  a plain session, options untouched
  Y  a session built with _session_options(cfg)
  Z  the SharedTillService's own session (service alive, runner thread running)

All three get identical work: real frame, real letterbox, real preprocess,
one sess.run. Three rounds, so a trend over time is visible instead of being
mistaken for a difference between variants.

    python backend/tools/diag_till_bisect6.py
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
REPS = 15
ROUNDS = 3


def main() -> None:
    import cv2
    import onnxruntime as ort

    from diag_infer_modules import _real_frames

    from backend.src.runtime.shared_inference import (
        SharedInferenceRegistry, _letterbox, _preferred_providers, _session_options)

    frames = _real_frames(8)
    providers = _preferred_providers(False)
    cfg = {"till_num_workers": 1, "till_max_batch": 1, "till_batch_wait_s": 0.005}

    plain_so = ort.SessionOptions()
    plain_so.log_severity_level = 3
    sess_x = ort.InferenceSession(TILL_MODEL, plain_so, providers=providers)
    sess_y = ort.InferenceSession(TILL_MODEL, _session_options(cfg), providers=providers)
    registry = SharedInferenceRegistry(cpu_only=False, config=cfg)
    till = registry.get_till(TILL_MODEL, imgsz=640)

    print(f"cpu quota (cv2 default threads) = {cv2.getNumThreads()}, "
          f"os.cpu_count = {__import__('os').cpu_count()}", file=sys.stderr)

    def preprocess(frame):
        lb, _r, _dx, _dy = _letterbox(frame, 640)
        blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
        blob = np.ascontiguousarray(blob, np.float32) / np.float32(255.0)
        return np.stack([blob], axis=0)

    counter = [0]

    def one(sess):
        i = counter[0] % len(frames)
        counter[0] += 1
        t0 = time.perf_counter()
        batch = preprocess(frames[i])
        t1 = time.perf_counter()
        sess.run(None, {sess.get_inputs()[0].name: batch})
        t2 = time.perf_counter()
        return t1 - t0, t2 - t1

    variants = [("X plain session", sess_x), ("Y _session_options", sess_y),
                ("Z service session", till.sess)]
    for _name, s in variants:  # warm every variant before any is timed
        for _ in range(3):
            one(s)

    try:
        for rnd in range(ROUNDS):
            print(f"  --- round {rnd + 1} ---", file=sys.stderr)
            for name, sess in variants:
                pre = run = 0.0
                for _ in range(REPS):
                    p, r = one(sess)
                    pre += p
                    run += r
                print(f"    {name:<22} preprocess={pre / REPS * 1e3:7.2f}ms  "
                      f"sess.run={run / REPS * 1e3:7.2f}ms", file=sys.stderr)
            # And the full service path in the same round, for scale.
            t0 = time.perf_counter()
            for i in range(REPS):
                till.detect(frames[i % len(frames)], conf=0.7)
            print(f"    {'full till.detect()':<22} total={(time.perf_counter() - t0) / REPS * 1e3:7.2f}ms",
                  file=sys.stderr)
    finally:
        registry.close()


if __name__ == "__main__":
    main()
