"""Do the 5 face landmarks separate a real face from the back of a head?

The position filter failed: every face box sits at 0.08-0.17 of the person
box, real ones and junk alike, because the detector is firing on the head
either way -- the head is simply turned away. Landmarks are the next candidate,
on the theory that a back of a head has no valid eye/nose/mouth arrangement.

That theory is not obviously true. The detector emits five points whatever is
in the box, and at 50 px they are blurred enough that a nonsense arrangement
could still look plausible. So this measures it on the seven chips the demo
actually chose, where the answer is already known by eye: tracks 2, 4 and 14
are faces, tracks 7, 10, 34 and 38 are not.

If the geometry does not separate those two groups, the idea is dead and no
amount of threshold picking will revive it.

    python3 backend/tools/probe_face_landmarks.py
"""
from __future__ import annotations

import argparse
import json
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

# yolov6-face landmark order
L_EYE, R_EYE, NOSE, L_MOUTH, R_MOUTH = range(5)


def features(box, lm):
    """Geometry that a real frontal-ish face satisfies and a hairline does not."""
    x1, y1, x2, y2 = box
    w, h = max(1e-6, x2 - x1), max(1e-6, y2 - y1)
    p = np.asarray(lm, np.float64)

    inside = int(np.all((p[:, 0] >= x1 - 2) & (p[:, 0] <= x2 + 2)
                        & (p[:, 1] >= y1 - 2) & (p[:, 1] <= y2 + 2)))
    eye_sep = float(np.linalg.norm(p[R_EYE] - p[L_EYE])) / w
    eye_mid = (p[L_EYE] + p[R_EYE]) / 2
    mouth_mid = (p[L_MOUTH] + p[R_MOUTH]) / 2
    # vertical ordering, as a fraction of box height: positive means the pair
    # is in the expected order
    eye_to_nose = float(p[NOSE][1] - eye_mid[1]) / h
    nose_to_mouth = float(mouth_mid[1] - p[NOSE][1]) / h
    # how far the nose sits off the eye midline, relative to eye separation:
    # near 0 for a frontal face, large when the arrangement is nonsense
    denom = max(1e-6, float(np.linalg.norm(p[R_EYE] - p[L_EYE])))
    yaw = abs(float(p[NOSE][0] - eye_mid[0])) / denom
    ordered = int(eye_to_nose > 0 and nose_to_mouth > 0)
    return dict(inside=inside, eye_sep=eye_sep, eye_to_nose=eye_to_nose,
                nose_to_mouth=nose_to_mouth, yaw=yaw, ordered=ordered)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--video", default=str(ROOT / "videos/entrance/entrance_180s.mp4"))
    ap.add_argument("--captures", default=str(ROOT / "outputs/entrance/entrance_captures.jsonl"))
    ap.add_argument("--model", default=str(ROOT / "models/yolo26s-pose.onnx"))
    ap.add_argument("--face-model", default=str(ROOT / "models/yolov6s_face.onnx"))
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--conf", type=float, default=0.45)
    ap.add_argument("--face-conf", type=float, default=0.4)
    ap.add_argument("--out", default=str(ROOT / "outputs/entrance/landmarks.png"))
    # by eye, from outputs/entrance/best_faces/montage.png
    ap.add_argument("--faces", default="2,4,14", help="track ids that ARE faces")
    a = ap.parse_args()

    truth = {int(v) for v in a.faces.split(",") if v.strip()}
    caps = [json.loads(l) for l in open(a.captures)]
    caps = [c for c in caps if "chip" in c]
    want = {c["face_frame"]: c for c in caps}

    S.MODEL, S.CONF = a.model, a.conf
    det = S.Detector()
    fd = FaceDetector(a.face_model, conf=a.face_conf)

    cap = cv2.VideoCapture(a.video)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    step, acc, idx = a.fps / src_fps, 0.0, 0
    rows, panels = [], []

    while want:
        if not cap.grab():
            break
        acc += step
        if acc < 1.0:
            continue
        acc -= 1.0
        if idx not in want:
            idx += 1
            continue
        ok, frame = cap.retrieve()
        idx_now, rec = idx, want.pop(idx)
        idx += 1
        if not ok:
            continue

        H, W = frame.shape[:2]
        target = (rec["face_w"], rec["face_h"])
        best = None
        for b in det(frame)[0]:
            x1, y1, x2, y2 = (float(v) for v in b[:4])
            bw, bh = x2 - x1, y2 - y1
            cx1, cy1 = int(max(0, x1 - .06 * bw)), int(max(0, y1 - .06 * bh))
            cx2, cy2 = int(min(W, x2 + .06 * bw)), int(min(H, y2 + .06 * bh))
            crop = frame[cy1:cy2, cx1:cx2]
            if crop.size == 0:
                continue
            s = min(640 / max(crop.shape[0], crop.shape[1]), 4.0)
            big = cv2.resize(crop, None, fx=s, fy=s) if s > 1 else crop
            s = max(s, 1.0)
            for box, score, lm in fd(big):
                fb = (np.asarray(box, np.float64) / s) + [cx1, cy1, cx1, cy1]
                flm = (np.asarray(lm, np.float64) / s) + [cx1, cy1]
                d = abs((fb[2] - fb[0]) - target[0]) + abs((fb[3] - fb[1]) - target[1])
                if best is None or d < best[0]:
                    best = (d, fb, flm, float(score))
        if best is None:
            print(f"! frame {idx_now}: could not re-find the recorded face")
            continue

        _d, fb, flm, score = best
        f = features(fb, flm)
        f.update(track=rec["track_id"], t=rec["t"], score=round(score, 2),
                 is_face=rec["track_id"] in truth)
        rows.append(f)

        pad = 6
        x1, y1, x2, y2 = (int(v) for v in fb)
        chip = frame[max(0, y1 - pad):y2 + pad, max(0, x1 - pad):x2 + pad].copy()
        if chip.size:
            sc = 190 / max(1, chip.shape[0])
            chip = cv2.resize(chip, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
            cols = [(80, 200, 255), (80, 200, 255), (60, 240, 60), (255, 140, 255), (255, 140, 255)]
            for k, (px, py) in enumerate(flm):
                cv2.circle(chip, (int((px - max(0, x1 - pad)) * sc),
                                  int((py - max(0, y1 - pad)) * sc)), 3, cols[k], -1)
            lab = f"#{rec['track_id']} {'FACE' if f['is_face'] else 'junk'}"
            cv2.rectangle(chip, (0, chip.shape[0] - 18), (chip.shape[1], chip.shape[0]), (0, 0, 0), -1)
            cv2.putText(chip, lab, (3, chip.shape[0] - 5), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (255, 255, 255), 1)
            panels.append(cv2.resize(chip, (150, 190)))
    cap.release()

    print(f"{'track':>6} {'t':>7} {'truth':>6} {'score':>6} {'in':>3} "
          f"{'eye_sep':>8} {'eye>nose':>9} {'nose>mouth':>11} {'yaw':>6} {'ord':>4}")
    for r in sorted(rows, key=lambda r: not r["is_face"]):
        print(f"{r['track']:6} {r['t']:7.1f} {'FACE' if r['is_face'] else 'junk':>6} "
              f"{r['score']:6.2f} {r['inside']:3} {r['eye_sep']:8.3f} "
              f"{r['eye_to_nose']:9.3f} {r['nose_to_mouth']:11.3f} {r['yaw']:6.2f} "
              f"{r['ordered']:4}")

    faces = [r for r in rows if r["is_face"]]
    junk = [r for r in rows if not r["is_face"]]
    print("\nseparation (face vs junk), per feature:")
    for k in ("eye_sep", "eye_to_nose", "nose_to_mouth", "yaw"):
        fv = np.array([r[k] for r in faces]) if faces else np.zeros(1)
        jv = np.array([r[k] for r in junk]) if junk else np.zeros(1)
        gap = "SEPARATES" if (fv.min() > jv.max() or fv.max() < jv.min()) else "overlaps"
        print(f"  {k:14} face [{fv.min():.3f}, {fv.max():.3f}]  "
              f"junk [{jv.min():.3f}, {jv.max():.3f}]   -> {gap}")

    if panels:
        while len(panels) % 4:
            panels.append(np.zeros((190, 150, 3), np.uint8))
        img = np.vstack([np.hstack(panels[i:i + 4]) for i in range(0, len(panels), 4)])
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(a.out, img)
        print(f"\nlandmarks drawn -> {a.out}  (blue=eyes, green=nose, pink=mouth)")


if __name__ == "__main__":
    main()
