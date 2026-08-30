"""Render RetailS pose-only clips as skeleton videos.

There's no raw video in this dataset (privacy-preserving) - only 2D COCO-17 keypoints per
track_id per frame, plus a frame-level shoplifting label for the labelled test splits. This
draws the skeleton(s) on a plain canvas and, when ground truth is available, flashes a red
border + "SHOPLIFTING" label on frames marked anomalous.

    python3 scripts/retails_visualize.py --split test_realworld --n 5
    python3 scripts/retails_visualize.py --split test_realworld --positive-only --n 5
    python3 scripts/retails_visualize.py --file RetailS_test_realworld/pose/test/01_0222.json
"""
from pathlib import Path as _Path
import argparse
import glob
import json
import os
import random
import shutil
import subprocess

import cv2
import numpy as np

HERE = str(_Path(__file__).resolve().parents[1])
ROOT = str(_Path(__file__).resolve().parents[2])
DATA_ROOT = f"{ROOT}/data/datasets/RetailS"
OUT_DIR = f"{ROOT}/dev/outputs/retails_viz"

COCO_SKELETON = [
    (15, 13), (13, 11), (16, 14), (14, 12), (11, 12), (5, 11), (6, 12),
    (5, 6), (5, 7), (6, 8), (7, 9), (8, 10), (1, 2), (0, 1), (0, 2),
    (1, 3), (2, 4), (3, 5), (4, 6),
]
TRACK_COLORS = [
    (80, 200, 255), (100, 255, 100), (255, 140, 80), (255, 100, 220),
    (100, 220, 255), (200, 255, 100), (180, 130, 255),
]

SPLITS = {
    "train": (f"{DATA_ROOT}/RetailS_train/pose/train", None),
    "test_staged": (f"{DATA_ROOT}/RetailS_test_staged/pose/test",
                     f"{DATA_ROOT}/RetailS_test_staged/gt/test_frame_mask"),
    "test_realworld": (f"{DATA_ROOT}/RetailS_test_realworld/pose/test",
                        f"{DATA_ROOT}/RetailS_test_realworld/gt/test_frame_mask"),
}


def to_h264(path):
    """OpenCV writes mp4v here, which many players refuse. Re-encode in place if ffmpeg exists."""
    if not shutil.which("ffmpeg"):
        return
    tmp = path.replace(".mp4", ".h264.mp4")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                        "-pix_fmt", "yuv420p", tmp], capture_output=True)
    if r.returncode == 0:
        os.replace(tmp, path)
    elif os.path.exists(tmp):
        os.remove(tmp)


def kp_array(kp51):
    """Flat keypoint list -> (17, 3) as [x, y, conf].

    RetailS stores each triplet as (y, x, conf) - row-major image indexing, not (x, y).
    Measured over 196k high-confidence frames: reading them as (y, x) puts head above
    shoulders above hips above ankles 94.5% of the time with a median height/width of
    2.75 (a standing person); reading them as (x, y) gets that ordering 47% of the time
    - chance - at a ratio of 0.36, i.e. everyone lying sideways.
    """
    kp = np.array(kp51, dtype=np.float32).reshape(17, 3)
    return np.stack([kp[:, 1], kp[:, 0], kp[:, 2]], axis=1)


def load_clip(json_path):
    """{track_id: {frame_idx(str): {"keypoints": [...]}}} -> {track_id: {frame_idx(int): (17,3) xyc}}"""
    d = json.load(open(json_path))
    tracks = {}
    max_frame = 0
    for tid, frames in d.items():
        tracks[tid] = {int(k): kp_array(v["keypoints"]) for k, v in frames.items()}
        max_frame = max(max_frame, max(tracks[tid].keys()))
    return tracks, max_frame + 1


def render(json_path, gt_path, out_path, fps=10, conf_thr=0.2, target_side=720, max_frames=None):
    tracks, n_frames_est = load_clip(json_path)
    gt = np.load(gt_path) if gt_path and os.path.exists(gt_path) else None
    n_frames = len(gt) if gt is not None else n_frames_est
    # Train clips run to ~19k frames (half an hour at 10fps); nobody watches that to eyeball data.
    if max_frames:
        n_frames = min(n_frames, max_frames)

    pts = [kp[:, :2] for frames in tracks.values() for kp in frames.values()]
    all_xy = np.concatenate(pts, axis=0)
    x0, y0 = all_xy.min(axis=0) - 40
    x1, y1 = all_xy.max(axis=0) + 40
    W, H = max(1, x1 - x0), max(1, y1 - y0)
    # A person walking through frame covers only a few hundred px, so these clips would render
    # postage-stamp sized at 1:1. Scale so the long side always lands near target_side.
    scale = target_side / max(W, H)
    W, H = max(64, int(W * scale)), max(64, int(H * scale))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    track_ids = sorted(tracks.keys())
    colors = {tid: TRACK_COLORS[i % len(TRACK_COLORS)] for i, tid in enumerate(track_ids)}
    th = max(2, int(round(W / 320)))          # keep strokes readable at any canvas size
    dot = max(3, int(round(W / 220)))
    fs = max(0.45, W / 1400)

    for i in range(n_frames):
        canvas = np.full((H, W, 3), 28, np.uint8)
        frame_confs = []
        for tid, frames in tracks.items():
            if i not in frames:
                continue
            kp = frames[i]                       # already (17, 3) as [x, y, conf]
            frame_confs.append(kp[:, 2].mean())
            xy = ((kp[:, :2] - [x0, y0]) * scale).astype(int)
            conf = kp[:, 2]
            col = colors[tid]
            # Confidence is drawn, not thresholded away: pose quality in this dataset varies a lot
            # frame to frame (whole limbs drop to ~0.05), and hiding those joints outright makes a
            # noisy clip look like a clean one. Dim = the detector was unsure, not "not there".
            # Most keypoints in this dataset sit in the 0.2-0.6 confidence band, so the ramp
            # saturates at 0.6 rather than 1.0 - otherwise everything renders near-black.
            def shade(c):
                f = 0.35 + 0.65 * float(np.clip(c / 0.6, 0.0, 1.0))
                return tuple(int(v * f) for v in col)

            for a, b in COCO_SKELETON:
                c_edge = min(conf[a], conf[b])
                if c_edge > 0.03:
                    cv2.line(canvas, tuple(xy[a]), tuple(xy[b]), shade(c_edge), th)
            for j in range(17):
                if conf[j] > 0.03:
                    cv2.circle(canvas, tuple(xy[j]), dot, shade(conf[j]), -1)
            head = xy[0]
            cv2.putText(canvas, f"id{tid}", (head[0] + 8, head[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, col, max(1, th - 1), cv2.LINE_AA)

        if gt is not None and i < len(gt) and gt[i]:
            cv2.rectangle(canvas, (0, 0), (W - 1, H - 1), (0, 0, 255), th * 3)
            cv2.putText(canvas, "SHOPLIFTING", (14, int(34 * fs / 0.5)),
                        cv2.FONT_HERSHEY_SIMPLEX, fs * 1.5, (0, 0, 255), th, cv2.LINE_AA)
        hud = f"frame {i}"
        if frame_confs:
            hud += f"   mean kp conf {np.mean(frame_confs):.2f}"
        else:
            hud += "   (no track in this frame)"
        cv2.putText(canvas, hud, (12, H - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, (190, 190, 190), max(1, th - 1), cv2.LINE_AA)
        writer.write(canvas)
    writer.release()
    to_h264(out_path)
    print(f"saved {out_path}  ({n_frames} frames @ {fps}fps, {W}x{H}, "
          f"{len(tracks)} track{'s' if len(tracks) > 1 else ''})")


def pick_files(split, n, positive_only, seed=0):
    pose_dir, gt_dir = SPLITS[split]
    files = sorted(glob.glob(f"{pose_dir}/*.json"))
    if gt_dir and positive_only:
        files = [f for f in files
                 if np.load(f"{gt_dir}/{os.path.splitext(os.path.basename(f))[0]}.npy").sum() > 0]
    random.Random(seed).shuffle(files)
    return files[:n]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--split", choices=list(SPLITS), default="test_realworld")
    ap.add_argument("--file", help="render one specific pose JSON (relative to datasets/RetailS/ "
                                    "or an absolute path) instead of picking from --split")
    ap.add_argument("--n", type=int, default=5, help="how many clips to render from --split")
    ap.add_argument("--positive-only", action="store_true",
                     help="only pick clips with at least one shoplifting-labelled frame")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-frames", type=int, default=600,
                     help="cap clip length; train clips reach ~19k frames (0 = no cap)")
    a = ap.parse_args()
    cap_frames = a.max_frames or None

    if a.file:
        json_path = a.file if os.path.isabs(a.file) else f"{DATA_ROOT}/{a.file}"
        stem = os.path.splitext(os.path.basename(json_path))[0]
        split_guess = next((s for s in SPLITS if s in json_path), "test_realworld")
        gt_dir = SPLITS[split_guess][1]
        gt_path = f"{gt_dir}/{stem}.npy" if gt_dir else None
        render(json_path, gt_path, f"{OUT_DIR}/{split_guess}_{stem}.mp4",
               fps=a.fps, max_frames=cap_frames)
    else:
        pose_dir, gt_dir = SPLITS[a.split]
        files = pick_files(a.split, a.n, a.positive_only, a.seed)
        if not files:
            raise SystemExit(f"no clips found for split={a.split} positive_only={a.positive_only}")
        for f in files:
            stem = os.path.splitext(os.path.basename(f))[0]
            gt_path = f"{gt_dir}/{stem}.npy" if gt_dir else None
            render(f, gt_path, f"{OUT_DIR}/{a.split}_{stem}.mp4",
                   fps=a.fps, max_frames=cap_frames)
