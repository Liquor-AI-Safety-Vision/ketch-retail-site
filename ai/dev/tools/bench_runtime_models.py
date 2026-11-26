from __future__ import annotations

from pathlib import Path as _Path

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort


ROOT = _Path(__file__).resolve().parents[3]

MODEL_SPECS = {
    "pose": {
        "filename": "yolo26m-pose.onnx",
        "shape": [1, 3, 640, 640],
    },
    "reid": {
        "filename": "osnet_x0_25_msmt17.onnx",
        "shape": [16, 3, 256, 128],
    },
    "face_det": {
        "filename": "yolov6s_face.onnx",
        "shape": [1, 3, 640, 640],
    },
    "arcface": {
        "filename": "ms1mv3_arcface_r18.onnx",
        "shape": [1, 3, 112, 112],
    },
    "till": {
        "filename": "till_seg_v4_640.onnx",
        "shape": [1, 3, 640, 640],
    },
}


def default_providers(mode: str) -> list[str]:
    if mode == "cpu":
        return ["CPUExecutionProvider"]
    return ["CUDAExecutionProvider", "CPUExecutionProvider"]


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    idx = (len(values) - 1) * q
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return values[lo]
    frac = idx - lo
    return values[lo] * (1.0 - frac) + values[hi] * frac


def make_input(shape: list[int], seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.random(shape, dtype=np.float32)


def monitor_gpu(samples: list[dict[str, float]], stop: threading.Event):
    proc = subprocess.Popen(
        [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,utilization.memory,memory.used",
            "--format=csv,noheader,nounits",
            "-lms",
            "100",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        while not stop.is_set():
            line = proc.stdout.readline()
            if not line:
                break
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                samples.append(
                    {
                        "gpu_util_percent": float(parts[0]),
                        "mem_util_percent": float(parts[1]),
                        "mem_used_mib": float(parts[2]),
                    }
                )
            except ValueError:
                continue
    finally:
        stop.set()
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def summarize_samples(samples: list[dict[str, float]]) -> dict[str, float | int | None]:
    if not samples:
        return {
            "samples": 0,
            "avg_gpu_util_percent": None,
            "max_gpu_util_percent": None,
            "avg_mem_util_percent": None,
            "max_mem_util_percent": None,
            "avg_mem_used_mib": None,
            "max_mem_used_mib": None,
        }
    gpu = [item["gpu_util_percent"] for item in samples]
    mem = [item["mem_util_percent"] for item in samples]
    used = [item["mem_used_mib"] for item in samples]
    return {
        "samples": len(samples),
        "avg_gpu_util_percent": statistics.mean(gpu),
        "max_gpu_util_percent": max(gpu),
        "avg_mem_util_percent": statistics.mean(mem),
        "max_mem_util_percent": max(mem),
        "avg_mem_used_mib": statistics.mean(used),
        "max_mem_used_mib": max(used),
    }


def bench_one(
    name: str,
    spec: dict,
    model_root: Path,
    providers: list[str],
    warmup: int,
    min_seconds: float,
) -> dict:
    path = str(model_root / spec["filename"])
    so = ort.SessionOptions()
    so.log_severity_level = 3
    sess = ort.InferenceSession(path, so, providers=providers)
    input_meta = sess.get_inputs()[0]
    input_name = input_meta.name
    blob = make_input(spec["shape"])
    if input_meta.type == "tensor(float16)":
        blob = blob.astype(np.float16)

    for _ in range(warmup):
        sess.run(None, {input_name: blob})

    gpu_samples: list[dict[str, float]] = []
    stop = threading.Event()
    monitor = None
    if "CUDAExecutionProvider" in sess.get_providers():
        monitor = threading.Thread(target=monitor_gpu, args=(gpu_samples, stop), daemon=True)
        monitor.start()

    lat_ms: list[float] = []
    started = time.perf_counter()
    while time.perf_counter() - started < min_seconds:
        t0 = time.perf_counter()
        sess.run(None, {input_name: blob})
        lat_ms.append((time.perf_counter() - t0) * 1000.0)

    stop.set()
    if monitor is not None:
        monitor.join(timeout=2)

    lat_sorted = sorted(lat_ms)
    total_s = sum(lat_ms) / 1000.0
    return {
        "model": name,
        "model_path": path,
        "input_shape": spec["shape"],
        "providers_requested": providers,
        "providers_actual": sess.get_providers(),
        "warmup_runs": warmup,
        "timed_runs": len(lat_ms),
        "timed_total_s": total_s,
        "latency_ms_mean": statistics.mean(lat_ms) if lat_ms else None,
        "latency_ms_p50": percentile(lat_sorted, 0.50),
        "latency_ms_p95": percentile(lat_sorted, 0.95),
        "latency_ms_min": min(lat_ms) if lat_ms else None,
        "latency_ms_max": max(lat_ms) if lat_ms else None,
        "throughput_fps": (len(lat_ms) / total_s) if total_s > 0 else None,
        "gpu_monitor": summarize_samples(gpu_samples),
    }


def main():
    p = argparse.ArgumentParser(description="Benchmark per-model ONNX inference without session load time.")
    p.add_argument(
        "--mode",
        choices=["cuda", "cpu"],
        default="cuda",
        help="Execution provider preference. 'cuda' requests CUDAExecutionProvider first.",
    )
    p.add_argument(
        "--models",
        default="pose,reid,face_det,arcface,till",
        help="Comma-separated subset of models to benchmark.",
    )
    p.add_argument("--warmup", type=int, default=10)
    p.add_argument("--min-seconds", type=float, default=5.0)
    p.add_argument(
        "--model-root",
        default=str(ROOT / "ai/models"),
        help="Directory containing benchmark target ONNX models.",
    )
    p.add_argument("--out", help="Optional JSON output path.")
    a = p.parse_args()

    selected = [item.strip() for item in a.models.split(",") if item.strip()]
    model_root = Path(a.model_root)
    providers = default_providers(a.mode)
    results = []
    for name in selected:
        if name not in MODEL_SPECS:
            raise SystemExit(f"unknown model: {name}")
        print(f"[bench] {name} providers={providers}", flush=True)
        results.append(
            bench_one(
                name,
                MODEL_SPECS[name],
                model_root=model_root,
                providers=providers,
                warmup=a.warmup,
                min_seconds=a.min_seconds,
            )
        )

    payload = {
        "host": os.uname().nodename,
        "mode": a.mode,
        "results": results,
    }
    text = json.dumps(payload, indent=2)
    if a.out:
        Path(a.out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
