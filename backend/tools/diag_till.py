"""Where do till's 57ms go: preprocess, the session, or the mask postprocess?

The module sweep put the whole split pipeline's ceiling on till: ~17.9
calls/s against pose's 172 and ReID's 114-357, and unlike those two it got
*worse* with more threads, not better. That is enough to know till is the
constraint but not enough to fix it, because a segmentation model has three
very different places to lose 57ms:

  preprocess   letterbox + normalise, and note this runs inside the batch
               runner thread, so with num_workers=1 every camera's
               preprocessing is serialised behind every other camera's
  infer        the ONNX session itself
  postprocess  per-detection: proto matmul, sigmoid over a g*g mask, resize
               to box size, findContours -- all Python/OpenCV, all on the
               runner thread too

TillResult already carries the first two as profile fields, so this reads
them straight out rather than re-instrumenting, and derives postprocess as
what is left of the wall clock. The raw session sweep underneath gives the
floor the model itself cannot go below.

    python backend/tools/diag_till.py
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT / "backend/tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend/tools"))

TILL_MODEL = str(ROOT / "models/till_seg_v4_640.onnx")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--calls", type=int, default=30)
    p.add_argument("--frames", type=int, default=12)
    p.add_argument("--model", default=TILL_MODEL)
    args = p.parse_args()

    from diag_infer_modules import _real_frames

    import onnxruntime as ort

    from backend.src.runtime.shared_inference import SharedInferenceRegistry, _preferred_providers

    frames = _real_frames(args.frames)
    print(f"till model: {Path(args.model).name} "
          f"({Path(args.model).stat().st_size / 1e6:.1f} MB), {len(frames)} real frames",
          file=sys.stderr)

    # One worker, batch of one: the simplest possible shape, so nothing here
    # is queueing behind anything else and every millisecond is real work.
    registry = SharedInferenceRegistry(cpu_only=False, config={
        "till_num_workers": 1, "till_max_batch": 1, "till_batch_wait_s": 0.005})
    try:
        till = registry.get_till(args.model, imgsz=640)
        for i in range(4):
            till.detect(frames[i % len(frames)], conf=0.7)

        acc: dict[str, float] = {}
        started = time.perf_counter()
        for i in range(args.calls):
            result = till.detect(frames[i % len(frames)], conf=0.7)
            for k, v in result.profile.items():
                if isinstance(v, (int, float)):
                    acc[k] = acc.get(k, 0.0) + float(v)
        wall = (time.perf_counter() - started) / args.calls

        print(f"\n  service call            {wall * 1e3:8.2f} ms   "
              f"({1 / wall:6.2f} calls/s)", file=sys.stderr)
        accounted = 0.0
        for k in ("letterbox_s", "preprocess_s", "infer_s"):
            if k in acc:
                v = acc[k] / args.calls
                accounted += v
                print(f"    {k:<20} {v * 1e3:8.2f} ms   {v / wall * 100:5.1f}%", file=sys.stderr)
        rest = wall - accounted
        print(f"    {'postprocess+queue':<20} {rest * 1e3:8.2f} ms   {rest / wall * 100:5.1f}%",
              file=sys.stderr)
        for k, v in sorted(acc.items()):
            if k not in {"letterbox_s", "preprocess_s", "infer_s"}:
                print(f"    ({k} = {v / args.calls:.3f})", file=sys.stderr)
        print(f"  last detections: {[(d[0], round(d[1], 2)) for d in result.detections]}",
              file=sys.stderr)
    finally:
        registry.close()

    sess = ort.InferenceSession(args.model, providers=_preferred_providers(False))
    inp = sess.get_inputs()[0]
    dtype = np.float16 if inp.type == "tensor(float16)" else np.float32
    print(f"\n  raw session ({sess.get_providers()[0]}) input={inp.shape} {inp.type}",
          file=sys.stderr)
    print(f"  outputs: {[(o.name, o.shape) for o in sess.get_outputs()]}", file=sys.stderr)
    for b in (1, 2, 4, 8):
        batch = np.random.rand(b, 3, 640, 640).astype(dtype)
        for _ in range(3):  # warm this exact shape; the CUDA EP re-plans per shape
            sess.run(None, {inp.name: batch})
        t0 = time.perf_counter()
        reps = 15
        for _ in range(reps):
            sess.run(None, {inp.name: batch})
        per = (time.perf_counter() - t0) / reps
        print(f"    batch={b:<2} {per * 1e3:7.2f} ms/call -> {per / b * 1e3:6.2f} ms/frame",
              file=sys.stderr)


if __name__ == "__main__":
    main()
