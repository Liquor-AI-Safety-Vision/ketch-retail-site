"""Do these entrance faces carry an identity at all?

Collects every face the demo would accept -- same person detector, same face
detector, same position rule -- keeps the highest-confidence one per track,
aligns and embeds all of them with ArcFace, then asks the only question that
decides whether a watchlist is possible here:

    is a face closer to ANOTHER face of the same person
    than it is to a face of a DIFFERENT person?

If the within-track spread overlaps the between-track spread, the embeddings
carry no identity at this resolution and no threshold, no averaging and no
gallery tuning can recover one. Measured, not argued.

The best chip per track is also written to a single sheet so the numbers can be
checked against what they are numbers about.

    python3 ai/tools/probe_entrance_identity.py
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[3]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = _Path(__file__).resolve().parents[3]
for p in (str(ROOT),):
    if p not in sys.path:
        sys.path.insert(0, p)

from ai.src.perception import stream_track as S  # noqa: E402
from ai.src.perception import warehouse  # noqa: E402
from ai.src.perception.face_align import ARCFACE_112, FaceDetector, align  # noqa: E402


def umeyama(src, dst):
    """Least-squares similarity transform over ALL the points.

    What InsightFace uses, and what align() does not: align() asks
    estimateAffinePartial2D with method=LMEDS, a robust estimator that discards
    points it judges to be outliers. With five correspondences the median
    residual is the third smallest, so a transform that fits three landmarks
    and grossly misses two scores a near-zero median and wins. None of the five
    is an outlier -- they are all real measurements -- so rejecting any of them
    is throwing away signal.
    """
    src, dst = np.asarray(src, np.float64), np.asarray(dst, np.float64)
    sm, dm = src.mean(0), dst.mean(0)
    sd, dd = src - sm, dst - dm
    A = dd.T @ sd / len(src)
    U, S, Vt = np.linalg.svd(A)
    D = np.eye(2)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        D[1, 1] = -1
    R = U @ D @ Vt
    scale = float(np.trace(np.diag(S) @ D) / max(1e-12, (sd ** 2).sum() / len(src)))
    t = dm - scale * (R @ sm)
    return np.hstack([scale * R, t.reshape(2, 1)]).astype(np.float32)


def align_umeyama(frame, lm, size=112):
    M = umeyama(lm, ARCFACE_112)
    return cv2.warpAffine(frame, M, (size, size), borderValue=0)
from ai.src.perception.face_identity import ArcFaceEmbedder  # noqa: E402
from ai.src.perception.uae_tracker import Detection, Tracker  # noqa: E402
from ai.src.common.paths import model_path, outputs_dir


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--video", default=str(ROOT / "data/videos/entrance/entrance_180s.mp4"))
    p.add_argument("--zone", default=str(outputs_dir() / "zones/entrance_zone.json"))
    p.add_argument("--model", default=model_path("yolo26s-pose.onnx"))
    p.add_argument("--face-model", default=model_path("yolov6s_face.onnx"))
    p.add_argument("--arcface", default=model_path("ms1mv3_arcface_r18.onnx"))
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--conf", type=float, default=0.45)
    p.add_argument("--face-conf", type=float, default=0.4)
    p.add_argument("--min-face-h", type=int, default=32)
    p.add_argument("--face-top-frac", type=float, default=0.35)
    p.add_argument("--entry-s", type=float, default=0.5)
    p.add_argument("--sheet", default=str(outputs_dir() / "entrance/identity_sheet.png"))
    a = p.parse_args()

    import json
    import math

    cfg = warehouse.Config(fps=a.fps, anchor="pelvis")
    cfg.person_conf = a.conf
    cfg.entry_confirmation_s = a.entry_s
    cfg.__post_init__()
    cfg.entry_frames = max(1, math.ceil(a.entry_s * a.fps))
    cfg.min_box_h_frac = 0.0
    S.MODEL, S.CONF = a.model, cfg.person_conf

    cap = cv2.VideoCapture(a.video)
    W, H = int(cap.get(3)), int(cap.get(4))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    zone_norm = json.loads(Path(a.zone).read_text())["entrance"]["zone_norm"]

    det, emb_reid = S.Detector(), S.Embedder()
    fd = FaceDetector(a.face_model, conf=a.face_conf)
    arc = ArcFaceEmbedder(a.arcface)
    tracker = Tracker(img_h=H, img_w=W, fps=cfg.fps)
    mon = warehouse.ZoneMonitor(zone_norm, W, H, cfg)

    # track_id -> list of (score, embedding, chip)
    per_track: dict[int, list] = defaultdict(list)
    came_from_outside: set[int] = set()

    step, acc, idx = cfg.fps / src_fps, 0.0, 0
    while True:
        if not cap.grab():
            break
        acc += step
        if acc < 1.0:
            continue
        acc -= 1.0
        ok, frame = cap.retrieve()
        if not ok:
            break

        boxes, kpts = det(frame)
        feats = emb_reid(frame, boxes)
        live, removed = tracker.update(
            [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i])
             for i in range(len(boxes))], idx)
        mon.drop(removed, idx)
        items = mon.update([(t.track_id, t.ltrb, t.kpts, src == "detected")
                            for t, src in live], idx)

        for tid, ltrb, _k, st, _pt, _s in items:
            if st == warehouse.OUTSIDE:
                came_from_outside.add(tid)
            elif tid not in came_from_outside:
                continue
            x1, y1, x2, y2 = ltrb
            bw, bh = x2 - x1, y2 - y1
            cx1, cy1 = int(max(0, x1 - .06 * bw)), int(max(0, y1 - .06 * bh))
            cx2, cy2 = int(min(W, x2 + .06 * bw)), int(min(H, y2 + .06 * bh))
            crop = frame[cy1:cy2, cx1:cx2]
            if crop.size == 0 or cx2 - cx1 < 16 or cy2 - cy1 < 16:
                continue
            s = min(640 / max(crop.shape[0], crop.shape[1]), 4.0)
            big = cv2.resize(crop, None, fx=s, fy=s) if s > 1 else crop
            s = max(s, 1.0)
            for box, score, lm in fd(big):
                fb = (np.asarray(box, np.float64) / s) + [cx1, cy1, cx1, cy1]
                flm = (np.asarray(lm, np.float64) / s) + [cx1, cy1]
                if fb[3] - fb[1] < a.min_face_h:
                    continue
                if ((fb[1] + fb[3]) / 2 - y1) / max(1e-6, bh) > a.face_top_frac:
                    continue
                # aligned exactly the way face_identity does it, so these
                # embeddings are the ones the gallery would actually see
                chip = align(frame, flm.astype(np.float32), size=112)
                if chip is None:
                    continue
                chip_u = align_umeyama(frame, flm.astype(np.float32), size=112)
                # keep the unaligned crop too: an aligned chip that looks
                # wrong could be a bad frame or a bad warp, and only the
                # pair tells them apart
                raw = frame[max(0, int(fb[1])):int(fb[3]),
                            max(0, int(fb[0])):int(fb[2])].copy()
                per_track[tid].append((float(score), arc(chip), chip, raw,
                                       arc(chip_u), chip_u))
        idx += 1
    cap.release()

    tracks = {t: v for t, v in per_track.items() if v}
    EMB = {"LMEDS (current align)": 1, "umeyama (all 5 points)": 4}
    print(f"tracks with at least one accepted face: {len(tracks)}")
    print(f"{'track':>6} {'faces':>6} {'best':>6} {'within-track cos':>18}")
    best_of = {}
    for tid, lst in sorted(tracks.items()):
        lst.sort(key=lambda r: -r[0])
        best_of[tid] = lst[0]
        e0 = lst[0][1]
        sims = [float(np.dot(e0, e[1])) for e in lst[1:]]
        rng = (f"{min(sims):.3f}..{max(sims):.3f}" if sims else "n/a (1 face)")
        print(f"{tid:6} {len(lst):6} {lst[0][0]:6.2f} {rng:>18}")

    for label, slot in EMB.items():
        within, ids = [], sorted(best_of)
        for tid in ids:
            lst = tracks[tid]
            e0 = lst[0][slot]
            within += [float(np.dot(e0, e[slot])) for e in lst[1:]]
        E = np.stack([best_of[t][slot] for t in ids])
        M = E @ E.T
        between = [float(M[i, j]) for i in range(len(ids)) for j in range(i + 1, len(ids))]
        w, b = np.asarray(within), np.asarray(between)
        print(f"\n{label}")
        print(f"  within  (same person)      n={len(w):4}  min {w.min():6.3f}  "
              f"p50 {np.percentile(w, 50):6.3f}  max {w.max():6.3f}")
        print(f"  between (different people) n={len(b):4}  min {b.min():6.3f}  "
              f"p50 {np.percentile(b, 50):6.3f}  max {b.max():6.3f}")
        print(f"  gap {w.min() - b.max():+.3f}  "
              f"({'SEPARATED' if w.min() > b.max() else 'OVERLAPPING'})   "
              f"different-person pairs above p10 of same-person: "
              f"{int((b >= np.percentile(w, 10)).sum())}/{len(b)}")
    within = []

    ids = sorted(best_of)
    E = np.stack([best_of[t][1] for t in ids])
    Mat = E @ E.T
    between = [float(Mat[i, j]) for i in range(len(ids)) for j in range(i + 1, len(ids))]

    print("\ncosine similarity, ArcFace r18, aligned 112x112")
    for label, arr in (("within  track (same person)", within),
                       ("between tracks (different people)", between)):
        if not arr:
            print(f"  {label:34} n=0")
            continue
        v = np.asarray(arr)
        print(f"  {label:34} n={len(v):4}  min {v.min():6.3f}  "
              f"p50 {np.percentile(v, 50):6.3f}  max {v.max():6.3f}")
    if within and between:
        w, b = np.asarray(within), np.asarray(between)
        print(f"\n  gap between the two: {w.min() - b.max():+.3f}  "
              f"({'SEPARATED' if w.min() > b.max() else 'OVERLAPPING'})")
        print(f"  overlap: {int((b >= np.percentile(w, 10)).sum())} of {len(b)} "
              f"different-person pairs score above the 10th percentile of "
              "same-person pairs")

    print("\nbetween-track matrix (best face of each track):")
    print("        " + "".join(f"{t:7}" for t in ids))
    for i, t in enumerate(ids):
        print(f"  {t:5} " + "".join(f"{Mat[i, j]:7.2f}" for j in range(len(ids))))

    cells = []
    for tid in ids:
        score, _e, chip, raw, _eu, chip_u = best_of[tid]
        r = cv2.resize(raw, (170, 170), interpolation=cv2.INTER_NEAREST) \
            if raw.size else np.zeros((170, 170, 3), np.uint8)
        c = np.hstack([r,
                       cv2.resize(chip, (170, 170), interpolation=cv2.INTER_CUBIC),
                       cv2.resize(chip_u, (170, 170), interpolation=cv2.INTER_CUBIC)])
        cv2.rectangle(c, (0, 150), (510, 170), (0, 0, 0), -1)
        cv2.putText(c, f"#{tid} s{score:.2f} n{len(tracks[tid])}", (4, 165),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        cells.append(c)
    while len(cells) % 2:
        cells.append(np.zeros((170, 510, 3), np.uint8))
    sheet = np.vstack([np.hstack(cells[i:i + 2]) for i in range(0, len(cells), 2)])
    Path(a.sheet).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(a.sheet, sheet)
    print(f"\naligned best chip per track -> {a.sheet}")


if __name__ == "__main__":
    main()
