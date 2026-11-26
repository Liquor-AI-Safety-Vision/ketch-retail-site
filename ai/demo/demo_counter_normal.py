"""Counter demo: ordinary transaction, no theft logic - just tracking + roles
+ till state.

    python3 demo_counter_normal.py --video videos/cctv_counter/cash_till_open_2.mp4

Reuses run_counter.py's Till/TillRegistry unchanged (the till does not care
which demo is asking about it). Only the person overlay differs from
run_counter's default: no track id, and the box colour encodes the role
(green=customer, orange=staff) instead of a per-id random colour, since this
clip exists to show "here is a normal sale", not "here is how re-id survives
an occlusion".
"""
import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[2]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import os
import time
import warnings

import cv2

from ai.src.perception import stream_track as S
from ai.src.perception import zones
from ai.src.perception.run_counter import Till, draw_till
from ai.src.perception.uae_tracker import Detection, Tracker

warnings.filterwarnings("ignore", message=".*encountered in matmul",
                        category=RuntimeWarning)

HERE = str(_Path(__file__).resolve().parents[2])

ROLE_COLOR = {"staff": (0, 140, 255), "customer": (60, 200, 60), "?": (170, 170, 170)}
ROLE_LABEL = {"staff": "STAFF", "customer": "CUSTOMER", "?": "?"}


def draw(img, items):
    h, w = img.shape[:2]
    s = max(1.0, h / 540.0)
    th, fs = int(round(2 * s)), 0.55 * s
    for ltrb, kpts, source, role in items:
        c = ROLE_COLOR.get(role, ROLE_COLOR["?"])
        predicted = source == "predicted"
        p1, p2 = (int(ltrb[0]), int(ltrb[1])), (int(ltrb[2]), int(ltrb[3]))
        cv2.rectangle(img, p1, p2, c, max(1, th // 2) if predicted else th)
        tag = ROLE_LABEL.get(role, "?")
        (tw, tht), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
        cv2.rectangle(img, (p1[0], p1[1] - tht - 8), (p1[0] + tw + 8, p1[1]), c, -1)
        cv2.putText(img, tag, (p1[0] + 4, p1[1] - 5), cv2.FONT_HERSHEY_SIMPLEX,
                    fs, (0, 0, 0), th)
        if kpts is not None:
            for a, b in S.SKELETON:
                if kpts[a, 2] > 0.5 and kpts[b, 2] > 0.5:
                    cv2.line(img, tuple(kpts[a, :2].astype(int)),
                             tuple(kpts[b, :2].astype(int)), c, max(1, th // 2))
            for x, y, cf in kpts:
                if cf > 0.5:
                    cv2.circle(img, (int(x), int(y)), max(2, th), c, -1)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--video", required=True)
    p.add_argument("--out")
    p.add_argument("--pose", default=f"{HERE}/ai/models/yolo26m-pose.onnx")
    p.add_argument("--reid", default=f"{HERE}/ai/models/osnet_x0_25_msmt17.onnx")
    p.add_argument("--till", default=f"{HERE}/ai/models/till_seg_v4_640.onnx")
    p.add_argument("--till-imgsz", type=int, default=640)
    p.add_argument("--till-conf", type=float, default=0.70)
    p.add_argument("--till-confirm", type=int, default=5)
    p.add_argument("--conf", type=float, default=0.35)
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--zones", default=f"{HERE}/ai/dev/outputs/top-side-view/top-side-view_zones.json")
    p.add_argument("--flip-side", action="store_true")
    p.add_argument("--limit", type=int)
    a = p.parse_args()

    S.MODEL, S.REID_MODEL, S.CONF = a.pose, a.reid, a.conf
    out_video = a.out or f"{HERE}/ai/demo/cctv_counter/{os.path.splitext(os.path.basename(a.video))[0]}.mp4"
    os.makedirs(os.path.dirname(out_video), exist_ok=True)

    cap = cv2.VideoCapture(a.video)
    W, H = int(cap.get(3)), int(cap.get(4))
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    det, emb = S.Detector(), S.Embedder()
    till = Till(a.till, a.till_imgsz, a.till_conf, confirm=a.till_confirm)
    tracker = Tracker(img_h=H, img_w=W, fps=a.fps)
    div, zname = zones.load_divider(a.zones, flip=a.flip_side, size=(W, H))
    voter = zones.RoleVoter(div)

    print(f"{os.path.basename(a.video)}: {W}x{H} @ {src_fps:.2f} -> {a.fps} fps, "
          f"{total} frames  divider={zname!r}")

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
        tills = till(frame)
        feats = emb(frame, boxes)
        live, removed = tracker.update(
            [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i])
             for i in range(len(boxes))], idx)
        for tid in removed:
            voter.drop(tid)

        items = []
        for t, source in live:
            role, _ = voter.update(t.track_id, t.ltrb, t.kpts)
            items.append((t.ltrb, t.kpts, source, role))

        items_till, _ = till.reg.update(tills)
        s = max(1.0, H / 540.0)
        draw_till(frame, items_till, till.reg, int(round(2 * s)))
        draw(frame, items)
        writer.write(frame)
        idx += 1
        if idx % 200 == 0:
            print(f"  {idx}/{total}")

    cap.release()
    writer.release()
    from ai.src.perception.video_io import to_h264
    to_h264(out_video)
    print(f"processed {idx} frames in {time.perf_counter() - t0:.0f}s")
    print(f"saved {out_video}")


if __name__ == "__main__":
    main()
