"""Compare two till-segmentation models frame by frame on the same footage.

Speed was settled elsewhere (the nano export is 3.3-5x faster raw, 10.5%
end to end). This asks the question that actually decides whether it can
replace v4: does it call the drawer's state correctly?

There is a documented baseline to beat, from the comment on --till in
scripts/run_counter.py: over the first 25 s of cash_till_open_1 the left
drawer is closed the whole time, and v4 called it Open in 0 of 249 frames
while v2 called it Open in 84%. A replacement that regresses that number is
not a replacement, however fast it is.

Runs both models over the same frames, writes a side-by-side video, and
reports per-class frame counts and the frames where the two disagree --
disagreements are where a human should look.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[2]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = _Path(__file__).resolve().parents[2]

from backend.src.perception import stream_track as S  # noqa: E402
from backend.src.perception.run_counter import Till  # noqa: E402
from backend.src.common.paths import model_path

COLOR = {"Open": (0, 0, 235), "Close": (0, 200, 0)}


def raw_states(till_model, frame):
    """Raw per-frame detections, bypassing the confirm/debounce registry.

    The registry exists to stop a flickering model from flipping the reported
    state; running through it here would hide exactly the flicker being
    measured.
    """
    # Till.__call__ returns (class_name, score, polygon) per detection.
    return [(str(name), float(score), poly) for name, score, poly in till_model(frame)]


def annotate(img, dets, label):
    for raw, score, poly in dets:
        c = COLOR.get(raw, (200, 200, 200))
        ov = img.copy()
        cv2.fillPoly(ov, [poly], c)
        cv2.addWeighted(ov, 0.30, img, 0.70, 0, img)
        cv2.polylines(img, [poly], True, c, 2)
        x, y = int(poly[:, 0].min()), int(poly[:, 1].min())
        cv2.putText(img, f"{raw} {score:.2f}", (x + 4, max(18, y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, c, 2, cv2.LINE_AA)
    cv2.putText(img, label, (14, 34), cv2.FONT_HERSHEY_SIMPLEX,
                0.9, (255, 255, 255), 2, cv2.LINE_AA)
    return img


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--model-a", default=model_path("till_seg_v4_640.onnx"),
                   help="baseline (v4 medium)")
    p.add_argument("--model-b", default=model_path("till_seg_n640_last.onnx"),
                   help="candidate (nano)")
    p.add_argument("--label-a", default="v4 medium")
    p.add_argument("--label-b", default="nano")
    p.add_argument("--conf", type=float, default=0.70,
                   help="matches run_counter.py's --till-conf default")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--stride", type=int, default=2)
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--limit-seconds", type=float, default=0.0,
                   help="stop after this many seconds of source footage; use 25 "
                        "to reproduce the documented cash_till_open_1 baseline")
    p.add_argument("--out", default="")
    args = p.parse_args()

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {args.video}")
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    limit_idx = int(args.limit_seconds * fps) if args.limit_seconds else 0

    a = Till(args.model_a, args.imgsz, args.conf)
    b = Till(args.model_b, args.imgsz, args.conf)
    print(f"{Path(args.video).name}: {W}x{H} @ {fps:.1f}fps | conf={args.conf} "
          f"stride={args.stride} | A={args.label_a} B={args.label_b} "
          f"| provider={a.provider}")

    stem = Path(args.video).stem
    out_path = Path(args.out) if args.out else ROOT / f"outputs/till_quality/{stem}_ab.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # side by side, downscaled so the file stays viewable
    ow = 960
    oh = int(H * (ow / 2) / W) if W else 540
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"),
                             max(1.0, fps / args.stride), (ow, oh))

    idx = processed = 0
    cnt = {"A": {}, "B": {}}
    disagree = []
    ta, tb = [], []
    t_start = time.time()
    while True:
        if not cap.grab():
            break
        if limit_idx and idx >= limit_idx:
            break
        if idx % args.stride:
            idx += 1
            continue
        ok, frame = cap.retrieve()
        if not ok:
            break

        t0 = time.perf_counter()
        da = raw_states(a, frame)
        t1 = time.perf_counter()
        db = raw_states(b, frame)
        t2 = time.perf_counter()
        ta.append((t1 - t0) * 1000)
        tb.append((t2 - t1) * 1000)

        sa = sorted(r for r, _s, _p in da)
        sb = sorted(r for r, _s, _p in db)
        for r in sa:
            cnt["A"][r] = cnt["A"].get(r, 0) + 1
        for r in sb:
            cnt["B"][r] = cnt["B"].get(r, 0) + 1
        if sa != sb:
            disagree.append((idx, sa, sb))

        ia = annotate(frame.copy(), da, f"{args.label_a}")
        ib = annotate(frame.copy(), db, f"{args.label_b}")
        half = (ow // 2, oh)
        writer.write(np.hstack([cv2.resize(ia, half), cv2.resize(ib, half)]))

        processed += 1
        idx += 1
        if args.max_frames and processed >= args.max_frames:
            break

    cap.release()
    writer.release()

    print(f"\nprocessed {processed} frames in {time.time()-t_start:.1f}s "
          f"| A {np.median(ta):.0f} ms/frame, B {np.median(tb):.0f} ms/frame")
    print(f"\n{'state':<10} {args.label_a:>12} {args.label_b:>12}")
    for k in sorted(set(cnt['A']) | set(cnt['B'])):
        print(f"{k:<10} {cnt['A'].get(k,0):>12} {cnt['B'].get(k,0):>12}")
    frames_a = sum(cnt["A"].values())
    frames_b = sum(cnt["B"].values())
    print(f"{'TOTAL det':<10} {frames_a:>12} {frames_b:>12}")

    print(f"\ndisagreements: {len(disagree)}/{processed} frames "
          f"({100.0*len(disagree)/max(1,processed):.1f}%)")
    for fidx, sa, sb in disagree[:15]:
        print(f"  f{fidx:<6} A={sa}  B={sb}")
    if len(disagree) > 15:
        print(f"  ... and {len(disagree)-15} more")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
