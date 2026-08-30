"""Where does the entrance divider have to sit, if the anchor is the pelvis?

The entrance camera looks through a glass storefront onto a parking lot, so a
person detector sees people who are outside the shop. A zone is meant to
separate them -- but the zone test is only as good as the point it tests, and
scripts/zones.py already settled that question for this project: the pelvis,
never the feet, because legs are occluded far too often and extrapolating down
to the floor lands outside the person's own box in 45% of frames.

The consequence is that the divider is a PELVIS-height boundary. The line drawn
in zone_editor was placed at the bottom of the door, which is floor height, so
it is not obviously in the right place any more. This measures that: it prints
where pelvises actually land for people inside vs outside, so the line can be
moved on evidence rather than on the drawing.

    python3 backend/tools/diag_entrance_zone.py --stride 15
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[2]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = _Path(__file__).resolve().parents[2]
for p in (str(ROOT),):
    if p not in sys.path:
        sys.path.insert(0, p)

from backend.src.perception import zones  # noqa: E402
from backend.src.common.paths import model_path


def load_zone(path: Path, W: int, H: int) -> np.ndarray:
    cfg = json.loads(path.read_text())
    entry = next(iter(cfg.values()))
    return (np.asarray(entry["zone_norm"], np.float32) * (W, H)).astype(np.float32)


def anchors_for(ltrb, kpts):
    """The production anchor, plus the two it was chosen over."""
    frac, src = zones.anchor_frac(ltrb, kpts)
    if frac is None:
        frac, src = zones.BOX_CENTRE_FRAC, "box"
    return {
        "pelvis": (zones.point_from_frac(ltrb, frac), src),
        "foot": (zones.point_from_frac(ltrb, zones.BOTTOM_CENTRE_FRAC), "box"),
        "head": (zones.point_from_frac(ltrb, (0.5, 0.0)), "box"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--video", default="videos/entrance/entrance_180s.mp4")
    ap.add_argument("--zone", default="outputs/zones/entrance_zone.json")
    ap.add_argument("--weights", default=model_path("yolo26m-pose.pt"))
    ap.add_argument("--stride", type=int, default=15, help="1 = every frame")
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--out", default="outputs/zones/entrance_zone_anchors.jpg")
    a = ap.parse_args()

    from ultralytics import YOLO

    cap = cv2.VideoCapture(str(ROOT / a.video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {a.video}")
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    zone = load_zone(ROOT / a.zone, W, H)
    model = YOLO(str(ROOT / a.weights))

    names = ("pelvis", "foot", "head")
    inside = {k: 0 for k in names}
    ys: dict[str, list[float]] = {k: [] for k in names}
    src_counts: dict[str, int] = {}
    rows = []
    frames = 0

    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % a.stride == 0:
            frames += 1
            res = model.predict(frame, conf=a.conf, classes=[0], verbose=False)[0]
            xyxy = res.boxes.xyxy.cpu().numpy()
            kp = res.keypoints.data.cpu().numpy() if res.keypoints is not None else None
            for i, b in enumerate(xyxy):
                ltrb = [float(v) for v in b]
                k = kp[i] if kp is not None and i < len(kp) else None
                pts = anchors_for(ltrb, k)
                src_counts[pts["pelvis"][1]] = src_counts.get(pts["pelvis"][1], 0) + 1
                row = {"ltrb": ltrb}
                for name in names:
                    p, _ = pts[name]
                    ys[name].append(float(p[1]))
                    hit = cv2.pointPolygonTest(zone, (float(p[0]), float(p[1])), False) >= 0
                    inside[name] += int(hit)
                    row[name] = (float(p[0]), float(p[1]), hit)
                rows.append(row)
        idx += 1
    cap.release()

    n = len(rows)
    print(f"{a.video}: {total} frames, sampled {frames} at stride {a.stride}")
    print(f"person detections: {n}")
    if not n:
        raise SystemExit("no people found - lower --conf or shorten --stride")
    print(f"pelvis anchor came from: {src_counts}\n")

    print(f"{'anchor':8} {'inside':>7} {'outside':>8} {'% in':>7}   "
          f"{'y p10':>7} {'y p50':>7} {'y p90':>7}")
    for name in names:
        i, arr = inside[name], np.asarray(ys[name])
        print(f"{name:8} {i:7} {n - i:8} {i / n * 100:6.1f}%   "
              f"{np.percentile(arr, 10):7.0f} {np.percentile(arr, 50):7.0f} "
              f"{np.percentile(arr, 90):7.0f}")

    cap = cv2.VideoCapture(str(ROOT / a.video))
    cap.set(cv2.CAP_PROP_POS_FRAMES, total // 3)
    _, canvas = cap.read()
    cap.release()
    ov = canvas.copy()
    cv2.fillPoly(ov, [zone.astype(np.int32)], (70, 210, 70))
    canvas = cv2.addWeighted(ov, 0.25, canvas, 0.75, 0)
    cv2.polylines(canvas, [zone.astype(np.int32)], True, (255, 255, 255), 3)
    for r in rows:
        x1, y1, x2, y2 = r["ltrb"]
        px, py, hit = r["pelvis"]
        col = (90, 240, 90) if hit else (60, 60, 240)
        cv2.rectangle(canvas, (int(x1), int(y1)), (int(x2), int(y2)), col, 2)
        cv2.circle(canvas, (int(px), int(py)), 6, col, -1)
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), canvas)
    print(f"\nwrote {a.out}  (dot = pelvis; green inside, red outside)")


if __name__ == "__main__":
    main()
