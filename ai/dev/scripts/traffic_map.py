"""Where do people actually walk in these rooms, and where do they appear/vanish?

A doorway shows up two ways: as a corridor of foot traffic, and as the place
detections wink in and out. Both are worth seeing before drawing a line, because
a line in the wrong place makes every later argument about the algorithm moot.
"""
import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[3]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import os
import sys

import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ai.src.perception import stream_track as S
from ai.src.perception import zones

S.MODEL = "models/yolo26m-pose.onnx"
S.CONF = 0.35
ROOT = str(_Path(__file__).resolve().parents[3])
FPS = 5.0                       # enough for a density map, half the compute

det = S.Detector()
for name in ("storage_1", "storage_2"):
    cap = cv2.VideoCapture(f"videos/{name}.mp4")
    src = cap.get(cv2.CAP_PROP_FPS)
    W, H = int(cap.get(3)), int(cap.get(4))
    heat = np.zeros((H, W), np.float32)
    pts, prev_ids = [], 0
    bg = None
    step, acc, n, nppl = FPS / src, 0.0, 0, 0
    while True:
        if not cap.grab():
            break
        acc += step
        if acc < 1.0:
            continue
        acc -= 1.0
        ok, f = cap.retrieve()
        if not ok:
            break
        if bg is None:
            bg = f.copy()
        boxes, kpts = det(f)
        for b, k in zip(boxes, kpts):
            pt, _ = zones.anchor(b[:4], k)
            pts.append((pt[0], pt[1], b[3] - b[1]))
            cv2.circle(heat, (int(pt[0]), int(pt[1])), 26, 1.0, -1)
        nppl += len(boxes)
        n += 1
    cap.release()

    heat = cv2.GaussianBlur(heat, (0, 0), 22)
    if heat.max() > 0:
        heat /= heat.max()
    hm = cv2.applyColorMap((heat * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    out = cv2.addWeighted(bg, 0.55, hm, 0.45, 0)

    P = np.array(pts)
    # the anchors themselves, so the actual sample positions are visible
    for x, y, _h in pts:
        cv2.circle(out, (int(x), int(y)), 3, (255, 255, 255), -1)

    # native resolution on purpose: zone_editor --image refuses anything that
    # does not match the video, since zones are stored in video coordinates
    os.makedirs(f"{ROOT}/ai/dev/outputs/{name}", exist_ok=True)
    cv2.imwrite(f"{ROOT}/ai/dev/outputs/{name}/{name}_traffic.jpg", out)
    print(f"{name}: {n} frames @ {FPS}fps, {nppl} person-detections, "
          f"{len(pts)} anchors")
    if len(P):
        print(f"  pelvis x: p5={np.percentile(P[:,0],5):.0f} "
              f"p50={np.percentile(P[:,0],50):.0f} "
              f"p95={np.percentile(P[:,0],95):.0f}   "
              f"y: p5={np.percentile(P[:,1],5):.0f} "
              f"p50={np.percentile(P[:,1],50):.0f} "
              f"p95={np.percentile(P[:,1],95):.0f}")
        print(f"  box height: p5={np.percentile(P[:,2],5):.0f} "
              f"p50={np.percentile(P[:,2],50):.0f} "
              f"p95={np.percentile(P[:,2],95):.0f}  "
              f"(depth range - small = far from camera)")
