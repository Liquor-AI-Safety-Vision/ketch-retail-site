"""Run the face detector straight at the entrance video and draw what it finds.

Answers one question before any pipeline work starts: does YOLOv6s-face see
faces in this camera at all, and how big are they? The entrance feed is
1280x1440 -- portrait, unlike every other camera in the system -- so letterboxing
to 640 shrinks it more than the 1920x1080 feeds, and a face that is fine on the
storage cameras may be a handful of pixels here.

Two modes, matching the two approaches under discussion:

  --mode full    detect on the whole frame (approach 2: no person model at all)
  --mode crop    detect inside a region of interest, upscaled first
                 (approach 2 refined: pre-crop the doorway so faces get bigger)

Writes an annotated video plus a stats summary. CPU is fine; this is a
look-at-it tool, not a benchmark.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[3]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = _Path(__file__).resolve().parents[3]

from ai.src.perception.face_align import FaceDetector, align  # noqa: E402
from ai.src.common.paths import model_path, outputs_dir


def draw(frame, dets, offset=(0, 0), scale=1.0):
    ox, oy = offset
    for box, score, lm in dets:
        x1, y1, x2, y2 = [int(v / scale) for v in box]
        x1, y1, x2, y2 = x1 + ox, y1 + oy, x2 + ox, y2 + oy
        w, h = x2 - x1, y2 - y1
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 0), 2)
        cv2.putText(frame, f"{score:.2f} {w}x{h}", (x1, max(0, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 0), 1, cv2.LINE_AA)
        for px, py in lm:
            cv2.circle(frame, (int(px / scale) + ox, int(py / scale) + oy),
                       2, (0, 0, 255), -1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", default=str(ROOT / "data/videos/entrance/entrance_180s.mp4"))
    p.add_argument("--model", default=model_path("yolov6s_face.onnx"))
    p.add_argument("--out", default=str(outputs_dir() / "face_entrance/annotated.mp4"))
    p.add_argument("--conf", type=float, default=0.5)
    p.add_argument("--stride", type=int, default=5,
                   help="process every Nth frame; the point is to look at results, "
                        "not to measure throughput")
    p.add_argument("--max-frames", type=int, default=0, help="0 = whole video")
    p.add_argument("--mode", choices=["full", "crop"], default="full")
    p.add_argument("--roi", default="",
                   help="crop mode: x1,y1,x2,y2 in source pixels")
    p.add_argument("--roi-upscale", type=float, default=2.0,
                   help="crop mode: enlarge the ROI before detection so a distant "
                        "face occupies more of the 640 input")
    p.add_argument("--dump-hits", default="",
                   help="directory to save the best detections as face chips, so the "
                        "actual pixels going into ArcFace can be eyeballed")
    p.add_argument("--dump-top", type=int, default=12,
                   help="how many of the highest-scoring detections to dump")
    args = p.parse_args()

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {args.video}")
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    roi = None
    if args.mode == "crop":
        if args.roi:
            roi = tuple(int(v) for v in args.roi.split(","))
        else:
            # Default guess: middle horizontal band, upper-middle vertically --
            # where someone walking through a doorway usually is. Meant to be
            # replaced with a real zone once we look at the footage.
            roi = (0, int(H * 0.15), W, int(H * 0.65))
        print(f"ROI = {roi}  upscale={args.roi_upscale}")

    det = FaceDetector(args.model, conf=args.conf)
    print(f"video {W}x{H} @ {fps:.1f}fps, {total} frames | mode={args.mode} "
          f"conf={args.conf} stride={args.stride}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"),
                             max(1.0, fps / args.stride), (W, H))

    idx = processed = frames_with_face = n_faces = 0
    sizes, scores, times = [], [], []
    hits = []          # (score, frame_idx, chip_bgr, aligned_bgr) for --dump-hits
    t_start = time.time()
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % args.stride:
            idx += 1
            continue
        ok, frame = cap.retrieve()
        if not ok:
            break

        t0 = time.perf_counter()
        if args.mode == "full":
            dets = det(frame)
            offset, scale = (0, 0), 1.0
            clean = frame.copy() if args.dump_hits else None
        else:
            x1, y1, x2, y2 = roi
            sub = frame[y1:y2, x1:x2]
            if args.roi_upscale != 1.0:
                sub = cv2.resize(sub, None, fx=args.roi_upscale, fy=args.roi_upscale,
                                 interpolation=cv2.INTER_LINEAR)
            dets = det(sub)
            offset, scale = (x1, y1), args.roi_upscale
            clean = sub.copy() if args.dump_hits else None
        times.append((time.perf_counter() - t0) * 1000.0)

        if args.mode == "crop":
            x1, y1, x2, y2 = roi
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 160, 0), 2)
        draw(frame, dets, offset=offset, scale=scale)
        cv2.putText(frame, f"f{idx}  faces={len(dets)}", (12, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
        writer.write(frame)

        processed += 1
        if dets:
            frames_with_face += 1
            n_faces += len(dets)
            for box, score, lm in dets:
                sizes.append(((box[2] - box[0]) / scale, (box[3] - box[1]) / scale))
                scores.append(score)
                if args.dump_hits:
                    # from the untouched copy: draw() has already painted boxes
                    # and landmark dots onto `frame`/`sub`, and chips cut from
                    # those would show the annotation instead of the pixels
                    # ArcFace actually receives.
                    src = clean
                    bx = [int(v) for v in box]
                    x1c, y1c = max(0, bx[0]), max(0, bx[1])
                    x2c, y2c = min(src.shape[1], bx[2]), min(src.shape[0], bx[3])
                    if x2c > x1c and y2c > y1c:
                        chip = src[y1c:y2c, x1c:x2c].copy()
                        aligned = align(src, lm.astype(np.float32), size=112)
                        hits.append((score, idx, chip, aligned))
        idx += 1
        if args.max_frames and processed >= args.max_frames:
            break

    cap.release()
    writer.release()

    print(f"\nprocessed {processed} frames in {time.time()-t_start:.1f}s "
          f"({np.median(times):.0f} ms/frame median)")
    print(f"frames with >=1 face: {frames_with_face}/{processed} "
          f"({100.0*frames_with_face/max(1,processed):.1f}%)")
    print(f"total detections: {n_faces}")
    if sizes:
        a = np.array(sizes)
        s = np.array(scores)
        print(f"face width  px: min {a[:,0].min():.0f}  median {np.median(a[:,0]):.0f}  max {a[:,0].max():.0f}")
        print(f"face height px: min {a[:,1].min():.0f}  median {np.median(a[:,1]):.0f}  max {a[:,1].max():.0f}")
        print(f"score:          min {s.min():.2f}  median {np.median(s):.2f}  max {s.max():.2f}")
        # ArcFace wants a 112x112 chip; anything much under that is being
        # upsampled from nothing and the embedding will be unreliable.
        print(f"faces >= 112px wide: {(a[:,0] >= 112).sum()}/{len(a)}")
        print(f"faces >=  64px wide: {(a[:,0] >=  64).sum()}/{len(a)}")
    if args.dump_hits and hits:
        d = Path(args.dump_hits)
        d.mkdir(parents=True, exist_ok=True)
        hits.sort(key=lambda h: -h[0])
        for rank, (score, fidx, chip, aligned) in enumerate(hits[:args.dump_top]):
            cv2.imwrite(str(d / f"{rank:02d}_f{fidx}_s{score:.2f}_raw.png"), chip)
            if aligned is not None:
                cv2.imwrite(str(d / f"{rank:02d}_f{fidx}_s{score:.2f}_aligned112.png"), aligned)
        # one contact sheet of the aligned chips, which is what ArcFace sees
        al = [h[3] for h in hits[:args.dump_top] if h[3] is not None]
        if al:
            cols = min(6, len(al))
            rows = (len(al) + cols - 1) // cols
            sheet = np.zeros((rows * 112, cols * 112, 3), np.uint8)
            for i, a in enumerate(al):
                r, c = divmod(i, cols)
                sheet[r*112:(r+1)*112, c*112:(c+1)*112] = a
            cv2.imwrite(str(d / "contact_sheet_aligned.png"), sheet)
        print(f"dumped {min(len(hits), args.dump_top)} chips to {d}")

    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
