"""Live GUI view of the tracker, fed by a simulated RTSP camera.

A producer thread reads the file, downsamples it to TARGET_FPS and paces the
frames against the wall clock, pushing into a depth-1 queue. If the pipeline is
still busy the frame is DROPPED rather than queued - that is what a live camera
does, and it means the view can never drift behind real time the way it would if
frames were allowed to pile up. Pausing the window does not pause the camera: the
producer keeps running and dropping, so resuming shows the present, not a backlog.

Detection, embedding and tracking are identical to stream_track.py - imported
import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[3]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

from it, not copied - so what you see here is what the offline run produces.

Controls
    q / ESC   quit
    space     pause the view (the camera keeps running)
    s         save the current rendered frame
    k         toggle skeleton
    w         toggle wrist markers
"""
import queue
import threading
import time
import warnings
from collections import deque

import cv2
import numpy as np

from ai.src.perception.stream_track import SKELETON, Detector, Embedder, color
from ai.src.perception.uae_tracker import KPT_L_WRIST, KPT_R_WRIST, Config, Detection, Tracker

warnings.filterwarnings("ignore", message=".*encountered in matmul",
                        category=RuntimeWarning)

VIDEO = "videos/top_view.mp4"
TARGET_FPS = 10.0
START_FRAME = 0         # seek before streaming; this footage is empty until ~4300
LOOP = True             # restart at EOF, so the "camera" never goes offline
DISPLAY_MAX_W = 1280    # the source is 1080p; scale down for the window only
WINDOW = "liquor store - counter cam (simulated RTSP)"


# ---------------------------------------------------------------- camera sim
def producer(q, stop, stats):
    """Emit frames at TARGET_FPS, dropping whatever the consumer cannot take."""
    cap = cv2.VideoCapture(VIDEO)
    if START_FRAME:
        cap.set(cv2.CAP_PROP_POS_FRAMES, START_FRAME)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or TARGET_FPS
    step, acc, idx = TARGET_FPS / src_fps, 0.0, 0
    t0 = time.perf_counter()
    while not stop.is_set():
        # grab() demuxes without the colour conversion and copy that read() does,
        # so the frames the downsample throws away cost very little
        if not cap.grab():
            if not LOOP:
                break
            cap.release()
            cap = cv2.VideoCapture(VIDEO)
            if START_FRAME:
                cap.set(cv2.CAP_PROP_POS_FRAMES, START_FRAME)
            stats["loops"] += 1
            continue
        acc += step
        if acc < 1.0:
            continue
        acc -= 1.0
        ok, frame = cap.retrieve()
        if not ok:
            break
        due = t0 + idx / TARGET_FPS          # pace to the wall clock
        slack = due - time.perf_counter()
        if slack > 0:
            time.sleep(slack)
        try:
            q.put_nowait((idx, frame))
        except queue.Full:
            stats["dropped"] += 1
        idx += 1
    cap.release()
    q.put((None, None))


# ---------------------------------------------------------------- overlay
def draw_person(img, tid, ltrb, kpts, source, show_skel, show_wrist):
    c = color(tid)
    predicted = source == "predicted"
    x1, y1, x2, y2 = (int(v) for v in ltrb)
    # a coasting track is drawn thin and marked, so a Kalman guess is never
    # mistaken for an observation
    cv2.rectangle(img, (x1, y1), (x2, y2), c, 1 if predicted else 2)

    tag = f"ID{tid}?" if predicted else f"ID{tid}"
    (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.rectangle(img, (x1, y1 - th - 6), (x1 + tw + 6, y1), c, -1)
    cv2.putText(img, tag, (x1 + 3, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

    if kpts is None:
        return
    if show_skel:
        for a, b in SKELETON:
            if kpts[a, 2] > 0.5 and kpts[b, 2] > 0.5:
                cv2.line(img, tuple(kpts[a, :2].astype(int)),
                         tuple(kpts[b, :2].astype(int)), c, 1)
    if show_wrist:
        # the two wrists are what problem 2 is built on, so show them explicitly:
        # filled = confident enough to reason from, hollow = recorded but flagged
        for idx, label in ((KPT_L_WRIST, "L"), (KPT_R_WRIST, "R")):
            x, y, conf = kpts[idx]
            p = (int(x), int(y))
            valid = conf >= Config.WRIST_CONF_TH
            cv2.circle(img, p, 6, (255, 255, 255), -1 if valid else 1)
            cv2.circle(img, p, 6, (0, 0, 0), 1)
            cv2.putText(img, label, (p[0] + 8, p[1] + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)


def draw_hud(img, lines, paused):
    h = 18 * len(lines) + 10
    panel = img[0:h, 0:430].copy()
    cv2.rectangle(panel, (0, 0), (430, h), (0, 0, 0), -1)
    img[0:h, 0:430] = cv2.addWeighted(panel, 0.55, img[0:h, 0:430], 0.45, 0)
    for i, text in enumerate(lines):
        cv2.putText(img, text, (10, 20 + 18 * i),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (240, 240, 240), 1)
    if paused:
        cv2.putText(img, "PAUSED (camera still running)",
                    (10, img.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (60, 220, 255), 2)


# ---------------------------------------------------------------- main
def main():
    cap = cv2.VideoCapture(VIDEO)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {VIDEO}")
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()

    det, emb = Detector(), Embedder()
    tracker = Tracker(img_h=H, img_w=W, fps=TARGET_FPS)
    scale = min(1.0, DISPLAY_MAX_W / W)

    print(f"camera : {VIDEO}  {W}x{H} @ {src_fps:.3f} -> {TARGET_FPS} fps")
    print(f"window : {int(W * scale)}x{int(H * scale)}  (display scale {scale:.2f})")
    print(f"tracker: max_age={Config.MAX_AGE_S}s  person_grace={Config.PERSON_GRACE_S}s  "
          f"embed_gate={Config.STEP_B_EMBED_GATE}")
    print("keys   : q/ESC quit | space pause | s snapshot | k skeleton | w wrists")

    q, stop = queue.Queue(maxsize=1), threading.Event()
    stats = {"dropped": 0, "loops": 0}
    threading.Thread(target=producer, args=(q, stop, stats), daemon=True).start()

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, int(W * scale), int(H * scale))

    lat = deque(maxlen=30)
    shown = deque(maxlen=30)
    seen_ids, n_snap = set(), 0
    paused, show_skel, show_wrist = False, True, True
    canvas = None
    t_prev = time.perf_counter()

    while True:
        if not paused:
            idx, frame = q.get()
            if idx is None:
                break
            t_frame = time.perf_counter()

            boxes, kpts = det(frame)
            feats = emb(frame, boxes)
            dets = [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i])
                    for i in range(len(boxes))]
            live, _removed = tracker.update(dets, idx)

            n_det = n_pred = 0
            for t, source in live:
                seen_ids.add(t.track_id)
                n_det += source == "detected"
                n_pred += source == "predicted"
                draw_person(frame, t.track_id, t.ltrb, t.kpts, source,
                            show_skel, show_wrist)

            b = int(Config.BORDER_THRES * min(H, W))
            cv2.rectangle(frame, (b, b), (W - b, H - b), (60, 60, 200), 1)

            lat.append((time.perf_counter() - t_frame) * 1000)
            now = time.perf_counter()
            shown.append(now - t_prev)
            t_prev = now
            disp_fps = 1.0 / max(np.mean(shown), 1e-6)

            draw_hud(frame, [
                f"frame {idx}   view {disp_fps:4.1f} fps   target {TARGET_FPS:.0f}",
                f"latency  p50 {np.percentile(lat, 50):5.1f} ms   "
                f"max {max(lat):5.1f} ms",
                f"tracks   {n_det} detected + {n_pred} predicted   "
                f"ids issued {len(seen_ids)}",
                f"dropped by backpressure {stats['dropped']}   "
                f"loops {stats['loops']}",
            ], paused)

            canvas = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame

        if canvas is not None:
            cv2.imshow(WINDOW, canvas)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord(" "):
            paused = not paused
        elif key == ord("k"):
            show_skel = not show_skel
        elif key == ord("w"):
            show_wrist = not show_wrist
        elif key == ord("s") and canvas is not None:
            n_snap += 1
            path = f"snapshot_{n_snap:03d}.jpg"
            cv2.imwrite(path, canvas)
            print(f"saved {path}")
        if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            break

    stop.set()
    cv2.destroyAllWindows()
    print(f"\nids issued {len(seen_ids)}  dropped {stats['dropped']}  "
          f"loops {stats['loops']}")


if __name__ == "__main__":
    main()
