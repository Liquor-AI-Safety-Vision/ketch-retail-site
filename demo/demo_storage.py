"""Storage/warehouse demo: zone entry (box turns red) + a face-ID corner chip.

    python3 demo_storage.py --room storage_1

Two identity modes:

  - default demo mode: names are still scripted by entry order, exactly as
    before, to keep the old walkthrough intact with no gallery setup
  - real matching mode (--face-db): detect face inside the entering track's
    person crop, align to 112x112, embed with ArcFace and match against SQLite
    gallery entries

That rule is keyed to *entry order*, not to a track id. An earlier version
hardcoded track_id -> name from a one-off exploration run, and it broke: the
CoreML embedder is not bit-exact run to run (see stream_track's own warning
about Accelerate/BLAS nondeterminism), which nudges the tracker's matching at
close calls and reissues different track ids for the same people on a
different run. Entry order is the one thing that stays true regardless.

What IS real: the face crop itself. Each frame a track is alive, the
yolov6s-face model runs on the full frame and, if a detection's centre falls
inside that track's box, its 5-point landmarks align the face to a canonical
112x112 chip - the same alignment a real recognizer would want - and the
best-scoring chip seen over the track's life is what gets shown. So the
picture in the corner is a real, aligned crop of that person; only the name
next to it is asserted rather than recognized.

Reuses warehouse.ZoneMonitor unchanged for the zone state machine (doc 02);
only adds the face chip and the identity label on top of run_warehouse.py's
drawing.
"""
import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[1]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import json
import os
import time
import warnings

import cv2
import numpy as np

from backend.src.perception.face_align import FaceDetector, align, best_face_in_box
from backend.src.perception.face_identity import FaceIdentifier
from backend.src.perception import stream_track as S
from backend.src.perception import warehouse
from backend.src.perception.uae_tracker import Detection, Tracker

warnings.filterwarnings("ignore", message=".*encountered in matmul",
                        category=RuntimeWarning)

HERE = str(_Path(__file__).resolve().parents[1])

STATE_COLOR = {warehouse.OUTSIDE: (170, 170, 170),
               warehouse.PENDING_IN: (60, 200, 255),
               warehouse.INSIDE: (60, 60, 255)}

# Whoever gets the first confirmed ZONE_ENTRY of the run is labelled this;
# every other track is "Unknown". Checked against the source footage: in
# storage_1 the first arrival (~t=108s, the "1:44" pair) is genuinely staff;
# in storage_2 the sole visitor (~t=62s) is too.
FIRST_ENTRANT_NAME = {"storage_1": "Staff_A", "storage_2": "Staff_B"}
UNKNOWN = "Unknown"
FACE_SCORE_MIN = 0.20   # below this a "face" is more likely shelf glare
CHIP = 140              # corner thumbnail size, px
ENTRY_FACE_WINDOW_S = 1.0
STATE_LABEL = {warehouse.OUTSIDE: "outside", warehouse.PENDING_IN: "entering",
               warehouse.INSIDE: "inside"}


class FaceChip:
    def __init__(self):
        self.best_score = -1.0
        self.img = None
        self.match = None


class EntryFaceWindow:
    def __init__(self, start_frame):
        self.start_frame = start_frame
        self.end_frame = None
        self.best = None

    def is_collecting(self, frame_idx):
        return self.end_frame is None or frame_idx <= self.end_frame

    def consider(self, decision):
        if decision.observation is None:
            return
        if decision.person is None:
            return
        key = (decision.person.similarity, decision.observation.detector_score)
        if self.best is None:
            self.best = (key, decision)
            return
        if key > self.best[0]:
            self.best = (key, decision)

    def resolved_identity(self):
        return None if self.best is None else self.best[1].display_name


def draw(img, items, poly, chips, identities, hud):
    h, w = img.shape[:2]
    s = max(1.0, h / 540.0)
    th, fs = int(round(2 * s)), 0.5 * s

    ov = img.copy()
    cv2.fillPoly(ov, [poly.astype(np.int32)], (60, 180, 60))
    cv2.addWeighted(ov, 0.15, img, 0.85, 0, img)
    cv2.polylines(img, [poly.astype(np.int32)], True, (255, 255, 255), 1,
                  cv2.LINE_AA)

    for tid, ltrb, kpts, st, pt, streak in items:
        c = STATE_COLOR[st]
        p1 = (int(ltrb[0]), int(ltrb[1]))
        p2 = (int(ltrb[2]), int(ltrb[3]))
        cv2.rectangle(img, p1, p2, c, th)
        tag = identities.get(tid, "Unknown")
        if st == warehouse.PENDING_IN:
            tag += f" entering {streak}/{poly_cfg.entry_frames}"
        elif st == warehouse.INSIDE and streak:
            tag += f" leaving {streak}/{poly_cfg.exit_frames}"
        (tw, tht), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
        cv2.rectangle(img, (p1[0], p1[1] - tht - 8), (p1[0] + tw + 8, p1[1]), c, -1)
        cv2.putText(img, tag, (p1[0] + 4, p1[1] - 5), cv2.FONT_HERSHEY_SIMPLEX,
                    fs, (0, 0, 0), th)

    state_of = {tid: st for tid, ltrb, kpts, st, pt, streak in items}
    x = w - CHIP - int(24 * s)
    y = int(46 * s)          # clear of the top-left border/margin, not flush to the corner
    label_fs, label_th = 0.55, 1
    for tid, chip in chips.items():
        if chip.img is None:
            continue
        name = identities.get(tid, "Unknown")
        state = STATE_LABEL.get(state_of.get(tid), "")
        col = (60, 200, 60) if name != "Unknown" else (170, 170, 170)
        label = f"{name} - {state}" if state else name
        (tw, tht), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX,
                                       label_fs, label_th)
        box_w = max(CHIP, tw + 12)
        cv2.rectangle(img, (x, y), (x + box_w, y + tht + 10), col, -1)
        cv2.putText(img, label, (x + 6, y + tht + 4), cv2.FONT_HERSHEY_SIMPLEX,
                    label_fs, (0, 0, 0), label_th, cv2.LINE_AA)
        y_img = y + tht + 10
        img[y_img:y_img + CHIP, x:x + CHIP] = chip.img
        cv2.rectangle(img, (x, y_img), (x + CHIP, y_img + CHIP), col, 2)
        y = y_img + CHIP + int(16 * s)

    cv2.rectangle(img, (0, 0), (w, int(28 * s)), (0, 0, 0), -1)
    cv2.putText(img, hud, (8, int(20 * s)), cv2.FONT_HERSHEY_SIMPLEX,
                0.5 * s, (255, 255, 255), 1, cv2.LINE_AA)


poly_cfg = None   # set in main, read by draw() for the PENDING_IN label


def main():
    global poly_cfg
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--room", required=True, choices=["storage_1", "storage_2"])
    p.add_argument("--zones", default=f"{HERE}/dev/outputs/zones/storage_zones.json")
    p.add_argument("--model", default=f"{HERE}/backend/models/yolo26m-pose.onnx")
    p.add_argument("--reid", default=f"{HERE}/backend/models/osnet_x0_25_msmt17.onnx")
    p.add_argument("--face-model", default=f"{HERE}/backend/models/yolov6s_face.onnx")
    p.add_argument("--face-embed-model",
                   default=f"{HERE}/backend/models/ms1mv3_arcface_r18.onnx")
    p.add_argument("--face-db",
                   help="SQLite gallery db. If omitted, fall back to the old "
                        "first-entrant demo labels.")
    p.add_argument("--face-conf", type=float, default=FACE_SCORE_MIN)
    p.add_argument("--face-match-th", type=float, default=0.30)
    p.add_argument("--face-match-margin", type=float, default=0.10)
    p.add_argument("--face-cpu", action="store_true",
                   help="force CPU for face detect + embed")
    p.add_argument("--gallery-store-id",
                   help="If set, only match identities inside this store scope.")
    p.add_argument("--gallery-company-id",
                   help="If set, only match identities inside this company scope.")
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--out")
    p.add_argument("--limit", type=int)
    p.add_argument("--debug-ids", action="store_true",
                   help="print track id + zone state per frame instead of "
                        "drawing/writing a video, for inspecting track churn")
    a = p.parse_args()

    cfg = warehouse.Config(fps=a.fps)
    S.MODEL, S.REID_MODEL, S.CONF = a.model, a.reid, cfg.person_conf
    poly_cfg = cfg

    video = f"{HERE}/data/videos/cctv_storage/{a.room}.mp4"
    room_cfg = json.load(open(a.zones))[a.room]
    zone_norm = room_cfg["zone_norm"]
    if "min_box_h_frac" in room_cfg:
        cfg.min_box_h_frac = float(room_cfg["min_box_h_frac"])

    cap = cv2.VideoCapture(video)
    W, H = int(cap.get(3)), int(cap.get(4))
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    det, emb = S.Detector(), S.Embedder()
    face_det = FaceDetector(a.face_model, conf=a.face_conf)
    face_id = None
    if a.face_db:
        providers = ["CPUExecutionProvider"] if a.face_cpu else None
        face_id = FaceIdentifier(
            a.face_model, a.face_embed_model, a.face_db,
            face_conf=a.face_conf, match_threshold=a.face_match_th,
            match_margin=a.face_match_margin, providers=providers,
            prefer_coreml=not a.face_cpu)
        face_det = None
    tracker = Tracker(img_h=H, img_w=W, fps=cfg.fps)
    mon = warehouse.ZoneMonitor(zone_norm, W, H, cfg)
    min_h = cfg.min_box_h_frac * H

    first_entrant_name = FIRST_ENTRANT_NAME.get(a.room, "Staff")
    first_entrant_tid = None    # set on this run's first confirmed ZONE_ENTRY
    identities = {}             # tid -> name, grown as entries are confirmed
    chips = {}
    entry_windows = {}
    post_entry_frames = round(ENTRY_FACE_WINDOW_S * cfg.fps)

    out_video = a.out or f"{HERE}/demo/cctv_storage/{a.room}.mp4"
    os.makedirs(os.path.dirname(out_video), exist_ok=True)
    writer = None if a.debug_ids else cv2.VideoWriter(
        out_video, cv2.VideoWriter_fourcc(*"mp4v"), cfg.fps, (W, H))

    print(f"{a.room}: {W}x{H} @ {src_fps:.2f} -> {cfg.fps} fps, {total} frames")
    if face_id is None:
        print(f"  identity mode: demo labels  first entrant -> "
              f"{first_entrant_name!r}, everyone after -> {UNKNOWN!r}")
    else:
        print(f"  identity mode: real matching  db={a.face_db}  "
              f"th={a.face_match_th:.2f}  margin={a.face_match_margin:.2f}")

    step, acc, idx, t0 = cfg.fps / src_fps, 0.0, 0, time.perf_counter()
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
        if len(boxes) and min_h > 0:
            keep = (boxes[:, 3] - boxes[:, 1]) >= min_h
            boxes, kpts = boxes[keep], [k for k, m in zip(kpts, keep) if m]
        feats = emb(frame, boxes)
        live, removed = tracker.update(
            [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i])
             for i in range(len(boxes))], idx)
        mon.drop(removed, idx)
        for tid in removed:
            chips.pop(tid, None)
            win = entry_windows.pop(tid, None)
            if win is not None and win.resolved_identity() is not None:
                identities[tid] = win.resolved_identity()
        n_events = len(mon.events)
        items = mon.update([(t.track_id, t.ltrb, t.kpts, src == "detected")
                            for t, src in live], idx)
        state_of = {tid: st for tid, ltrb, kp, st, pt, streak in items}
        for tid, st in state_of.items():
            if face_id is None:
                continue
            if st == warehouse.PENDING_IN:
                entry_windows.setdefault(tid, EntryFaceWindow(idx))
            elif st == warehouse.OUTSIDE:
                entry_windows.pop(tid, None)
        for e in mon.events[n_events:]:
            if e["event"] != "ZONE_ENTRY":
                continue
            tid = e["track_id"]
            if face_id is not None:
                win = entry_windows.setdefault(tid, EntryFaceWindow(idx))
                win.end_frame = idx + post_entry_frames
                identities.setdefault(tid, UNKNOWN)
                continue
            if first_entrant_tid is None:
                first_entrant_tid = tid
                identities[tid] = first_entrant_name
            else:
                identities.setdefault(tid, UNKNOWN)

        if a.debug_ids:
            t_s = idx / cfg.fps
            ids = [(tid, st) for tid, ltrb, kp, st, pt, streak in items]
            if ids:
                print(f"t={t_s:6.1f}s  {ids}")
            idx += 1
            continue

        if items and face_id is None:
            faces = face_det(frame)
            for tid, ltrb, kp, st, pt, streak in items:
                hit = best_face_in_box(faces, ltrb)
                if hit is None:
                    continue
                box, score, lm = hit
                chip = chips.setdefault(tid, FaceChip())
                if score > chip.best_score:
                    crop = align(frame, lm, size=CHIP)
                    if crop is not None:
                        chip.best_score, chip.img = score, crop
        elif items:
            for tid, ltrb, kp, st, pt, streak in items:
                win = entry_windows.get(tid)
                if win is None or not win.is_collecting(idx):
                    continue
                dec = face_id.identify(
                    frame, ltrb, store_id=a.gallery_store_id,
                    company_id=a.gallery_company_id)
                chip = chips.setdefault(tid, FaceChip())
                if dec.observation is None:
                    continue
                if dec.observation.detector_score > chip.best_score:
                    chip.best_score = dec.observation.detector_score
                    chip.img = cv2.resize(dec.observation.chip_bgr, (CHIP, CHIP))
                    chip.match = dec
                win.consider(dec)
                if not dec.is_unknown:
                    identities[tid] = dec.display_name
                else:
                    identities.setdefault(tid, UNKNOWN)

        if face_id is not None:
            for tid, win in list(entry_windows.items()):
                st = state_of.get(tid)
                if win.end_frame is None:
                    if st not in (warehouse.PENDING_IN, warehouse.INSIDE):
                        entry_windows.pop(tid, None)
                    continue
                if idx < win.end_frame and st in (warehouse.PENDING_IN, warehouse.INSIDE):
                    continue
                resolved = win.resolved_identity()
                if resolved is not None:
                    identities[tid] = resolved
                else:
                    identities.setdefault(tid, UNKNOWN)
                entry_windows.pop(tid, None)

        n = mon.n_inside()
        hud = (f"f{idx}  t={idx/cfg.fps:6.1f}s  tracks={len(items)}  in_zone={n}  "
               f"entries={sum(1 for e in mon.events if e['event']=='ZONE_ENTRY')}")
        draw(frame, items, mon.poly, chips, identities, hud)
        writer.write(frame)
        idx += 1
        if idx % 200 == 0:
            print(f"  {idx}/{total}")

    cap.release()
    if writer:
        writer.release()
        from backend.src.perception.video_io import to_h264
        to_h264(out_video)
        with open(f"{HERE}/dev/outputs/{a.room}/{a.room}_demo_events.jsonl", "w") as fh:
            for e in mon.events:
                fh.write(json.dumps(e) + "\n")
        print(f"processed {idx} frames in {time.perf_counter() - t0:.0f}s")
        print(f"saved {out_video}")


if __name__ == "__main__":
    main()
