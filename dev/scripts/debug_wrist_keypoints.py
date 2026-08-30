"""Debug viz: fresh pose inference, full 17-keypoint overlay, cropped tight
on the person, rendered as a video (not one-off frames) so the keypoint
jitter/mislocation is visible over time. For inspecting the pose model, not
production use.

    python3 debug_wrist_keypoints.py
"""
import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[2]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import os

import cv2
import numpy as np

from backend.src.perception import stream_track as S

HERE = str(_Path(__file__).resolve().parents[2])
OUT = f"{HERE}/dev/outputs/debug"
os.makedirs(OUT, exist_ok=True)

KPT_NAMES = ["nose", "eyeL", "eyeR", "earL", "earR", "shoL", "shoR", "elbL",
             "elbR", "wriL", "wriR", "hipL", "hipR", "kneL", "kneR", "ankL",
             "ankR"]
FPS = 10.0
SCALE = 4
PAD = 40

S.MODEL = f"{HERE}/backend/models/yolo26m-pose.onnx"
S.CONF = 0.35
det = S.Detector()


def downsampled_frames(video, fps=FPS):
    """Yield (idx, frame) at the same accumulator downsample probe_wrist.py
    uses, so idx lines up with the frozen frames_json dumps."""
    cap = cv2.VideoCapture(video)
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    step, acc, idx = fps / src_fps, 0.0, 0
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
        yield idx, frame
        idx += 1
    cap.release()


def draw_full_pose(crop_canvas, box, kpts, ox, oy):
    """ox, oy: top-left of the fixed crop window in original-frame pixels."""
    def tr(p):
        return (int((p[0] - ox) * SCALE), int((p[1] - oy) * SCALE))

    for a, b in S.SKELETON:
        if kpts[a, 2] > 0.3 and kpts[b, 2] > 0.3:
            cv2.line(crop_canvas, tr(kpts[a, :2]), tr(kpts[b, :2]),
                     (0, 255, 255), 2)
    for i, (x, y, c) in enumerate(kpts):
        if c < 0.15:
            continue
        p = tr((x, y))
        col = (0, 0, 255) if c >= 0.5 else (0, 140, 255)
        r = 10 if "wri" in KPT_NAMES[i] else 6
        cv2.circle(crop_canvas, p, r, col, -1)
        cv2.putText(crop_canvas, f"{KPT_NAMES[i]} {c:.2f}",
                    (p[0] + 12, p[1] + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    col, 2, cv2.LINE_AA)


def pick_person(boxes):
    return int(np.argmax((boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])))


def render_debug_video(video, tag, max_f=None):
    # pass 1: union box, so the crop window is fixed for the whole clip
    boxes_seen = []
    for idx, frame in downsampled_frames(video):
        if max_f and idx > max_f:
            break
        boxes, _ = det(frame)
        if len(boxes):
            boxes_seen.append(boxes[pick_person(boxes), :4])
    if not boxes_seen:
        print(f"  {tag}: no detections at all")
        return
    u = np.array(boxes_seen)
    ox1, oy1 = max(0, int(u[:, 0].min()) - PAD), max(0, int(u[:, 1].min()) - PAD)
    ox2, oy2 = int(u[:, 2].max()) + PAD, int(u[:, 3].max()) + PAD
    out_w, out_h = (ox2 - ox1) * SCALE, (oy2 - oy1) * SCALE

    out_path = f"{OUT}/{tag}_pose_debug.mp4"
    wr = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), FPS,
                         (out_w, out_h))

    for idx, frame in downsampled_frames(video):
        if max_f and idx > max_f:
            break
        src = frame[oy1:oy2, ox1:ox2]
        canvas = cv2.resize(src, (out_w, out_h), interpolation=cv2.INTER_CUBIC)

        boxes, kpts = det(frame)
        if len(boxes):
            i = pick_person(boxes)
            draw_full_pose(canvas, boxes[i, :4], kpts[i], ox1, oy1)
        cv2.putText(canvas, f"f{idx}  t={idx/FPS:4.1f}s", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2, cv2.LINE_AA)
        wr.write(canvas)

    wr.release()
    from backend.src.perception.video_io import to_h264
    to_h264(out_path)
    print(f"  saved {out_path}")


if __name__ == "__main__":
    print("cvlp_h3_00043_Left (FSM: fails to confirm)")
    render_debug_video(f"{HERE}/data/videos/counter-generated/Left/cvlp_h3_00043_.mp4",
                       "cvlp_h3_00043_Left")

    print("cvlp_h3_00045_Left (FSM: confirms CONCEALED)")
    render_debug_video(f"{HERE}/data/videos/counter-generated/Left/cvlp_h3_00045_.mp4",
                       "cvlp_h3_00045_Left")
