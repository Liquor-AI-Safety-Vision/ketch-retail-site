"""Cut a presentation clip out of the entrance pipeline.

Same perception as demo_entrance_face.py -- person detect, track, pelvis
anchor, zone with a 0.5 s entry confirmation, face detected on the person crop

from the moment the track appears OUTSIDE. What differs is only what reaches
the screen: no track ids, no confidence numbers, no debug HUD. A viewer should
read the state machine and the face, not the instrumentation.

Two modes:

  --label Unknown         nobody matches the watchlist (the normal case)
  --alert                 one track is treated as a watchlist hit: red box,
                          red frame border, a banner, and the face panel
                          relabelled

The alert is SIMULATED. There is no gallery and no ArcFace here -- the
measurement in probe_entrance_identity.py showed these faces do not separate
identities at this resolution, so a real match would be dishonest. The chip on
screen is the real best face this pipeline captured for that person; only the
verdict attached to it is staged.

The clip window is written, but the video is processed from the start, because
a track's history -- was it ever OUTSIDE, what is its best face so far --
cannot be reconstructed from the middle.

    python3 ai/tools/demo_entrance_clip.py --start-s 50 --end-s 77 \
        --out ai/demo/entrance/entrance_unknown.mp4
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[2]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = _Path(__file__).resolve().parents[2]
for p in (str(ROOT),):
    if p not in sys.path:
        sys.path.insert(0, p)

from ai.src.perception import stream_track as S  # noqa: E402
from ai.src.perception import zone_monitor  # noqa: E402
from ai.src.perception.face_align import FaceDetector  # noqa: E402
from ai.src.perception.person_tracker import Detection, Tracker  # noqa: E402
from ai.src.common.paths import model_path, outputs_dir

from demo_entrance_face import FaceWindow, face_in_person, transcode  # noqa: E402

STATE_TEXT = {zone_monitor.OUTSIDE: "OUTSIDE",
              zone_monitor.PENDING_IN: "PENDING",
              zone_monitor.INSIDE: "INSIDE"}
STATE_COLOR = {zone_monitor.OUTSIDE: (170, 170, 170),
               zone_monitor.PENDING_IN: (60, 200, 255),
               zone_monitor.INSIDE: (90, 240, 90)}
ALERT = (60, 60, 235)
FACE_BOX = (255, 120, 255)

PANEL = 220          # face thumbnail side, px
MARGIN_TOP = 130     # clears the camera's own timestamp burn-in
MARGIN_RIGHT = 44


def outlined(img, text, org, scale, colour, thick=2):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0),
                thick + 4, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, colour,
                thick, cv2.LINE_AA)


def draw_panel(frame, chip, label, colour):
    """Face thumbnail with its verdict, top-right, inset from both edges."""
    H, W = frame.shape[:2]
    x2, y1 = W - MARGIN_RIGHT, MARGIN_TOP
    x1, y2 = x2 - PANEL, y1 + PANEL
    bar = 54

    cv2.rectangle(frame, (x1 - 6, y1 - 6), (x2 + 6, y2 + bar + 6), (20, 20, 20), -1)
    if chip is not None and chip.size:
        frame[y1:y2, x1:x2] = cv2.resize(chip, (PANEL, PANEL),
                                         interpolation=cv2.INTER_CUBIC)
    else:
        cv2.rectangle(frame, (x1, y1), (x2, y2), (45, 45, 45), -1)
        outlined(frame, "no face", (x1 + 46, y1 + PANEL // 2), 0.7, (140, 140, 140), 1)
    cv2.rectangle(frame, (x1, y2), (x2, y2 + bar), (20, 20, 20), -1)
    (tw, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.95, 2)
    outlined(frame, label, (x1 + max(6, (PANEL - tw) // 2), y2 + 38), 0.95, colour, 2)
    cv2.rectangle(frame, (x1 - 6, y1 - 6), (x2 + 6, y2 + bar + 6), colour, 3)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--video", default=str(ROOT / "data/videos/entrance/entrance_180s.mp4"))
    p.add_argument("--zone", default=str(outputs_dir() / "zones/entrance_zone.json"))
    p.add_argument("--model", default=model_path("yolo26s-pose.onnx"))
    p.add_argument("--face-model", default=model_path("yolov6s_face.onnx"))
    p.add_argument("--out", required=True)
    p.add_argument("--start-s", type=float, required=True)
    p.add_argument("--end-s", type=float, required=True)
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--conf", type=float, default=0.45)
    p.add_argument("--face-conf", type=float, default=0.4)
    p.add_argument("--min-face-h", type=int, default=32)
    p.add_argument("--face-top-frac", type=float, default=0.35)
    p.add_argument("--entry-s", type=float, default=0.5)
    p.add_argument("--label", default="Unknown")
    p.add_argument("--alert", action="store_true",
                   help="stage a watchlist hit on one track")
    p.add_argument("--alert-label", default="Shoplifter")
    p.add_argument("--alert-banner", default="Regular shoplifter detected")
    p.add_argument("--alert-track", type=int, default=0,
                   help="0 = pick the track that confirms entry inside the clip")
    a = p.parse_args()

    cfg = zone_monitor.Config(fps=a.fps, anchor="pelvis")
    cfg.person_conf = a.conf
    cfg.entry_confirmation_s = a.entry_s
    cfg.__post_init__()
    cfg.entry_frames = max(1, math.ceil(a.entry_s * a.fps))
    cfg.min_box_h_frac = 0.0
    S.MODEL, S.CONF = a.model, cfg.person_conf

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {a.video}")
    W, H = int(cap.get(3)), int(cap.get(4))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    zone_norm = json.loads(Path(a.zone).read_text())["entrance"]["zone_norm"]

    det, emb = S.Detector(), S.Embedder()
    fd = FaceDetector(a.face_model, conf=a.face_conf)
    tracker = Tracker(img_h=H, img_w=W, fps=cfg.fps)
    mon = zone_monitor.ZoneMonitor(zone_norm, W, H, cfg)

    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = out_path.with_name(out_path.stem + "__mp4v.mp4")
    writer = cv2.VideoWriter(str(raw_path), cv2.VideoWriter_fourcc(*"mp4v"),
                             cfg.fps, (W, H))

    windows: dict[int, FaceWindow] = {}
    came_from_outside: set[int] = set()
    target = a.alert_track or None
    panel_chip, panel_label, panel_colour = None, a.label, (235, 235, 235)
    written = 0

    step, acc, idx = cfg.fps / src_fps, 0.0, 0
    while True:
        if not cap.grab():
            break
        acc += step
        if acc < 1.0:
            continue
        acc -= 1.0
        t = idx / cfg.fps
        if t > a.end_s:
            break
        ok, frame = cap.retrieve()
        if not ok:
            break

        boxes, kpts = det(frame)
        feats = emb(frame, boxes)
        live, removed = tracker.update(
            [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i])
             for i in range(len(boxes))], idx)
        mon.drop(removed, idx)
        before = len(mon.events)
        items = mon.update([(tr.track_id, tr.ltrb, tr.kpts, src == "detected")
                            for tr, src in live], idx)

        # The staged hit: whoever walks IN during the clip. The came_from_outside
        # test is not optional -- ZONE_ENTRY also fires for tracks first seen
        # already inside, which are people heading for the door. One of those
        # was picked first here, and since out-bound tracks are never given a
        # face, the alert silently never fired.
        if a.alert and target is None:
            for e in mon.events[before:]:
                tid = int(e["track_id"])
                if (e["event"] == "ZONE_ENTRY" and a.start_s <= t <= a.end_s
                        and tid in came_from_outside):
                    target = tid
                    break

        drawn = []
        for tid, ltrb, _k, st, _pt, _s in items:
            if st == zone_monitor.OUTSIDE:
                came_from_outside.add(tid)
            elif tid not in came_from_outside:
                continue          # walking out; not what this demo is about
            win = windows.setdefault(tid, FaceWindow(a.min_face_h))
            faces = []
            x1, y1, x2, y2 = ltrb
            for box, score, chip, rel in face_in_person(fd, frame, ltrb):
                if rel > a.face_top_frac:
                    continue
                faces.append(box)
                win.consider(idx, box, score, chip)
            drawn.append((tid, ltrb, st, faces))

        # the panel follows the staged target once it has a face, otherwise
        # whichever inbound person currently has the best face on file
        pick = None
        if a.alert and target is not None and windows.get(target, None) \
                and windows[target].best is not None:
            pick = target
        else:
            cands = [(w.best[0], t_) for t_, w in windows.items()
                     if w.best is not None and t_ in {d[0] for d in drawn}]
            if cands:
                pick = max(cands)[1]
        if pick is not None:
            panel_chip = windows[pick].best[3]
            hit = a.alert and pick == target
            panel_label = a.alert_label if hit else a.label
            panel_colour = ALERT if hit else (235, 235, 235)
        elif not drawn:
            # nobody inbound on screen: clear it. Leaving the last person's
            # face up while the doorway is empty reads as a live identification
            # of someone who is not there.
            panel_chip, panel_label = None, a.label
            panel_colour = (235, 235, 235)

        if t < a.start_s:
            idx += 1
            continue

        cv2.polylines(frame, [mon.poly.astype(np.int32)], True, (255, 255, 255), 3)
        for tid, ltrb, st, faces in drawn:
            hit = a.alert and tid == target and windows[tid].best is not None
            col = ALERT if hit else STATE_COLOR[st]
            x1, y1, x2, y2 = (int(v) for v in ltrb)
            cv2.rectangle(frame, (x1, y1), (x2, y2), col, 4 if hit else 3)
            outlined(frame, STATE_TEXT[st], (x1, max(24, y1 - 12)), 0.85, col)
            for fx1, fy1, fx2, fy2 in faces:
                cv2.rectangle(frame, (int(fx1), int(fy1)), (int(fx2), int(fy2)),
                              ALERT if hit else FACE_BOX, 2)

        if a.alert and target is not None and windows.get(target) \
                and windows[target].best is not None:
            cv2.rectangle(frame, (6, 6), (W - 7, H - 7), ALERT, 14)
            (tw, th), _ = cv2.getTextSize(a.alert_banner,
                                          cv2.FONT_HERSHEY_SIMPLEX, 1.15, 3)
            bx = (W - tw) // 2
            cv2.rectangle(frame, (bx - 26, 34), (bx + tw + 26, 34 + th + 30),
                          (20, 20, 20), -1)
            cv2.rectangle(frame, (bx - 26, 34), (bx + tw + 26, 34 + th + 30),
                          ALERT, 3)
            outlined(frame, a.alert_banner, (bx, 34 + th + 8), 1.15, ALERT, 3)

        draw_panel(frame, panel_chip, panel_label, panel_colour)
        writer.write(frame)
        written += 1
        idx += 1

    cap.release()
    writer.release()
    transcode(raw_path, out_path)
    print(f"{a.start_s:.0f}-{a.end_s:.0f}s -> {written} frames @ {cfg.fps} fps "
          f"({written / cfg.fps:.1f}s)")
    if a.alert:
        print(f"staged watchlist hit on track #{target}")
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
