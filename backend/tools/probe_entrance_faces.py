"""Over a window of frames, crop every person and try to find a face.

Deliberately ignores the zone. demo_entrance_face.py only looks for faces once
a track is confirmed INSIDE, and frame 733 shows why that is worth questioning:
a man is walking in through the door, facing the camera, and the demo reports
faces=0 because his pelvis has not crossed the divider yet. Whether the best
face of a visit arrives BEFORE the zone confirms it is a measurement, not an
opinion, and this makes it.

Dumps every face chip it finds, named by frame and person, plus a per-frame
annotated image, so the answer can be looked at rather than trusted.

    python3 backend/tools/probe_entrance_faces.py --start-s 70 --end-s 80
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import stream_track as S  # noqa: E402
from face_align import FaceDetector  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--video", default=str(ROOT / "videos/entrance/entrance_180s.mp4"))
    p.add_argument("--model", default=str(ROOT / "models/yolo26m-pose.onnx"))
    p.add_argument("--face-model", default=str(ROOT / "models/yolov6s_face.onnx"))
    p.add_argument("--outdir", default=str(ROOT / "outputs/entrance/probe"))
    p.add_argument("--start-s", type=float, default=70.0)
    p.add_argument("--end-s", type=float, default=80.0)
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--conf", type=float, default=0.45)
    p.add_argument("--face-conf", type=float, default=0.25,
                   help="lower than the demo's 0.4 on purpose: the question is "
                        "what the detector CAN see here, and a score that is "
                        "too low to trust is still evidence a face is there")
    p.add_argument("--target", type=int, default=640,
                   help="enlarge the person crop to this on its long side")
    a = p.parse_args()

    S.MODEL, S.CONF = a.model, a.conf
    det = S.Detector()
    face_det = FaceDetector(a.face_model, conf=a.face_conf)

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {a.video}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    H, W = int(cap.get(4)), int(cap.get(3))

    outdir = Path(a.outdir)
    (outdir / "chips").mkdir(parents=True, exist_ok=True)

    step, acc, idx = a.fps / src_fps, 0.0, 0
    rows = []
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

        boxes, _kpts = det(frame)
        vis = frame.copy()
        for j, b in enumerate(boxes):
            x1, y1, x2, y2 = (float(v) for v in b[:4])
            bw, bh = x2 - x1, y2 - y1
            cx1 = int(max(0, x1 - 0.06 * bw))
            cy1 = int(max(0, y1 - 0.06 * bh))
            cx2 = int(min(W, x2 + 0.06 * bw))
            cy2 = int(min(H, y2 + 0.06 * bh))
            crop = frame[cy1:cy2, cx1:cx2]
            if crop.size == 0:
                continue
            s = min(a.target / max(crop.shape[0], crop.shape[1]), 4.0)
            big = cv2.resize(crop, None, fx=s, fy=s) if s > 1.0 else crop
            s = s if s > 1.0 else 1.0

            cv2.rectangle(vis, (int(x1), int(y1)), (int(x2), int(y2)), (90, 240, 90), 2)
            cv2.putText(vis, f"p{j} h={int(bh)}", (int(x1), max(16, int(y1) - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (90, 240, 90), 2)

            found = face_det(big)
            for k, (box, score, _lm) in enumerate(found):
                fx1, fy1, fx2, fy2 = (np.asarray(box, np.float64) / s) + [cx1, cy1, cx1, cy1]
                fh = int(fy2 - fy1)
                rows.append((idx, round(t, 1), j, int(bh), round(float(score), 3),
                             int(fx2 - fx1), fh))
                cv2.rectangle(vis, (int(fx1), int(fy1)), (int(fx2), int(fy2)),
                              (255, 120, 255), 2)
                cv2.putText(vis, f"{score:.2f} {int(fx2-fx1)}x{fh}",
                            (int(fx1), max(12, int(fy1) - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 120, 255), 2)
                chip = frame[max(0, int(fy1)):int(fy2), max(0, int(fx1)):int(fx2)]
                if chip.size:
                    cv2.imwrite(str(outdir / "chips" /
                                    f"f{idx:04d}_p{j}_{k}_s{score:.2f}_{fh}px.png"), chip)
        cv2.imwrite(str(outdir / f"f{idx:04d}_t{t:05.1f}.jpg"), vis)
        idx += 1
    cap.release()

    print(f"window {a.start_s}-{a.end_s}s at {a.fps} fps, "
          f"person conf={a.conf}, face conf={a.face_conf}")
    print(f"{'frame':>6} {'t':>6} {'person':>6} {'box_h':>6} "
          f"{'score':>6} {'face_w':>7} {'face_h':>7}")
    for r in rows:
        print(f"{r[0]:6} {r[1]:6.1f} {r[2]:6} {r[3]:6} {r[4]:6.3f} {r[5]:7} {r[6]:7}")
    if rows:
        hs = np.array([r[6] for r in rows])
        print(f"\n{len(rows)} faces | height min {hs.min()} p50 "
              f"{int(np.percentile(hs, 50))} max {hs.max()}  (ArcFace wants >=112)")
    else:
        print("\nno faces at all in this window")
    print(f"\nannotated frames + chips -> {a.outdir}")


if __name__ == "__main__":
    main()
