
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[2]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = _Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from backend.src.perception import run_counter as RC  # noqa: E402
from backend.src.perception import stream_track as S  # noqa: E402
from backend.src.common.paths import model_path


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    idx = (len(values) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(values) - 1)
    frac = idx - lo
    return values[lo] * (1.0 - frac) + values[hi] * frac


def ms_summary(values_s: list[float]) -> dict[str, float | int | None]:
    if not values_s:
        return {
            "count": 0,
            "mean_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "min_ms": None,
            "max_ms": None,
        }
    ordered = sorted(v * 1000.0 for v in values_s)
    return {
        "count": len(ordered),
        "mean_ms": statistics.mean(ordered),
        "p50_ms": percentile(ordered, 0.50),
        "p95_ms": percentile(ordered, 0.95),
        "min_ms": ordered[0],
        "max_ms": ordered[-1],
    }


def read_frame(video_path: str, time_s: float) -> np.ndarray:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise FileNotFoundError(video_path)
    cap.set(cv2.CAP_PROP_POS_MSEC, time_s * 1000.0)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise RuntimeError(f"failed to read frame at {time_s}s from {video_path}")
    return frame


def bench_pose(detector: S.Detector, frame: np.ndarray, iterations: int) -> dict[str, object]:
    totals = []
    infer = []
    letterbox = []
    preprocess = []
    decode = []
    det_counts = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        boxes, _kpts = detector(frame)
        totals.append(time.perf_counter() - t0)
        prof = detector.last_profile
        infer.append(float(prof.get("infer_s", 0.0)))
        letterbox.append(float(prof.get("letterbox_s", 0.0)))
        preprocess.append(float(prof.get("preprocess_s", 0.0)))
        decode.append(float(prof.get("decode_s", 0.0)))
        det_counts.append(int(prof.get("detections", len(boxes))))
    return {
        "provider": detector.provider,
        "detections_mean": statistics.mean(det_counts) if det_counts else 0.0,
        "total": ms_summary(totals),
        "letterbox": ms_summary(letterbox),
        "preprocess": ms_summary(preprocess),
        "infer": ms_summary(infer),
        "decode": ms_summary(decode),
    }


def bench_reid(embedder: S.Embedder, frame: np.ndarray, boxes: np.ndarray, iterations: int) -> dict[str, object]:
    totals = []
    infer = []
    crop_resize = []
    pack_norm = []
    post_concat = []
    l2norm = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        feats = embedder(frame, boxes)
        totals.append(time.perf_counter() - t0)
        prof = embedder.last_profile
        infer.append(float(prof.get("infer_s", 0.0)))
        crop_resize.append(float(prof.get("crop_resize_s", 0.0)))
        pack_norm.append(float(prof.get("pack_norm_s", 0.0)))
        post_concat.append(float(prof.get("post_concat_s", 0.0)))
        l2norm.append(float(prof.get("l2norm_s", 0.0)))
        _ = feats.shape
    return {
        "provider": embedder.provider,
        "num_boxes": int(len(boxes)),
        "total": ms_summary(totals),
        "crop_resize": ms_summary(crop_resize),
        "pack_norm": ms_summary(pack_norm),
        "infer": ms_summary(infer),
        "post_concat": ms_summary(post_concat),
        "l2norm": ms_summary(l2norm),
    }


def bench_till(till: RC.Till, frame: np.ndarray, iterations: int) -> dict[str, object]:
    totals = []
    infer = []
    letterbox = []
    preprocess = []
    decode_masks = []
    out_counts = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        items = till(frame)
        totals.append(time.perf_counter() - t0)
        prof = till.last_profile
        infer.append(float(prof.get("infer_s", 0.0)))
        letterbox.append(float(prof.get("letterbox_s", 0.0)))
        preprocess.append(float(prof.get("preprocess_s", 0.0)))
        decode_masks.append(float(prof.get("decode_masks_s", 0.0)))
        out_counts.append(int(prof.get("num_outputs", len(items))))
    return {
        "provider": till.provider,
        "outputs_mean": statistics.mean(out_counts) if out_counts else 0.0,
        "total": ms_summary(totals),
        "letterbox": ms_summary(letterbox),
        "preprocess": ms_summary(preprocess),
        "infer": ms_summary(infer),
        "decode_masks": ms_summary(decode_masks),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--counter-video", default=str(ROOT / "data/videos/cctv_counter/cash_till_open_2.mp4"))
    ap.add_argument("--whole-store-video", default=str(ROOT / "data/videos/whole_store/whole_store_short.mp4"))
    ap.add_argument("--pose-model", default=model_path("yolo26m-pose.onnx"))
    ap.add_argument("--reid-model", default=model_path("osnet_x0_25_msmt17.onnx"))
    ap.add_argument("--till-model", default=model_path("till_seg_v4_640.onnx"))
    ap.add_argument("--counter-time", type=float, default=15.0)
    ap.add_argument("--whole-store-time", type=float, default=10.0)
    ap.add_argument("--iterations", type=int, default=200)
    ap.add_argument("--warmup", type=int, default=20)
    args = ap.parse_args()

    S.MODEL = args.pose_model
    S.REID_MODEL = args.reid_model
    S.CONF = 0.35
    S.ORT_PROVIDERS = None

    counter_frame = read_frame(args.counter_video, args.counter_time)
    whole_store_frame = read_frame(args.whole_store_video, args.whole_store_time)

    counter_det = S.Detector()
    counter_emb = S.Embedder()
    till = RC.Till(args.till_model, 640, 0.70, confirm=5)
    ws_det = S.Detector()
    ws_emb = S.Embedder()

    counter_boxes, _ = counter_det(counter_frame)
    ws_boxes, _ = ws_det(whole_store_frame)
    counter_boxes = counter_boxes[: min(len(counter_boxes), 2)]
    ws_boxes = ws_boxes[: min(len(ws_boxes), 2)]

    for _ in range(args.warmup):
        counter_det(counter_frame)
        if len(counter_boxes):
            counter_emb(counter_frame, counter_boxes)
        till(counter_frame)
        ws_det(whole_store_frame)
        if len(ws_boxes):
            ws_emb(whole_store_frame, ws_boxes)

    result = {
        "counter": {
            "pose": bench_pose(counter_det, counter_frame, args.iterations),
            "reid": bench_reid(counter_emb, counter_frame, counter_boxes, args.iterations),
            "till": bench_till(till, counter_frame, args.iterations),
        },
        "whole_store": {
            "pose": bench_pose(ws_det, whole_store_frame, args.iterations),
            "reid": bench_reid(ws_emb, whole_store_frame, ws_boxes, args.iterations),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
