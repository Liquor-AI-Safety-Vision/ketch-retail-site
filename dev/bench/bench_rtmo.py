"""Benchmark RTMO (one-stage, bottom-up) against the same frames as bench_models.

RTMO needs no person detector: it regresses every person's keypoints in a single
pass. That matters here because the failure this benchmark is chasing is a
detection failure, and a top-down pose model cannot fix what the detector never
proposed.

Preprocessing follows rtmlib exactly - pad to the TOP-LEFT with 114, keep BGR,
no mean/std - so any difference measured is the model, not the input pipeline.
The session is rebuilt on the CoreML EP to match how the other models were timed.
"""
import time
import warnings

import cv2
import numpy as np
import onnxruntime as ort
from rtmlib.tools.pose_estimation.rtmo import RTMO

warnings.filterwarnings("ignore")

CONF = 0.35
FRAMES = [0, 500, 1200, 2000, 3000, 3600, 4400, 5100]
TRUTH = {0: 3, 500: 3, 1200: 2, 2000: 1, 3000: 2, 3600: 2, 4400: 4, 5100: 3}
MODELS = [("rtmo-s", "models/rtmo-s.onnx"),
          ("rtmo-m", "models/rtmo-m.onnx"),
          ("rtmo-l", "models/rtmo-l.onnx")]


def build(path):
    m = RTMO(path, model_input_size=(640, 640), score_thr=CONF,
             backend="onnxruntime", device="cpu")
    m.session = ort.InferenceSession(
        path, providers=["CoreMLExecutionProvider", "CPUExecutionProvider"])
    return m


def main():
    imgs = {f: cv2.imread(f"bench/f{f:05d}.jpg") for f in FRAMES}
    hdr = "model    " + "".join(f"{f:>6}" for f in FRAMES) + "   sum   ms   fps"
    print(hdr)
    print("truth    " + "".join(f"{TRUTH[f]:>6}" for f in FRAMES)
          + f"{sum(TRUTH.values()):>6}")
    print("-" * len(hdr))

    out = {}
    for tag, path in MODELS:
        m = build(path)
        counts, ts, keep = [], [], {}
        for f in FRAMES:
            img = imgs[f]
            for _ in range(2):
                kpts, scores = m(img)
            t0 = time.perf_counter()
            for _ in range(4):
                kpts, scores = m(img)
            ts.append((time.perf_counter() - t0) / 4 * 1000)
            counts.append(len(kpts))
            keep[f] = (kpts, scores)
        ms = float(np.mean(ts))
        out[tag] = (counts, ms, keep)
        print(f"{tag:<9}" + "".join(f"{c:>6}" for c in counts)
              + f"{sum(counts):>6}{ms:>6.1f}{1000 / ms:>6.1f}")

    best = max(out, key=lambda t: sum(out[t][0]))
    print(f"\nrendering {best} -> bench/best_{best}.jpg")
    tiles = []
    for f in [0, 500, 4400, 5100]:
        im = imgs[f].copy()
        kpts, scores = out[best][2][f]
        for person, sc in zip(kpts, scores):
            vis = person[sc > 0.3] if sc.ndim else person
            if len(vis):
                x1, y1 = vis.min(0).astype(int)
                x2, y2 = vis.max(0).astype(int)
                cv2.rectangle(im, (x1, y1), (x2, y2), (0, 255, 0), 3)
            for (x, y), c in zip(person, sc):
                if c > 0.3:
                    cv2.circle(im, (int(x), int(y)), 5, (0, 200, 255), -1)
        cv2.putText(im, f"{best}  f{f}  n={len(kpts)} (truth {TRUTH[f]})",
                    (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 255), 4)
        tiles.append(cv2.resize(im, (960, 540)))
    cv2.imwrite(f"bench/best_{best}.jpg",
                np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])]))


if __name__ == "__main__":
    main()
