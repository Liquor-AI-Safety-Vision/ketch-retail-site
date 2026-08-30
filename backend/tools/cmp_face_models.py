"""yolov6s-face vs yolov6m-face on the entrance footage, same crops, same frames.

The person detector runs once per frame and both face models are given the
identical person crops, so any difference in the numbers is the face model and
nothing else -- not a different set of people, not a different crop scale.

Reports per window: how many faces each model finds, at what score, at what
size, and how many clear the demo's 0.4 threshold. Chips go to disk side by
side, because the count that matters in the end is how many of them a human
can recognise, and no metric here measures that.

    python3 backend/tools/cmp_face_models.py --start-s 39 --end-s 47
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import stream_track as S  # noqa: E402
from face_align import FaceDetector  # noqa: E402


def crops_for(frame, boxes, pad=0.06, target=640):
    H, W = frame.shape[:2]
    out = []
    for b in boxes:
        x1, y1, x2, y2 = (float(v) for v in b[:4])
        bw, bh = x2 - x1, y2 - y1
        cx1, cy1 = int(max(0, x1 - pad * bw)), int(max(0, y1 - pad * bh))
        cx2, cy2 = int(min(W, x2 + pad * bw)), int(min(H, y2 + pad * bh))
        crop = frame[cy1:cy2, cx1:cx2]
        if crop.size == 0 or cx2 - cx1 < 16 or cy2 - cy1 < 16:
            continue
        s = min(target / max(crop.shape[0], crop.shape[1]), 4.0)
        big = cv2.resize(crop, None, fx=s, fy=s) if s > 1.0 else crop
        out.append((big, max(s, 1.0), cx1, cy1))
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--video", default=str(ROOT / "videos/entrance/entrance_180s.mp4"))
    p.add_argument("--model", default=str(ROOT / "models/yolo26s-pose.onnx"))
    p.add_argument("--start-s", type=float, default=39.0)
    p.add_argument("--end-s", type=float, default=47.0)
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--conf", type=float, default=0.45)
    p.add_argument("--face-conf", type=float, default=0.15,
                   help="low, so the comparison sees what each model CAN find; "
                        "the 0.4 the demo uses is reported separately")
    p.add_argument("--demo-conf", type=float, default=0.4)
    p.add_argument("--outdir", default=str(ROOT / "outputs/entrance/cmp_face"))
    a = p.parse_args()

    S.MODEL, S.CONF = a.model, a.conf
    det = S.Detector()
    models = {
        "yolov6s": FaceDetector(str(ROOT / "models/yolov6s_face.onnx"), conf=a.face_conf),
        "yolov6m": FaceDetector(str(ROOT / "models/yolov6m_face.onnx"), conf=a.face_conf),
    }
    stats = {k: {"n": 0, "scores": [], "heights": [], "ms": 0.0, "calls": 0}
             for k in models}
    for k in models:
        (Path(a.outdir) / k).mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(a.video)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    step, acc, idx, n_crops = a.fps / src_fps, 0.0, 0, 0
    while True:
        if not cap.grab():
            break
        acc += step
        if acc < 1.0:
            continue
        acc -= 1.0
        t = idx / a.fps
        if t > a.end_s:
            break
        if t < a.start_s:
            idx += 1
            continue
        ok, frame = cap.retrieve()
        if not ok:
            break

        boxes, _ = det(frame)
        for j, (big, s, cx1, cy1) in enumerate(crops_for(frame, boxes)):
            n_crops += 1
            for name, fd in models.items():
                t0 = time.perf_counter()
                found = fd(big)
                stats[name]["ms"] += (time.perf_counter() - t0) * 1000
                stats[name]["calls"] += 1
                for box, score, _lm in found:
                    fx1, fy1, fx2, fy2 = (np.asarray(box, np.float64) / s) + [cx1, cy1, cx1, cy1]
                    h = int(fy2 - fy1)
                    stats[name]["n"] += 1
                    stats[name]["scores"].append(float(score))
                    stats[name]["heights"].append(h)
                    chip = frame[max(0, int(fy1)):int(fy2), max(0, int(fx1)):int(fx2)]
                    if chip.size and score >= a.demo_conf:
                        cv2.imwrite(str(Path(a.outdir) / name /
                                        f"f{idx:04d}_p{j}_s{score:.2f}_{h}px.png"), chip)
        idx += 1
    cap.release()

    print(f"window {a.start_s}-{a.end_s}s @ {a.fps} fps | {n_crops} person crops | "
          f"face conf floor {a.face_conf}")
    print(f"\n{'model':9} {'faces':>6} {'>=' + str(a.demo_conf):>7} "
          f"{'score p50':>10} {'score max':>10} {'h p50':>6} {'h max':>6} {'ms/crop':>8}")
    for name, s in stats.items():
        sc = np.asarray(s["scores"]) if s["scores"] else np.zeros(1)
        hs = np.asarray(s["heights"]) if s["heights"] else np.zeros(1)
        over = int((sc >= a.demo_conf).sum()) if s["scores"] else 0
        print(f"{name:9} {s['n']:6} {over:7} {np.percentile(sc, 50):10.3f} "
              f"{sc.max():10.3f} {int(np.percentile(hs, 50)):6} {int(hs.max()):6} "
              f"{s['ms'] / max(1, s['calls']):8.1f}")
    print(f"\nchips (>= {a.demo_conf}) -> {a.outdir}/<model>/")


if __name__ == "__main__":
    main()
