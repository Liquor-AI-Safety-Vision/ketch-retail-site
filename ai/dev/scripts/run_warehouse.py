"""Phase 1 runner: warehouse zone + entry/exit events on recorded footage.

    python3 run_warehouse.py --room storage_1

Reuses the shared perception stack unchanged (detector, embedder, tracker from
stream_track / person_tracker); only the business rules differ, which is the split
doc 02 section 3.1 asks for. Offline by design - this exists to judge tracking
quality and the entry/exit logic, so every frame is processed and none dropped.
"""
import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[3]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import json
import os
import shutil
import subprocess
import time
import warnings

import cv2
import numpy as np

from ai.src.perception import stream_track as S
from ai.src.perception import zone_monitor
from ai.src.perception.person_tracker import Detection, Tracker

warnings.filterwarnings("ignore", message=".*encountered in matmul",
                        category=RuntimeWarning)

HERE = str(_Path(__file__).resolve().parents[3])


def to_h264(path):
    """OpenCV can only write mp4v here, which most players refuse. Re-encode in
    place so the file that lands on disk is one you can actually open."""
    if not shutil.which("ffmpeg"):
        print(f"! ffmpeg not found; leaving {path} as mp4v (may not play)")
        return
    tmp = path.replace(".mp4", ".h264.mp4")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                        "-pix_fmt", "yuv420p", tmp], capture_output=True)
    if r.returncode != 0:
        print(f"! re-encode failed: {r.stderr.decode()[:200]}")
        return
    os.replace(tmp, path)
STATE_COLOR = {zone_monitor.OUTSIDE: (170, 170, 170),
               zone_monitor.PENDING_IN: (60, 200, 255),
               zone_monitor.INSIDE: (70, 70, 255)}


def draw(img, items, poly, hud, cfg):
    h, w = img.shape[:2]
    s = max(1.0, h / 540.0)
    th, fs = int(round(2 * s)), 0.45 * s

    ov = img.copy()
    cv2.fillPoly(ov, [poly.astype(np.int32)], (60, 180, 60))
    cv2.addWeighted(ov, 0.18, img, 0.82, 0, img)
    cv2.polylines(img, [poly.astype(np.int32)], True, (255, 255, 255), th)

    for tid, ltrb, kpts, st, pt, streak in items:
        c = STATE_COLOR[st]
        p1 = (int(ltrb[0]), int(ltrb[1]))
        cv2.rectangle(img, p1, (int(ltrb[2]), int(ltrb[3])), c, th)
        tag = f"ID{tid} {st}"
        if st == zone_monitor.PENDING_IN:
            tag += f" {streak}/{cfg.entry_frames}"
        elif st == zone_monitor.INSIDE and streak:
            tag += f" leaving {streak}/{cfg.exit_frames}"
        (tw, tht), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
        cv2.rectangle(img, (p1[0], p1[1] - tht - 8), (p1[0] + tw + 8, p1[1]), c, -1)
        cv2.putText(img, tag, (p1[0] + 4, p1[1] - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), th)
        cv2.circle(img, (int(pt[0]), int(pt[1])), max(4, th + 3), c, -1)
        cv2.circle(img, (int(pt[0]), int(pt[1])), max(4, th + 3), (255, 255, 255), 1)

    cv2.rectangle(img, (0, 0), (w, int(34 * s)), (0, 0, 0), -1)
    cv2.putText(img, hud, (10, int(24 * s)), cv2.FONT_HERSHEY_SIMPLEX,
                0.55 * s, (255, 255, 255), th)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--room", default="storage_1")
    p.add_argument("--zones", default=f"{HERE}/ai/dev/outputs/zones/storage_zones.json")
    p.add_argument("--model", default=f"{HERE}/ai/models/yolo26m-pose.onnx")
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--conf", type=float, help="override person_conf")
    p.add_argument("--min-h", type=float,
                   help="min person height as a fraction of frame height; "
                        "overrides min_box_h_frac in the room config")
    p.add_argument("--anchor", default="pelvis",
                   choices=["pelvis", "bottom_center"],
                   help="doc 02 says bottom_center; pelvis measured better "
                        "under waist-height occlusion")
    a = p.parse_args()

    cfg = zone_monitor.Config(fps=a.fps, anchor=a.anchor)
    if a.conf is not None:
        cfg.person_conf = a.conf
    S.MODEL, S.CONF = a.model, cfg.person_conf

    video = f"{HERE}/data/videos/{a.room}.mp4"
    room_cfg = json.load(open(a.zones))[a.room]
    zone_norm = room_cfg["zone_norm"]
    # per-camera first, then the CLI, then the fallback in Config
    if "min_box_h_frac" in room_cfg:
        cfg.min_box_h_frac = float(room_cfg["min_box_h_frac"])
    if a.min_h is not None:
        cfg.min_box_h_frac = a.min_h

    cap = cv2.VideoCapture(video)
    W, H = int(cap.get(3)), int(cap.get(4))
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    det, emb = S.Detector(), S.Embedder()
    tracker = Tracker(img_h=H, img_w=W, fps=cfg.fps)
    mon = zone_monitor.ZoneMonitor(zone_norm, W, H, cfg)

    out_video = f"{HERE}/data/videos/{a.room}_warehouse.mp4"
    os.makedirs(f"{HERE}/ai/dev/outputs/{a.room}", exist_ok=True)
    out_events = f"{HERE}/ai/dev/outputs/{a.room}/{a.room}_warehouse_events.jsonl"
    writer = cv2.VideoWriter(out_video, cv2.VideoWriter_fourcc(*"mp4v"),
                             cfg.fps, (W, H))

    min_h = cfg.min_box_h_frac * H
    print(f"{a.room}: {W}x{H} @ {src_fps:.2f} -> {cfg.fps} fps, {total} frames")
    print(f"conf={cfg.person_conf}  entry={cfg.entry_confirmation_s}s"
          f"/{cfg.entry_frames}f  exit={cfg.exit_confirmation_s}s"
          f"/{cfg.exit_frames}f  presence={cfg.presence_confirmation_frames}f"
          f"  anchor={cfg.anchor}")
    print(f"min person height: {cfg.min_box_h_frac:.2f}H = {min_h:.0f}px "
          "(per-camera: depends on lens and mounting, not just resolution)")
    print("no allowed windows: every confirmed entry is a violation")

    # A confirmation window longer than the tracker's max age can never complete
    # for someone who leaves the camera's view, which is how people usually
    # leave a room. Say so rather than let ZONE_EXIT quietly go missing.
    from ai.src.perception.person_tracker import Config as TCfg
    for label, age in (("max_age", TCfg.MAX_AGE_S),
                       ("near_border_max_age", TCfg.NEAR_BORDER_MAX_AGE_S)):
        if cfg.exit_confirmation_s > age:
            print(f"! exit_confirmation {cfg.exit_confirmation_s}s > tracker "
                  f"{label} {age}s - a track that leaves view dies first; "
                  f"exits are inferred after {cfg.exit_on_loss_min_frames} "
                  "frames outside instead")

    step, acc, idx, t0 = cfg.fps / src_fps, 0.0, 0, time.perf_counter()
    n_det, max_inside, frames_occupied, n_small = [], 0, 0, 0
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
        if len(boxes) and min_h > 0:
            # filter here, before the tracker, so a shelf ornament never gets
            # issued a track id at all - a rejected detection leaves no trace,
            # a rejected track would still churn ids and zone state
            keep = (boxes[:, 3] - boxes[:, 1]) >= min_h
            n_small += int((~keep).sum())
            boxes, kpts = boxes[keep], [k for k, m in zip(kpts, keep) if m]
        feats = emb(frame, boxes)
        live, removed = tracker.update(
            [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i])
             for i in range(len(boxes))], idx)
        mon.drop(removed, idx)
        items = mon.update([(t.track_id, t.ltrb, t.kpts, src == "detected")
                            for t, src in live], idx)

        n = mon.n_inside()
        max_inside = max(max_inside, n)
        frames_occupied += n > 0
        n_det.append(len(boxes))

        draw(frame, items, mon.poly,
             f"f{idx}  t={idx/cfg.fps:6.1f}s  tracks={len(items)}  "
             f"in_zone={n}  entries={sum(1 for e in mon.events if e['event']=='ZONE_ENTRY')}",
             cfg)
        writer.write(frame)
        idx += 1

    cap.release()
    writer.release()
    to_h264(out_video)
    with open(out_events, "w") as fh:
        for e in mon.events:
            fh.write(json.dumps(e) + "\n")

    wall = time.perf_counter() - t0
    kinds = {}
    for e in mon.events:
        kinds[e["event"]] = kinds.get(e["event"], 0) + 1
    ent = [e for e in mon.events if e["event"] == "ZONE_ENTRY"]
    born = [e for e in ent if "BORN_INSIDE_ZONE" in e["reason_codes"]]
    print(f"\nprocessed {idx} frames in {wall:.0f}s ({idx/wall:.1f} fps)")
    print(f"detections/frame: mean={np.mean(n_det):.2f} max={max(n_det)}")
    print(f"detections dropped as too small: {n_small}")
    print(f"zone occupied in {frames_occupied}/{idx} frames "
          f"({frames_occupied/idx*100:.0f}%), peak {max_inside} people")
    print("events: " + "  ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    print(f"  of the entries, born-inside: {len(born)}")
    print("anchor source: " + "  ".join(f"{k}={v}" for k, v in
                                        mon.anchor_src.items() if v)
          + f"   (of these, {mon.coasted} placed by remembered fraction "
            "on a coasting frame)")
    print(f"saved {out_video}\n      {out_events}")


if __name__ == "__main__":
    main()
