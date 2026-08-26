"""Third bisect: same code, three ways to call it.

Established so far: the till session runs in 6.5ms; preprocessing before it
adds 4ms; both together are ~11ms. Yet one till.detect() costs 60ms, and the
service's own profile puts 35ms of that on the sess.run line -- the same line
that takes 6.5ms when timed anywhere else. And this is not the batch runner
being slow in general, because the pose service goes through the identical
runner and adds only ~1.5ms.

So the remaining question is narrow: is the cost in _infer_batch's body, or
in getting to it? These call exactly the same function three ways.

  L  _infer_batch() directly, main thread          -- the body alone
  M  _infer_batch() directly, background thread    -- the body, off-main
  N  detect() through the runner                   -- the body plus the runner

L tells us what the code costs. N - M is what the runner costs. If L is
already 55ms then every earlier measurement was of something the real path
does differently, and the mask postprocess is where to look next.

    python backend/tools/diag_till_bisect3.py
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "scripts", ROOT / "backend/tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

TILL_MODEL = str(ROOT / "models/till_seg_v4_640.onnx")
REPS = 15


def _time(fn, label: str):
    for _ in range(3):
        fn()
    t0 = time.perf_counter()
    for _ in range(REPS):
        fn()
    per = (time.perf_counter() - t0) / REPS
    print(f"  {label:<50} {per * 1e3:8.2f} ms", file=sys.stderr)
    return per


def _time_in_thread(fn, label: str):
    box = {}
    t = threading.Thread(target=lambda: box.update(per=_time(fn, label)))
    t.start()
    t.join()
    return box["per"]


def main() -> None:
    from diag_infer_modules import _real_frames

    from backend.src.runtime.shared_inference import SharedInferenceRegistry

    frames = _real_frames(8)
    cfg = {"till_num_workers": 1, "till_max_batch": 1, "till_batch_wait_s": 0.005}
    registry = SharedInferenceRegistry(cpu_only=False, config=cfg)
    try:
        till = registry.get_till(TILL_MODEL, imgsz=640)
        counter = [0]

        def body():
            i = counter[0] % len(frames)
            counter[0] += 1
            return till._infer_batch([{"frame_bgr": frames[i], "conf": 0.7}])

        _time(body, "L  _infer_batch() directly, main thread")
        _time_in_thread(body, "M  _infer_batch() directly, background thread")

        def via_runner():
            i = counter[0] % len(frames)
            counter[0] += 1
            return till.detect(frames[i], conf=0.7)

        _time(via_runner, "N  detect() through the batch runner")

        # And the profile from the direct call, so the split inside the body
        # is measured on the same path L just timed.
        result = body()
        prof = {k: v for k, v in result[0].profile.items() if isinstance(v, (int, float))}
        print(f"  profile from the direct call: "
              f"letterbox={prof.get('letterbox_s', 0) * 1e3:.2f}ms "
              f"preprocess={prof.get('preprocess_s', 0) * 1e3:.2f}ms "
              f"infer={prof.get('infer_s', 0) * 1e3:.2f}ms "
              f"masks={prof.get('decode_masks_s', 0) * 1e3:.2f}ms", file=sys.stderr)
    finally:
        registry.close()


if __name__ == "__main__":
    main()
