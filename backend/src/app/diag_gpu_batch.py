"""Diagnose CUDA EP behaviour behind the in-pipeline pose latency.

Answers three questions the aggregate benchmark could not:
  1. how long session creation actually costs (the bootstrap component)
  2. whether a *changing* batch shape re-triggers cuDNN algo search
  3. whether cudnn_conv_algo_search=HEURISTIC removes that cost

Run on the GPU box:
    python backend/src/app/diag_gpu_batch.py --model models/yolo26m-pose.onnx
"""
from __future__ import annotations

import argparse
import statistics
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort


def build(model_path: str, algo: str | None) -> tuple[ort.InferenceSession, float]:
    so = ort.SessionOptions()
    so.log_severity_level = 3
    opts: dict[str, object] = {}
    if algo:
        opts["cudnn_conv_algo_search"] = algo
    providers = [("CUDAExecutionProvider", opts), "CPUExecutionProvider"]
    t0 = time.perf_counter()
    sess = ort.InferenceSession(model_path, so, providers=providers)
    return sess, (time.perf_counter() - t0) * 1000.0


def timed(sess: ort.InferenceSession, name: str, dtype, batch: int, imgsz: int) -> float:
    blob = np.random.rand(batch, 3, imgsz, imgsz).astype(dtype)
    t0 = time.perf_counter()
    sess.run(None, {name: blob})
    return (time.perf_counter() - t0) * 1000.0


def probe(model_path: str, algo: str | None, imgsz: int, label: str) -> None:
    sess, load_ms = build(model_path, algo)
    meta = sess.get_inputs()[0]
    dtype = np.float16 if meta.type == "tensor(float16)" else np.float32

    print(f"\n=== {label} ===")
    print(f"session_create_ms = {load_ms:.0f}")

    # First call per batch shape: pays whatever per-shape setup the EP does.
    first: dict[int, float] = {}
    for b in (1, 2, 3, 4):
        first[b] = timed(sess, meta.name, dtype, b, imgsz)
    print("first call per new batch shape (ms): " + "  ".join(f"b{b}={v:8.1f}" for b, v in first.items()))

    # Steady state per shape, after that shape has been seen.
    steady: dict[int, float] = {}
    for b in (1, 2, 3, 4):
        runs = [timed(sess, meta.name, dtype, b, imgsz) for _ in range(20)]
        steady[b] = statistics.median(runs)
    print("steady median per batch (ms):      " + "  ".join(f"b{b}={v:8.1f}" for b, v in steady.items()))
    print("per-image at steady (ms):          " + "  ".join(f"b{b}={v / b:8.1f}" for b, v in steady.items()))

    # Alternating shapes: reproduces the pipeline, where batch size varies 1..4.
    alt = [timed(sess, meta.name, dtype, b, imgsz) for b in [1, 2, 3, 4] * 10]
    fixed = [timed(sess, meta.name, dtype, 1, imgsz) for _ in range(40)]
    print(f"alternating shapes  median={statistics.median(alt):7.1f}ms  mean={statistics.mean(alt):7.1f}ms")
    print(f"fixed shape b=1     median={statistics.median(fixed):7.1f}ms  mean={statistics.mean(fixed):7.1f}ms")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="models/yolo26m-pose.onnx")
    p.add_argument("--imgsz", type=int, default=640)
    a = p.parse_args()
    path = str(Path(a.model).resolve())

    probe(path, None, a.imgsz, "DEFAULT (whatever ORT picks: EXHAUSTIVE)")
    probe(path, "HEURISTIC", a.imgsz, "cudnn_conv_algo_search=HEURISTIC")


if __name__ == "__main__":
    main()
