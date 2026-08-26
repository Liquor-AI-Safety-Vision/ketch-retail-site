"""Second bisect: the session is fast alone, so what makes it slow in place?

The first bisect proved the session itself is healthy -- 6.4-8.1ms whether
called with plain options, service options, a real blob, or from a
background thread. Only the full service call jumped, to 60.74ms, and the
service's own profile blames 35.62ms of that on the sess.run line itself.
The same call cannot be both 8ms and 35ms, so something about *where it sits*
is the cost, not the call.

The one thing _infer_batch does that none of the first bisect's cases did is
run cv2.resize and a full-frame numpy divide immediately before sess.run, on
the same thread. On a 192-core box both OpenCV and onnxruntime default their
pools to the core count, so that sequence asks two ~192-thread pools to hand
off to each other on every single frame. This times the sequence, not the
call, and then removes one pool at a time:

  G  preprocess then sess.run, both pools at default        (the suspect)
  H  same, cv2 capped to 1 thread                           (cap_native_threads does this)
  I  same, ort intra-op capped to 8
  J  same, both capped
  K  sess.run alone, no preprocessing before it             (the 8ms control)

If G is slow and K is fast with everything else equal, the handoff is the
cost and the fix is a thread-pool setting, not a model or an architecture
change.

    python backend/tools/diag_till_bisect2.py
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


def main() -> None:
    import cv2
    import onnxruntime as ort

    from diag_infer_modules import _real_frames

    from backend.src.runtime.shared_inference import _letterbox, _preferred_providers

    frames = _real_frames(8)
    providers = _preferred_providers(False)
    print(f"cv2 threads default = {cv2.getNumThreads()}, "
          f"ort default intra-op = core count", file=sys.stderr)

    def build(intra: int = 0):
        so = ort.SessionOptions()
        so.log_severity_level = 3
        if intra > 0:
            so.intra_op_num_threads = intra
            so.inter_op_num_threads = 1
        return ort.InferenceSession(TILL_MODEL, so, providers=providers)

    def preprocess(frame):
        lb, r, dx, dy = _letterbox(frame, 640)
        blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
        blob = np.ascontiguousarray(blob, np.float32) / np.float32(255.0)
        return np.stack([blob], axis=0)

    def measure(sess, label: str, with_preprocess: bool):
        inp = sess.get_inputs()[0].name
        pre = preprocess(frames[0])
        for _ in range(3):
            sess.run(None, {inp: pre})
        pre_total = 0.0
        run_total = 0.0
        t_all = time.perf_counter()
        for i in range(REPS):
            if with_preprocess:
                t0 = time.perf_counter()
                batch = preprocess(frames[i % len(frames)])
                pre_total += time.perf_counter() - t0
            else:
                batch = pre
            t0 = time.perf_counter()
            sess.run(None, {inp: batch})
            run_total += time.perf_counter() - t0
        wall = time.perf_counter() - t_all
        print(f"  {label:<52} total={wall / REPS * 1e3:7.2f}ms  "
              f"(preprocess={pre_total / REPS * 1e3:6.2f}  sess.run={run_total / REPS * 1e3:7.2f})",
              file=sys.stderr)

    cv2.setNumThreads(-1)  # back to the library default
    s_default = build()
    measure(s_default, "G  preprocess + run, both pools default", True)

    cv2.setNumThreads(1)
    measure(s_default, "H  preprocess + run, cv2=1 thread", True)

    cv2.setNumThreads(-1)
    s_intra8 = build(intra=8)
    measure(s_intra8, "I  preprocess + run, ort intra-op=8", True)

    cv2.setNumThreads(1)
    measure(s_intra8, "J  preprocess + run, cv2=1 and ort intra-op=8", True)

    cv2.setNumThreads(-1)
    measure(s_default, "K  run alone, no preprocess before it", False)


if __name__ == "__main__":
    main()
