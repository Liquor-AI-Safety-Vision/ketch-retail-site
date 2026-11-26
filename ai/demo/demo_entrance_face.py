"""Entrance demo: capture a face on the way in, keep the best one per visit.

The first version of this file only looked for faces once a track was
confirmed INSIDE, and measuring it showed that was backwards. At frame 733 a
man is walking in through the door facing the camera, the face detector scores
him 0.69 -- and the demo reported faces=0, because his pelvis had not crossed
the divider yet. The best view of someone entering happens BEFORE the zone
confirms them, not after: once inside, they have turned away and the camera
sees the back of their head.

So face capture is not gated on the zone at all:

    person detect -> track -> face detect on the person crop, every frame
                       |                        |
                 pelvis anchor            keep the best one
                       |                    for this track
                  zone + confirm                 |
                       |                         |
                   ZONE_ENTRY  ------------------+--> that visit's face

The zone decides WHEN a visit is real, not WHETHER to look for a face.

Two exclusions, both deliberate:

  * a track first seen already inside the zone is skipped entirely. Those are
    people walking up from the shop floor towards the door -- the outward
    direction -- and identifying them is not what this is for. warehouse's
    monitor already distinguishes them (BORN_INSIDE_ZONE), and here the test is
    simply whether the track was ever observed OUTSIDE.
  * ZONE_EXIT is still tracked and drawn, but nothing acts on it. It is in the
    picture so the demo shows a complete state machine, not because leaving
    matters here.

Nothing is recognised: no gallery, no ArcFace, no watchlist. This stops at
"here is the best face of someone who walked in", which is the input the
retrieval step will take.

Conventions inherited rather than reinvented:

  * the anchor is the pelvis, per scripts/zones.py -- feet are occluded far too
    often, and extrapolating down to the floor was measured landing outside the
    person's own box in 45% of frames
  * confirmation is expressed in SECONDS, not frames, so the same setting means
    the same delay at any frame rate: this demo runs at 10 fps, production
    targets 5 fps. It is rounded UP, not to nearest -- 0.5 s at 5 fps is 2.5
    frames and must become 3. Python's round() would make it 2, because round()
    breaks ties to even.

    python3 ai/tools/demo_entrance_face.py
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
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = _Path(__file__).resolve().parents[2]
for p in (str(ROOT),):
    if p not in sys.path:
        sys.path.insert(0, p)

from ai.src.perception import stream_track as S  # noqa: E402
from ai.src.perception import warehouse  # noqa: E402
from ai.src.perception.face_align import FaceDetector  # noqa: E402
from ai.src.perception.uae_tracker import Detection, Tracker  # noqa: E402
from ai.src.common.paths import model_path, outputs_dir

STATE_COLOR = {
    warehouse.OUTSIDE: (150, 150, 150),      # grey: seen, but not our problem
    warehouse.PENDING_IN: (60, 200, 255),    # amber: inside, not yet confirmed
    warehouse.INSIDE: (90, 240, 90),         # green: confirmed
}
FACE_COLOR = (255, 120, 255)
REJECT_COLOR = (80, 80, 200)   # kept on screen so the filter is visible


def face_in_person(det, frame, ltrb, pad=0.06, target=640):
    """Detect faces inside one person box, returned in FRAME coordinates.

    The crop is enlarged to the detector's own input size before it runs. A
    person at this entrance is roughly 250-500 px tall in a 1280x1440 frame, so
    their face is a few dozen pixels; letterboxing the whole frame to 640 would
    shrink it further, while letterboxing the crop enlarges it instead. Same
    detector, same weights, more pixels on the part that matters.
    """
    H, W = frame.shape[:2]
    x1, y1, x2, y2 = ltrb
    bw, bh = x2 - x1, y2 - y1
    cx1 = int(max(0, x1 - pad * bw))
    cy1 = int(max(0, y1 - pad * bh))
    cx2 = int(min(W, x2 + pad * bw))
    cy2 = int(min(H, y2 + pad * bh))
    if cx2 - cx1 < 16 or cy2 - cy1 < 16:
        return []
    crop = frame[cy1:cy2, cx1:cx2]
    s = min(target / max(crop.shape[0], crop.shape[1]), 4.0)
    if s > 1.0:
        crop = cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_LINEAR)
    else:
        s = 1.0

    out = []
    for box, score, lm in det(crop):
        fx1, fy1, fx2, fy2 = (np.asarray(box, np.float64) / s) + [cx1, cy1, cx1, cy1]
        # The chip is cut from the ORIGINAL frame, not from the upscaled crop.
        # Upscaling helped the detector find the face; it adds no information,
        # and saving interpolated pixels would make the chip look better than
        # the evidence actually is.
        chip = frame[max(0, int(fy1)):int(fy2), max(0, int(fx1)):int(fx2)].copy()
        # Where the face sits inside the person box, 0 at the top. Heads are at
        # the top of people; the junk this filters out -- shirt fabric, shelf
        # behind a shoulder, floor tile -- is not, and confidence alone could
        # not tell them apart (real faces scored 0.71-0.80, junk 0.74).
        rel = ((fy1 + fy2) / 2 - y1) / max(1e-6, bh)
        out.append(((float(fx1), float(fy1), float(fx2), float(fy2)),
                    float(score), chip, float(rel)))
    return out


class FaceWindow:
    """The best face seen for one track, and the evidence behind it.

    Ranked on detector score, but only among faces at least `min_h` tall.
    Score alone is not safe as the sole key: measured on this footage, the
    detector returned boxes 10 and 11 px high at scores up to 0.49, which would
    outrank a genuine 90 px face at 0.44 and hand the gallery a chip with no
    identity in it. Height first as a gate, score to choose -- rather than
    ranking on height, which would prefer a large blurred profile over a
    smaller sharp one.
    """

    def __init__(self, min_h: int):
        self.min_h = min_h
        self.best = None          # (score, frame_idx, box, chip)
        self.seen = 0             # faces of any size, for the miss diagnosis
        self.rejected_small = 0

    def consider(self, frame_idx, box, score, chip):
        self.seen += 1
        h = box[3] - box[1]
        if h < self.min_h:
            self.rejected_small += 1
            return
        if self.best is None or score > self.best[0]:
            self.best = (float(score), int(frame_idx), tuple(box), chip)


def transcode(raw: Path, final: Path) -> None:
    """mp4v -> H.264, so the result opens in QuickTime and in a browser.

    If ffmpeg is missing the mp4v file is kept under the intended name rather
    than deleted: an unplayable video is still worth more than none, and the
    warning says why it will not open.
    """
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        raw.replace(final)
        print("! ffmpeg not found: left the mp4v file in place. QuickTime will "
              "refuse it; play it with VLC or install ffmpeg and re-run.")
        return
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
           "-movflags", "+faststart", str(final)]
    if subprocess.run(cmd).returncode == 0:
        raw.unlink(missing_ok=True)
    else:
        raw.replace(final)
        print("! ffmpeg transcode failed; kept the mp4v file")


def draw(frame, items, faces, poly, hud, cfg, born_inside=()):
    cv2.polylines(frame, [poly.astype(np.int32)], True, (255, 255, 255), 3)

    for tid, ltrb, _kpts, st, pt, streak in items:
        x1, y1, x2, y2 = (int(v) for v in ltrb)
        col = STATE_COLOR.get(st, (150, 150, 150))
        cv2.rectangle(frame, (x1, y1), (x2, y2), col, 3)
        # the pelvis, drawn because it -- not the box -- is what the zone test
        # actually reads; a surprising in/out decision is only explainable if
        # the point it was made on is visible
        cv2.circle(frame, (int(pt[0]), int(pt[1])), 7, col, -1)
        cv2.circle(frame, (int(pt[0]), int(pt[1])), 7, (0, 0, 0), 2)
        tag = f"#{tid} {st}"
        if st == warehouse.PENDING_IN:
            tag += f" {streak}/{cfg.entry_frames}"
        if tid in born_inside:
            # says WHY this person never gets a face box, so an empty result
            # reads as a decision rather than as a detector failure
            tag += " out-bound/no-face"
        for th, c in ((5, (0, 0, 0)), (2, col)):
            cv2.putText(frame, tag, (x1, max(18, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, c, th, cv2.LINE_AA)

    for tid, (fx1, fy1, fx2, fy2), score, keep in faces:
        p1, p2 = (int(fx1), int(fy1)), (int(fx2), int(fy2))
        col = FACE_COLOR if keep else REJECT_COLOR
        cv2.rectangle(frame, p1, p2, col, 2 if keep else 1)
        lab = f"face {score:.2f} {p2[0]-p1[0]}x{p2[1]-p1[1]}"
        if not keep:
            lab += " LOW"      # too far down the body to be a head
        for th, c in ((4, (0, 0, 0)), (1, col)):
            cv2.putText(frame, lab, (p1[0], max(12, p1[1] - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, th, cv2.LINE_AA)

    cv2.rectangle(frame, (0, 0), (frame.shape[1], 34), (0, 0, 0), -1)
    cv2.putText(frame, hud, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                (255, 255, 255), 1, cv2.LINE_AA)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--video", default=str(ROOT / "data/videos/entrance/entrance_180s.mp4"))
    p.add_argument("--zone", default=str(outputs_dir() / "zones/entrance_zone.json"))
    p.add_argument("--zone-name", default="entrance")
    # s, not m. Measured on this Mac: yolo26m-pose takes 515 ms per frame
    # against 45 ms for yolo26s-pose -- 11x, which is not a size difference.
    # CoreML accepts only 172 of the m graph's 596 nodes and splits it into 9
    # partitions, so every frame bounces between the ANE and the CPU. onnxruntime
    # says so on load; it is the warning that is easy to filter out and ignore.
    p.add_argument("--model", default=model_path("yolo26s-pose.onnx"))
    p.add_argument("--face-model", default=model_path("yolov6s_face.onnx"))
    p.add_argument("--out", default=str(ROOT / "data/videos/entrance/entrance_zone_face.mp4"))
    p.add_argument("--events", default=str(outputs_dir() / "entrance/entrance_events.jsonl"))
    p.add_argument("--fps", type=float, default=10.0, help="downsample target")
    p.add_argument("--conf", type=float, default=0.45, help="person confidence")
    p.add_argument("--face-conf", type=float, default=0.4)
    p.add_argument("--entry-s", type=float, default=0.5,
                   help="0.5, not warehouse's 1.0: this camera's view of the "
                        "doorway is narrow, so a second of confirmation is a "
                        "large part of the time a person is even in frame")
    p.add_argument("--min-face-h", type=int, default=32,
                   help="ignore faces shorter than this when choosing the best "
                        "one. Measured here: junk boxes came back 10-29 px tall "
                        "at scores up to 0.49, real ones 52-102 px")
    p.add_argument("--face-top-frac", type=float, default=0.35,
                   help="a face must sit within this fraction of the person box, "
                        "measured from the top. Heads are at the top of people; "
                        "confidence could not separate real faces (0.71-0.80) "
                        "from fabric and background (0.74), and this can")
    p.add_argument("--faces-dir", default=str(outputs_dir() / "entrance/best_faces"))
    p.add_argument("--min-h", type=float, default=0.0,
                   help="minimum person height as a fraction of frame height. "
                        "Default 0 = off: warehouse.Config's 0.14 is a "
                        "storage_1 measurement and applying it here unmeasured "
                        "would drop real tracks silently")
    p.add_argument("--max-frames", type=int, default=0)
    a = p.parse_args()

    cfg = warehouse.Config(fps=a.fps, anchor="pelvis")
    cfg.person_conf = a.conf
    cfg.entry_confirmation_s = a.entry_s
    cfg.__post_init__()          # recompute entry_frames/exit_frames
    # Round UP, overriding Config's round(). "Confirm over half a second" has
    # to mean at least half a second at every frame rate, and round() gives
    # 0.5 s * 5 fps = 2.5 -> 2 frames = 0.4 s. Ties break to even in Python,
    # so this is not even consistently downward -- 0.5 s at 7 fps rounds up.
    cfg.entry_frames = max(1, math.ceil(cfg.entry_confirmation_s * cfg.fps))
    cfg.min_box_h_frac = a.min_h
    S.MODEL, S.CONF = a.model, cfg.person_conf

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {a.video}")
    W, H = int(cap.get(3)), int(cap.get(4))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    zone_norm = json.loads(Path(a.zone).read_text())[a.zone_name]["zone_norm"]
    det, emb = S.Detector(), S.Embedder()
    face_det = FaceDetector(a.face_model, conf=a.face_conf)
    tracker = Tracker(img_h=H, img_w=W, fps=cfg.fps)
    mon = warehouse.ZoneMonitor(zone_norm, W, H, cfg)

    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Path(a.events).parent.mkdir(parents=True, exist_ok=True)
    # mp4v is MPEG-4 Part 2. QuickTime refuses to play it ("format not
    # supported"), so a run that succeeded still looks like a failure to
    # anyone who double-clicks the file. OpenCV cannot be relied on for H.264
    # -- the avc1 encoder is absent from most wheels -- so write mp4v to a
    # temporary file and let ffmpeg transcode at the end.
    raw_path = out_path.with_name(out_path.stem + "__mp4v.mp4")
    writer = cv2.VideoWriter(str(raw_path), cv2.VideoWriter_fourcc(*"mp4v"),
                             cfg.fps, (W, H))

    print(f"{Path(a.video).name}: {W}x{H} @ {src_fps:.2f} -> {cfg.fps} fps, "
          f"{total} frames")
    print(f"person conf={cfg.person_conf}  anchor={cfg.anchor}  "
          f"entry={cfg.entry_confirmation_s}s = {cfg.entry_frames}f @ {cfg.fps}fps "
          f"(production 5 fps -> {max(1, math.ceil(cfg.entry_confirmation_s * 5))}f)")
    print(f"face must lie within the top {a.face_top_frac:.2f} of the person box")
    print(f"face conf={a.face_conf} on the person crop, from the moment a "
          f"track appears OUTSIDE; best face kept per track (min {a.min_face_h}px)")
    print("tracks first seen already inside the zone are skipped: they are "
          "walking out, not in")
    if cfg.min_box_h_frac <= 0:
        print("min person height: off")

    faces_dir = Path(a.faces_dir)
    faces_dir.mkdir(parents=True, exist_ok=True)

    min_h = cfg.min_box_h_frac * H
    step, acc, idx = cfg.fps / src_fps, 0.0, 0
    n_small = frames_with_face = n_faces = 0
    face_sizes: list[int] = []
    windows: dict[int, FaceWindow] = {}
    came_from_outside: set[int] = set()
    born_inside: set[int] = set()
    captured: list[dict] = []
    rejected_position = 0
    t0 = time.perf_counter()

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
        if a.max_frames and idx >= a.max_frames:
            break

        boxes, kpts = det(frame)
        if len(boxes) and min_h > 0:
            keep = (boxes[:, 3] - boxes[:, 1]) >= min_h
            n_small += int((~keep).sum())
            boxes, kpts = boxes[keep], [k for k, m in zip(kpts, keep) if m]
        feats = emb(frame, boxes)
        live, removed = tracker.update(
            [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i])
             for i in range(len(boxes))], idx)
        mon.drop(removed, idx)
        # Snapshot BEFORE update: update() is what appends the entry events, so
        # taking the length afterwards always yields an empty slice -- which is
        # why the first run of this reported 0 captures against 18 entries.
        events_before = len(mon.events)
        items = mon.update([(t.track_id, t.ltrb, t.kpts, src == "detected")
                            for t, src in live], idx)

        faces = []
        for tid, ltrb, _k, st, _pt, _s in items:
            # The direction test, and the only thing the zone is used for here.
            # A track that has been OUTSIDE at some point is walking in; one
            # that was never OUTSIDE was already on the shop floor when it
            # appeared, and is on its way out.
            if st == warehouse.OUTSIDE:
                came_from_outside.add(tid)
            elif tid not in came_from_outside:
                born_inside.add(tid)
                continue
            win = windows.setdefault(tid, FaceWindow(a.min_face_h))
            for box, score, chip, rel in face_in_person(face_det, frame, ltrb):
                keep = rel <= a.face_top_frac
                faces.append((tid, box, score, keep))
                if not keep:
                    rejected_position += 1
                    continue
                face_sizes.append(int(box[3] - box[1]))
                win.consider(idx, box, score, chip)
        n_faces += len(faces)
        frames_with_face += bool(faces)

        # A visit becomes real at ZONE_ENTRY; that is when the face collected
        # on the way in is handed over. Not at the end of the track: by then
        # the person is deep in the shop and the window has nothing to add.
        for e in mon.events[events_before:]:
            if e["event"] != "ZONE_ENTRY":
                continue
            tid = int(e["track_id"])
            win = windows.get(tid)
            rec = {"t": e["t"], "frame": idx, "track_id": tid,
                   "reason_codes": e["reason_codes"],
                   "faces_seen": win.seen if win else 0,
                   "rejected_small": win.rejected_small if win else 0}
            if win is not None and win.best is not None:
                score, fidx, box, chip = win.best
                rec.update(score=round(score, 3), face_frame=fidx,
                           face_w=int(box[2] - box[0]), face_h=int(box[3] - box[1]))
                name = f"t{e['t']:06.1f}_track{tid}_f{fidx}_s{score:.2f}_{rec['face_h']}px.png"
                if chip.size:
                    cv2.imwrite(str(faces_dir / name), chip)
                    rec["chip"] = name
            captured.append(rec)

        entries = sum(1 for e in mon.events if e["event"] == "ZONE_ENTRY")
        draw(frame, items, faces, mon.poly,
             f"f{idx}  t={idx / cfg.fps:6.1f}s  tracks={len(items)}  "
             f"in_zone={mon.n_inside()}  entries={entries}  faces={len(faces)}",
             cfg, born_inside)
        writer.write(frame)
        idx += 1

    cap.release()
    writer.release()
    transcode(raw_path, out_path)
    with open(a.events, "w") as fh:
        for e in mon.events:
            fh.write(json.dumps(e) + "\n")

    dt = time.perf_counter() - t0
    entries = [e for e in mon.events if e["event"] == "ZONE_ENTRY"]
    exits = [e for e in mon.events if e["event"] == "ZONE_EXIT"]
    print(f"\nprocessed {idx} frames in {dt:.1f}s ({idx / max(dt, 1e-9):.1f} fps)")
    print(f"anchor source: {mon.anchor_src}  (coasted {mon.coasted})")
    print(f"tracks: {len(came_from_outside)} came from outside, "
          f"{len(born_inside)} skipped as out-bound")
    print(f"ZONE_ENTRY: {len(entries)}   ZONE_EXIT: {len(exits)} (drawn, not acted on)")
    # Derived here rather than read from the reason code, so this number does
    # not depend on warehouse's labelling being right -- it was not, and the
    # first run of this file reported zero of them while eleven tracks were
    # being skipped.
    born = [e for e in entries if int(e["track_id"]) not in came_from_outside]
    if born:
        # raising the divider makes this more likely, and every one of these is
        # an entry whose face was never collected
        print(f"! {len(born)} of them are BORN_INSIDE_ZONE: the track was first "
              "seen already inside, so no face was captured on the way in")

    print(f"\nfaces seen: {n_faces} across {frames_with_face} frames; "
          f"{rejected_position} rejected as too low in the person box "
          f"(> {a.face_top_frac:.2f}H)")
    if face_sizes:
        arr = np.asarray(face_sizes)
        print(f"face height px: min {arr.min()}  p50 {int(np.percentile(arr, 50))}  "
              f"max {arr.max()}   (ArcFace wants >=112)")

    with_face = [c for c in captured if "chip" in c]
    print(f"\nbest face per confirmed entry: {len(with_face)}/{len(captured)}")
    print(f"{'t':>7} {'track':>6} {'seen':>5} {'small':>6} "
          f"{'score':>6} {'w':>4} {'h':>4}")
    for c in captured:
        if "chip" in c:
            print(f"{c['t']:7.1f} {c['track_id']:6} {c['faces_seen']:5} "
                  f"{c['rejected_small']:6} {c['score']:6.2f} "
                  f"{c['face_w']:4} {c['face_h']:4}")
        else:
            print(f"{c['t']:7.1f} {c['track_id']:6} {c['faces_seen']:5} "
                  f"{c['rejected_small']:6}     -- no usable face --")

    caps_path = Path(a.events).with_name("entrance_captures.jsonl")
    with open(caps_path, "w") as fh:
        for c in captured:
            fh.write(json.dumps(c) + "\n")

    print(f"\nvideo    -> {a.out}")
    print(f"events   -> {a.events}")
    print(f"captures -> {caps_path}")
    print(f"chips    -> {a.faces_dir}")


if __name__ == "__main__":
    main()
