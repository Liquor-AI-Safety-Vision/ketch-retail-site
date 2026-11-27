"""Assess whether the counter-generated clips can support Problem 2.

Problem 2 needs one thing the tracking pipeline does not already provide: a
wrist position per frame that is right often enough to drive the FSM. This
measures that, per clip, on the employee track only.

    python3 probe_wrist.py --clips <dir> [--n 8]

Emits per-clip JSON to <out>/<stem>.json and a summary table.
"""
import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[3]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import glob
import json
import os
import sys

import cv2
import numpy as np

ROOT = str(_Path(__file__).resolve().parents[3])
sys.path.insert(0, f"{ROOT}/scripts")

from ai.src.perception import stream_track as S                       # noqa: E402
from ai.src.perception.run_counter import Till                   # noqa: E402
from ai.src.perception.uae_tracker import Detection, Tracker      # noqa: E402

L_WRIST, R_WRIST = 9, 10
L_ELBOW, R_ELBOW = 7, 8
L_HIP, R_HIP = 11, 12
CONF_TH = 0.3                                  # wrist_conf_th, Phu luc A


def expand(box, f=0.25):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    return [x1 - f * w, y1 - f * h, x2 + f * w, y2 + f * h]


def inside(pt, box):
    return box[0] <= pt[0] <= box[2] and box[1] <= pt[1] <= box[3]


def run(video, det, emb, till_model, fps=10.0):
    cap = cv2.VideoCapture(video)
    W, H = int(cap.get(3)), int(cap.get(4))
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    tracker = Tracker(img_h=H, img_w=W, fps=fps)
    till_model.reg.tills.clear()

    step, acc, idx = fps / src_fps, 0.0, 0
    frames = []
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
        tills = till_model(frame)
        feats = emb(frame, boxes)
        live, _ = tracker.update(
            [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i])
             for i in range(len(boxes))], idx)
        items_till, _ = till_model.reg.update(tills)

        rec = {"f": idx, "tracks": [], "tills": []}
        poly_of = {}
        for t, name, score, poly in items_till:
            poly_of[id(t)] = [float(poly[:, 0].min()), float(poly[:, 1].min()),
                              float(poly[:, 0].max()), float(poly[:, 1].max())]
        for t in till_model.reg.tills:
            rec["tills"].append({"i": till_model.reg.index(t),
                                 "state": t.state,
                                 "centre": [float(c) for c in t.centre],
                                 "box": poly_of.get(id(t))})
        for t, source in live:
            k = t.kpts
            rec["tracks"].append({
                "id": int(t.track_id),
                "src": source,
                "ltrb": [float(v) for v in t.ltrb],
                "kpts": None if k is None else
                        [[float(x), float(y), float(c)] for x, y, c in k],
            })
        frames.append(rec)
        idx += 1
    cap.release()
    return frames, (W, H)


def analyse(frames, size):
    """The employee is the track that spends the most frames nearest a till."""
    W, H = size
    # actor = track with the largest mean bbox area (staff are nearest camera
    # at the counter; bystanders are deep in the aisle)
    areas, counts = {}, {}
    for fr in frames:
        for t in fr["tracks"]:
            x1, y1, x2, y2 = t["ltrb"]
            areas[t["id"]] = areas.get(t["id"], 0.0) + (x2 - x1) * (y2 - y1)
            counts[t["id"]] = counts.get(t["id"], 0) + 1
    if not areas:
        return None
    actor = max(areas, key=lambda i: areas[i] / counts[i])

    n = 0
    conf_l, conf_r = [], []
    valid_l = valid_r = 0
    in_till = 0
    open_frames = 0
    kp_all = []
    for fr in frames:
        t = next((t for t in fr["tracks"] if t["id"] == actor), None)
        opened = [x for x in fr["tills"] if x["state"] == "Open"]
        open_frames += bool(opened)
        if t is None or t["kpts"] is None:
            continue
        n += 1
        k = np.array(t["kpts"])
        kp_all.append(k[:, 2])
        conf_l.append(k[L_WRIST, 2])
        conf_r.append(k[R_WRIST, 2])
        valid_l += k[L_WRIST, 2] >= CONF_TH
        valid_r += k[R_WRIST, 2] >= CONF_TH
        if opened:
            box = opened[0]["box"]
            if box:
                eb = expand(box)
                for w in (L_WRIST, R_WRIST):
                    if k[w, 2] >= CONF_TH and inside(k[w, :2], eb):
                        in_till += 1
                        break

    kp_all = np.array(kp_all) if kp_all else np.zeros((0, 17))
    return {
        "actor": int(actor),
        "n_tracks": len(areas),
        "frames_with_actor_pose": n,
        "frames_total": len(frames),
        "frames_till_open": open_frames,
        "wrist_conf_mean_L": float(np.mean(conf_l)) if conf_l else 0.0,
        "wrist_conf_mean_R": float(np.mean(conf_r)) if conf_r else 0.0,
        "wrist_valid_frac_L": valid_l / n if n else 0.0,
        "wrist_valid_frac_R": valid_r / n if n else 0.0,
        "frames_wrist_in_open_till_roi": in_till,
        "kpt_conf_mean_per_joint": kp_all.mean(0).round(3).tolist()
                                   if len(kp_all) else [],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--clips", nargs="+", required=True)
    p.add_argument("--out", default="probe_out")
    p.add_argument("--pose", default=f"{ROOT}/ai/models/yolo26m-pose.onnx")
    a = p.parse_args()

    S.MODEL = a.pose
    S.REID_MODEL = f"{ROOT}/ai/models/osnet_x0_25_msmt17.onnx"
    S.CONF = 0.35
    os.makedirs(a.out, exist_ok=True)

    det, emb = S.Detector(), S.Embedder()
    till_model = Till(f"{ROOT}/ai/models/till_seg_v4_640.onnx", 640, 0.70, confirm=5)

    rows = []
    for v in a.clips:
        stem = os.path.splitext(os.path.basename(v))[0]
        frames, size = run(v, det, emb, till_model)
        st = analyse(frames, size)
        with open(f"{a.out}/{stem}.json", "w") as fh:
            json.dump({"video": v, "size": size, "stats": st, "frames": frames},
                      fh)
        rows.append((stem, st))
        print(f"{stem}: {st['n_tracks']} tracks, actor={st['actor']}, "
              f"open={st['frames_till_open']}/{st['frames_total']}, "
              f"wristL={st['wrist_valid_frac_L']:.2f} "
              f"wristR={st['wrist_valid_frac_R']:.2f} "
              f"in_roi={st['frames_wrist_in_open_till_roi']}", flush=True)

    print("\n=== summary ===")
    vl = np.mean([r[1]["wrist_valid_frac_L"] for r in rows])
    vr = np.mean([r[1]["wrist_valid_frac_R"] for r in rows])
    roi = np.mean([r[1]["frames_wrist_in_open_till_roi"] for r in rows])
    op = np.mean([r[1]["frames_till_open"] / r[1]["frames_total"] for r in rows])
    print(f"clips={len(rows)}  till open {op*100:.0f}% of frames")
    print(f"wrist valid (conf>={CONF_TH}): L={vl*100:.0f}%  R={vr*100:.0f}%")
    print(f"frames with a wrist inside the open-till ROI: mean {roi:.1f}/clip")
    joints = np.array([r[1]["kpt_conf_mean_per_joint"] for r in rows
                       if r[1]["kpt_conf_mean_per_joint"]]).mean(0)
    names = ["nose", "eyeL", "eyeR", "earL", "earR", "shoL", "shoR", "elbL",
             "elbR", "wriL", "wriR", "hipL", "hipR", "kneL", "kneR", "ankL",
             "ankR"]
    print("mean keypoint confidence, actor track:")
    for nm, c in zip(names, joints):
        print(f"  {nm:<5} {c:.3f}")


if __name__ == "__main__":
    main()
