"""Whole-store demo: person tracking only, no theft logic.

Customers are just being tracked, nothing suspicious happens in these clips,
so the overlay says nothing about identity or role - one uniform green
bbox + skeleton per person is the whole picture. Wrists get their own,
higher confidence gate (0.70 vs the 0.5 used for every other joint) because a
mislabelled wrist is what would make a viewer read a hand gesture that never
happened; every other joint just draws the skeleton and a wrong one there is
visually harmless.

    python3 demo_whole_store.py --video videos/whole_store/cold_drinks.mp4

Offline by design, like run_counter.py: every downsampled frame is processed,
none dropped, so the output is reviewable frame by frame.
"""
import argparse
import os
import time
import warnings

import cv2
import numpy as np

import stream_track as S
from uae_tracker import Detection, Tracker

warnings.filterwarnings("ignore", message=".*encountered in matmul",
                        category=RuntimeWarning)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GREEN = (60, 200, 60)
KPT_VIS = 0.5
WRIST_VIS = 0.70
WRIST_IDX = (9, 10)


def draw(img, items):
    h, w = img.shape[:2]
    s = max(1.0, h / 540.0)
    th = int(round(2 * s))
    for tid, ltrb, kpts, source in items:
        predicted = source == "predicted"
        p1 = (int(ltrb[0]), int(ltrb[1]))
        p2 = (int(ltrb[2]), int(ltrb[3]))
        cv2.rectangle(img, p1, p2, GREEN, max(1, th // 2) if predicted else th)
        if kpts is None:
            continue
        vis = kpts[:, 2] > KPT_VIS
        for j in WRIST_IDX:
            vis[j] = kpts[j, 2] > WRIST_VIS
        for a, b in S.SKELETON:
            if a in WRIST_IDX and not vis[a]:
                continue
            if b in WRIST_IDX and not vis[b]:
                continue
            if kpts[a, 2] > KPT_VIS and kpts[b, 2] > KPT_VIS and vis[a] and vis[b]:
                cv2.line(img, tuple(kpts[a, :2].astype(int)),
                         tuple(kpts[b, :2].astype(int)), GREEN, max(1, th // 2))
        for j, (x, y, c) in enumerate(kpts):
            if vis[j]:
                cv2.circle(img, (int(x), int(y)), max(2, th), GREEN, -1)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--video", required=True)
    p.add_argument("--out")
    p.add_argument("--pose", default=f"{HERE}/models/yolo26m-pose.onnx")
    p.add_argument("--reid", default=f"{HERE}/models/osnet_x0_25_msmt17.onnx")
    p.add_argument("--conf", type=float, default=0.35, help="person detection conf")
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--limit", type=int, help="stop after N frames (wiring check)")
    a = p.parse_args()

    S.MODEL, S.REID_MODEL, S.CONF = a.pose, a.reid, a.conf
    out_video = a.out or f"{HERE}/demo/whole_store/{os.path.splitext(os.path.basename(a.video))[0]}.mp4"
    os.makedirs(os.path.dirname(out_video), exist_ok=True)

    cap = cv2.VideoCapture(a.video)
    W, H = int(cap.get(3)), int(cap.get(4))
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    det, emb = S.Detector(), S.Embedder()
    tracker = Tracker(img_h=H, img_w=W, fps=a.fps)

    print(f"{os.path.basename(a.video)}: {W}x{H} @ {src_fps:.2f} -> {a.fps} fps, "
          f"{total} frames  wrist_vis={WRIST_VIS} person_conf={a.conf}")

    writer = cv2.VideoWriter(out_video, cv2.VideoWriter_fourcc(*"mp4v"),
                             a.fps, (W, H))
    step, acc, idx, t0 = a.fps / src_fps, 0.0, 0, time.perf_counter()
    while True:
        if a.limit and idx >= a.limit:
            break
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
        feats = emb(frame, boxes)
        live, _ = tracker.update(
            [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i])
             for i in range(len(boxes))], idx)
        items = [(t.track_id, t.ltrb, t.kpts, source) for t, source in live]
        draw(frame, items)
        writer.write(frame)
        idx += 1
        if idx % 200 == 0:
            print(f"  {idx}/{total}")

    cap.release()
    writer.release()
    from run_warehouse import to_h264
    to_h264(out_video)
    print(f"processed {idx} frames in {time.perf_counter() - t0:.0f}s")
    print(f"saved {out_video}")


if __name__ == "__main__":
    main()
