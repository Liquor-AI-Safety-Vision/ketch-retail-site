"""Split a pose sess.run into transfer, compute and per-call overhead.

yolo26n (12 MB) and yolo26m (86 MB) benchmark within 16% of each other, which
says the model arithmetic is not what the 5ms per call is being spent on. This
probe separates the parts so the next optimisation targets the real one:

  - normal run          : what the pipeline pays today (implicit H2D + D2H + sync)
  - io_binding on GPU   : same compute, input already resident on device
  - batch scaling       : how much of the cost is per-call rather than per-image

Run on the GPU box:
    python ai/src/app/diag_call_overhead.py --model models/yolo26m-pose.onnx
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[3]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import statistics
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from ai.src.common.paths import model_path


def median_ms(fn, n: int = 100) -> float:
    for _ in range(10):
        fn()
    runs = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        runs.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(runs)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=model_path("yolo26m-pose.onnx"))
    p.add_argument("--imgsz", type=int, default=640)
    a = p.parse_args()

    so = ort.SessionOptions()
    so.log_severity_level = 3
    sess = ort.InferenceSession(
        str(Path(a.model).resolve()),
        so,
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    meta = sess.get_inputs()[0]
    name = meta.name
    dtype = np.float16 if meta.type == "tensor(float16)" else np.float32
    out_name = sess.get_outputs()[0].name

    print(f"model={Path(a.model).name}  input_dtype={np.dtype(dtype).name}")

    # 1. What the pipeline pays now.
    host = np.random.rand(1, 3, a.imgsz, a.imgsz).astype(dtype)
    normal = median_ms(lambda: sess.run(None, {name: host}))
    print(f"normal run (implicit copy + sync)   : {normal:6.2f} ms")

    # 2. Same compute with the input already on the device.
    try:
        dev = ort.OrtValue.ortvalue_from_numpy(host, "cuda", 0)
        binding = sess.io_binding()
        binding.bind_ortvalue_input(name, dev)
        binding.bind_output(out_name, "cuda", 0)

        def bound_run():
            sess.run_with_iobinding(binding)

        bound = median_ms(bound_run)
        print(f"io_binding, input resident on GPU   : {bound:6.2f} ms")
        print(f"  -> per-call transfer/sync overhead: {normal - bound:6.2f} ms "
              f"({100 * (normal - bound) / normal:.0f}% of the call)")
    except Exception as exc:  # pragma: no cover - depends on ORT build
        print(f"io_binding unavailable: {exc!r}")

    # 3. Per-call vs per-image: if cost were pure compute this would be linear.
    print("\nbatch scaling (fixed shape each, so no per-shape penalty):")
    base = None
    for b in (1, 2, 4, 8):
        try:
            blob = np.random.rand(b, 3, a.imgsz, a.imgsz).astype(dtype)
            ms = median_ms(lambda blob=blob: sess.run(None, {name: blob}), n=40)
        except Exception:
            print(f"  b{b}: unsupported (static batch model)")
            continue
        if base is None:
            base = ms
        print(f"  b{b}: {ms:7.2f} ms total  {ms / b:6.2f} ms/image  "
              f"speedup/image vs b1 {base / (ms / b):4.2f}x")


if __name__ == "__main__":
    main()
