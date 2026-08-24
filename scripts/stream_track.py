"""Streaming person tracking: YOLO26n-pose (ONNX/CoreML) -> uae_tracker DeepSORT.

Simulates an RTSP-style camera: a producer thread downsamples the source to
TARGET_FPS and pushes frames into a depth-1 queue, dropping the frame if the
consumer is still busy - what a real stream does under backpressure.

All identity logic lives in uae_tracker.py. This file only detects, embeds,
paces the stream, and draws. There is no re-identification gallery: a track that
stays unmatched past its max age is REMOVED and its id is gone. See uae_tracker's
module docstring for why.

Keypoints ride into the tracker on the Detection and come back out on the Track,
so a wrist belongs to the id the matcher gave it. Tracks are reported with the
provenance of their box - "detected" or "predicted" - because the behaviour layer
must be able to keep an event alive across a missed frame without mistaking a
Kalman guess for evidence.

Models
------
detector  YOLO26n-pose, ONNX, onnxruntime CoreML EP
embedder  OSNet-x0.25 trained on MSMT17, ONNX, onnxruntime CoreML EP.
          907 KB, 512-d, ~3.8 ms per batch of 16. A purpose-trained person
          re-id model; see the Embedder docstring for the measured gain over
          the ImageNet backbone it replaced.
"""
import argparse
import json
import os
import queue
import sys
import threading
import time
import warnings

import cv2
import numpy as np
import onnxruntime as ort

import zones
from uae_tracker import Config, Detection, Tracker

# NumPy 2.0 on macOS/Accelerate raises spurious divide-by-zero / overflow /
# invalid warnings from BLAS gemv on perfectly finite input (the flags come from
# SIMD padding lanes). Verified: results match a manual dot product to 3e-17.
warnings.filterwarnings("ignore", message=".*encountered in matmul",
                        category=RuntimeWarning)

MODEL = "models/yolo26n-pose.onnx"
REID_MODEL = "models/osnet_x0_25_msmt17.onnx"
VIDEO = "videos/cctv_test.mp4"
OUT_VIDEO = "videos/cctv_tracked.mp4"
OUT_EVENTS = "track_events.jsonl"

IMGSZ = 640
CONF = 0.35
TARGET_FPS = 10.0
REALTIME = True   # False: process every downsampled frame, never drop

ZONES = None      # path to a zone_editor JSON; None disables role labelling
STAFF_SIDE = -1   # which side of the divider is staff; flip to +1 if inverted

ROLE_COLOR = {"staff": (60, 230, 60), "customer": (60, 160, 255), "?": (170, 170, 170)}

REID_BATCH = 16   # the exported OSNet graph has a fixed batch dimension
ORT_PROVIDERS = None
IMNET_MEAN = np.array([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
IMNET_STD = np.array([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)

SKELETON = [(15, 13), (13, 11), (16, 14), (14, 12), (11, 12), (5, 11), (6, 12),
            (5, 6), (5, 7), (6, 8), (7, 9), (8, 10), (1, 2), (0, 1), (0, 2),
            (1, 3), (2, 4), (3, 5), (4, 6)]


# ---------------------------------------------------------------- detection
def letterbox(im, size=IMGSZ, canvas=None):
    h, w = im.shape[:2]
    r = min(size / h, size / w)
    nh, nw = round(h * r), round(w * r)
    pad_t, pad_l = (size - nh) // 2, (size - nw) // 2
    if canvas is None or canvas.shape != (size, size, 3) or canvas.dtype != np.uint8:
        out = np.full((size, size, 3), 114, np.uint8)
    else:
        out = canvas
        out.fill(114)
    roi = out[pad_t:pad_t + nh, pad_l:pad_l + nw]
    cv2.resize(im, (nw, nh), dst=roi)
    return out, r, pad_l, pad_t


def preferred_providers():
    if ORT_PROVIDERS:
        return list(ORT_PROVIDERS)
    avail = ort.get_available_providers()
    out = []
    if sys.platform == "darwin" and "CoreMLExecutionProvider" in avail:
        out.append("CoreMLExecutionProvider")
    if sys.platform != "darwin" and "CUDAExecutionProvider" in avail:
        out.append("CUDAExecutionProvider")
    if "CPUExecutionProvider" in avail:
        out.append("CPUExecutionProvider")
    return out or ["CPUExecutionProvider"]


class Detector:
    def __init__(self):
        self.sess = ort.InferenceSession(
            MODEL, providers=preferred_providers())
        inp_meta = self.sess.get_inputs()[0]
        self.inp = inp_meta.name
        self.input_dtype = np.float16 if inp_meta.type == "tensor(float16)" else np.float32
        self.provider = self.sess.get_providers()[0]
        self.last_profile = {}
        self._letterbox_canvas = np.empty((IMGSZ, IMGSZ, 3), dtype=np.uint8)

    def __call__(self, frame):
        t0 = time.perf_counter()
        lb, r, dx, dy = letterbox(frame, canvas=self._letterbox_canvas)
        t1 = time.perf_counter()
        blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
        blob = np.ascontiguousarray(blob, self.input_dtype)[None] / self.input_dtype(255.0)
        t2 = time.perf_counter()
        out = self.sess.run(None, {self.inp: blob})[0][0]      # (300, 57) NMS-free
        t3 = time.perf_counter()
        h, w = frame.shape[:2]
        selected = out[out[:, 4] >= CONF]
        if len(selected):
            boxes = selected[:, :4].astype(np.float32, copy=True)
            boxes[:, [0, 2]] = (boxes[:, [0, 2]] - dx) / r
            boxes[:, [1, 3]] = (boxes[:, [1, 3]] - dy) / r
            boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0.0, w - 1.0)
            boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0.0, h - 1.0)
            keep = ((boxes[:, 2] - boxes[:, 0]) >= 8.0) & ((boxes[:, 3] - boxes[:, 1]) >= 16.0)
            boxes = boxes[keep]
            scores = selected[keep, 4:5].astype(np.float32, copy=False)
            k_arr = selected[keep, 6:].reshape(-1, 17, 3).astype(np.float32, copy=True)
            k_arr[:, :, 0] = (k_arr[:, :, 0] - dx) / r
            k_arr[:, :, 1] = (k_arr[:, :, 1] - dy) / r
            kpts = [k for k in k_arr]
            boxes = np.concatenate([boxes, scores], axis=1)
        else:
            boxes = np.empty((0, 5), np.float32)
            kpts = []
        t4 = time.perf_counter()
        self.last_profile = {
            "provider": self.provider,
            "letterbox_s": t1 - t0,
            "preprocess_s": t2 - t1,
            "infer_s": t3 - t2,
            "decode_s": t4 - t3,
            "total_s": t4 - t0,
            "detections": len(boxes),
        }
        return boxes, kpts


# ---------------------------------------------------------------- appearance
class Embedder:
    """OSNet-x0.25 trained on MSMT17, ONNX on the CoreML EP. 512-d, L2-normed.

    Measured against the previous ImageNet MobileNetV3 backbone on this footage,
    scoring same-track pairs (>=1 s apart) against pairs of tracks alive in the
    same frame - which are certainly different people:

                    AUC     d'     TPR @ 1% false-match rate
        mobilenet   0.785   1.09   35.6 %
        osnet       0.879   1.92   68.2 %

    Blending in a torso colour histogram raised AUC to 0.893 but *lowered* TPR at
    1% FAR to 66%, so it was dropped: it helps the average case and hurts the
    tail, and the tail is where match decisions are made.
    """

    def __init__(self):
        self.sess = ort.InferenceSession(
            REID_MODEL,
            providers=preferred_providers())
        inp_meta = self.sess.get_inputs()[0]
        self.inp = inp_meta.name
        self.input_dtype = np.float16 if inp_meta.type == "tensor(float16)" else np.float32
        self.dim = self.sess.get_outputs()[0].shape[1]
        self.provider = self.sess.get_providers()[0]
        self.last_profile = {}
        self._pad_batch = np.zeros((REID_BATCH, 3, 256, 128), dtype=self.input_dtype)

    def __call__(self, frame, boxes):
        if not len(boxes):
            self.last_profile = {
                "provider": self.provider,
                "crop_resize_s": 0.0,
                "pack_norm_s": 0.0,
                "infer_s": 0.0,
                "l2norm_s": 0.0,
                "total_s": 0.0,
                "num_boxes": 0,
                "num_chunks": 0,
            }
            return np.zeros((0, self.dim), np.float32)
        t0 = time.perf_counter()
        crops = []
        for x1, y1, x2, y2 in boxes[:, :4].astype(int):
            c = frame[max(0, y1):y2, max(0, x1):x2]
            if c.size == 0:
                c = np.zeros((16, 8, 3), np.uint8)
            crops.append(cv2.cvtColor(cv2.resize(c, (128, 256)), cv2.COLOR_BGR2RGB))
        t1 = time.perf_counter()
        nchw = np.stack(crops).transpose(0, 3, 1, 2).astype(np.float32) / 255.0
        nchw = (nchw - IMNET_MEAN) / IMNET_STD
        nchw = nchw.astype(self.input_dtype, copy=False)
        t2 = time.perf_counter()

        out = []
        infer_s = 0.0
        num_chunks = 0
        for i in range(0, len(nchw), REID_BATCH):
            chunk = nchw[i:i + REID_BATCH]
            pad = REID_BATCH - len(chunk)
            if pad:                       # the graph's batch dimension is fixed
                self._pad_batch.fill(0)
                self._pad_batch[:len(chunk)] = chunk
                chunk = self._pad_batch
            ti0 = time.perf_counter()
            f = self.sess.run(None, {self.inp: np.ascontiguousarray(chunk)})[0]
            infer_s += time.perf_counter() - ti0
            num_chunks += 1
            out.append(f[:REID_BATCH - pad] if pad else f)
        t3 = time.perf_counter()
        f = np.concatenate(out)
        out_norm = (f / (np.linalg.norm(f, axis=1, keepdims=True) + 1e-9)).astype(np.float32)
        t4 = time.perf_counter()
        self.last_profile = {
            "provider": self.provider,
            "crop_resize_s": t1 - t0,
            "pack_norm_s": t2 - t1,
            "infer_s": infer_s,
            "post_concat_s": t3 - t2 - infer_s,
            "l2norm_s": t4 - t3,
            "total_s": t4 - t0,
            "num_boxes": len(boxes),
            "num_chunks": num_chunks,
        }
        return out_norm


# ---------------------------------------------------------------- stream sim
def producer(q, stop, stats):
    """Emit frames at TARGET_FPS; drop when the consumer is behind, like RTSP."""
    cap = cv2.VideoCapture(VIDEO)
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    step, acc, idx = TARGET_FPS / src_fps, 0.0, 0
    t0 = time.perf_counter()
    while not stop.is_set():
        # grab() demuxes without the colour conversion and buffer copy read()
        # performs, so frames the downsample discards cost far less CPU
        if not cap.grab():
            break
        acc += step
        if acc < 1.0:
            continue
        acc -= 1.0
        ok, frame = cap.retrieve()
        if not ok:
            break
        if REALTIME:
            due = t0 + idx / TARGET_FPS    # pace to wall clock, like a live camera
            slack = due - time.perf_counter()
            if slack > 0:
                time.sleep(slack)
            try:
                q.put_nowait((idx, frame))
            except queue.Full:
                stats["dropped"] += 1
        else:
            # offline: block instead of dropping, so the output holds every frame
            q.put((idx, frame))
        idx += 1
    cap.release()
    q.put((None, None))


# ---------------------------------------------------------------- draw
def color(tid):
    rng = np.random.RandomState(tid * 7919)
    return tuple(int(c) for c in rng.randint(60, 255, 3))


def draw(img, items, hud, div=None):
    h, w = img.shape[:2]
    # overlay scaled to the frame: a 2px box and 0.45 text vanish on 1080p
    s = max(1.0, h / 540.0)
    th, fs = int(round(2 * s)), 0.45 * s
    b = int(Config.BORDER_THRES * min(h, w))
    cv2.rectangle(img, (b, b), (w - b, h - b), (60, 60, 200), 1)
    if div is not None:
        # extend the drawn polyline past both borders, which is how it is used
        p = div.p.copy()
        for i, j in ((0, 1), (-1, -2)):
            d = p[i] - p[j]
            p[i] = p[i] + d / (np.linalg.norm(d) + 1e-6) * (w + h)
        cv2.polylines(img, [p.astype(np.int32)], False, (255, 255, 255), th)
    for tid, ltrb, kpts, source, role, foot in items:
        c = color(tid)
        p1 = (int(ltrb[0]), int(ltrb[1]))
        # a coasting track is drawn thin and marked, so a Kalman guess is never
        # mistaken for an observation when reviewing the footage
        predicted = source == "predicted"
        cv2.rectangle(img, p1, (int(ltrb[2]), int(ltrb[3])), c,
                      max(1, th // 2) if predicted else th)
        tag = f"ID{tid}?" if predicted else f"ID{tid}"
        if role is not None:
            tag = f"{tag} {role}"
        # the box keeps its per-id colour so identity stays followable; the tag
        # carries the role colour, so the two are readable independently
        (tw, tht), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
        cv2.rectangle(img, (p1[0], p1[1] - tht - 6), (p1[0] + tw + 6, p1[1]),
                      ROLE_COLOR.get(role, c), -1)
        cv2.putText(img, tag, (p1[0] + 3, p1[1] - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), th)
        if foot is not None:
            # the point the role was decided from - so a wrong label is
            # traceable to a bad anchor rather than to the zone
            cv2.circle(img, (int(foot[0]), int(foot[1])), max(3, th + 2), c, -1)
            cv2.circle(img, (int(foot[0]), int(foot[1])), max(3, th + 2),
                       (255, 255, 255), 1)
        if kpts is not None:
            for a, b_ in SKELETON:
                if kpts[a, 2] > 0.5 and kpts[b_, 2] > 0.5:
                    cv2.line(img, tuple(kpts[a, :2].astype(int)),
                             tuple(kpts[b_, :2].astype(int)), c, max(1, th // 2))
            for x, y, v in kpts:
                if v > 0.5:
                    cv2.circle(img, (int(x), int(y)), max(2, th), c, -1)
    cv2.putText(img, hud, (8, int(24 * s)), cv2.FONT_HERSHEY_SIMPLEX,
                0.5 * s, (255, 255, 255), th)


def consumer(out_q, writer, div=None):
    """Overlay and encode off the critical path; the tracker must not wait on I/O."""
    while True:
        job = out_q.get()
        if job is None:
            break
        frame, items, hud = job
        draw(frame, items, hud, div)
        writer.write(frame)


# ---------------------------------------------------------------- main
def main():
    cap = cv2.VideoCapture(VIDEO)
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()

    det = Detector()
    emb = Embedder()
    tracker = Tracker(img_h=H, img_w=W, fps=TARGET_FPS)
    F = tracker.frames
    print(f"stream: {W}x{H} @ {src_fps:.3f} -> {TARGET_FPS} fps")
    print(f"embedder: {REID_MODEL} -> {emb.dim}-d, CoreML EP")
    print(f"tracker: max_age={Config.MAX_AGE_S}s/{F['max_age']}f  "
          f"near_border={Config.NEAR_BORDER_MAX_AGE_S}s/{F['near_border_max_age']}f  "
          f"n_init={Config.N_INIT}  maha={Config.MAHA_METHOD}  no re-id gallery")

    div, voter = None, None
    if ZONES:
        div, zname = zones.load_divider(ZONES, flip=STAFF_SIDE > 0)
        voter = zones.RoleVoter(div)
        print(f"zones: {ZONES} divider={zname!r}  staff side = the one containing "
              f"{tuple(int(v) for v in div.staff_ref)}  "
              f"dead_band=max({zones.DEAD_BAND_MIN_PX}px, "
              f"{zones.DEAD_BAND_FRAC:.0%} of box height)  "
              f"vote_min={zones.VOTE_MIN}")

    writer = cv2.VideoWriter(OUT_VIDEO, cv2.VideoWriter_fourcc(*"mp4v"),
                             TARGET_FPS, (W, H))
    q, stop, stats = queue.Queue(maxsize=1), threading.Event(), {"dropped": 0}
    threading.Thread(target=producer, args=(q, stop, stats), daemon=True).start()
    out_q = queue.Queue(maxsize=8)
    writer_th = threading.Thread(target=consumer, args=(out_q, writer, div))
    writer_th.start()

    events, seen_ids, roles = [], set(), {}
    n, t_det, t_emb, t_trk, t_io, n_pred = 0, 0.0, 0.0, 0.0, 0.0, 0
    lat, n_dets = [], []
    t0 = time.perf_counter()
    while True:
        idx, frame = q.get()
        if idx is None:
            break
        t_frame = time.perf_counter()

        ti = time.perf_counter()
        boxes, kpts = det(frame)
        t_det += time.perf_counter() - ti

        ti = time.perf_counter()
        feats = emb(frame, boxes)
        dets = [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i])
                for i in range(len(boxes))]
        t_emb += time.perf_counter() - ti

        ti = time.perf_counter()
        live, removed = tracker.update(dets, idx)
        t_trk += time.perf_counter() - ti

        for tid in removed:
            # tentative tracks are removed on their first miss and were never
            # announced, so reporting them here would log deaths without births
            if tid in seen_ids:
                events.append({"frame": idx, "t": round(idx / TARGET_FPS, 2),
                               "event": "TRACK_REMOVED", "id": tid,
                               "role": roles.get(tid, "?")})
            if voter:
                voter.drop(tid)
        items = []
        for t, source in live:
            if t.track_id not in seen_ids:
                seen_ids.add(t.track_id)
                events.append({"frame": idx, "t": round(idx / TARGET_FPS, 2),
                               "event": "TRACK_CONFIRMED", "id": t.track_id})
            role = foot = None
            if voter:
                role, foot = voter.update(t.track_id, t.ltrb, t.kpts)
                if role != roles.get(t.track_id, "?"):
                    roles[t.track_id] = role
                    events.append({"frame": idx, "t": round(idx / TARGET_FPS, 2),
                                   "event": "ROLE_SET", "id": t.track_id,
                                   "role": role})
            # the pose came in on the detection this track was matched to, so it
            # is this track's by construction; a coasting track shows its last one
            items.append((t.track_id, t.ltrb, t.kpts, source, role, foot))
        n_pred += sum(1 for _, s in live if s == "predicted")

        hud = (f"live={len(live)} lost={len(tracker.lost_tracks)} "
               f"ids={len(seen_ids)}")
        if voter:
            hud += (f"  staff={sum(1 for _, _, _, _, r, _ in items if r == 'staff')}"
                    f" cust={sum(1 for _, _, _, _, r, _ in items if r == 'customer')}")
        ti = time.perf_counter()
        out_q.put((frame, items, hud))
        t_io += time.perf_counter() - ti

        lat.append((time.perf_counter() - t_frame) * 1000)
        n_dets.append(len(boxes))
        n += 1
        if n % 200 == 0:
            print(f"  frame {idx}  live={len(live)} ids={len(seen_ids)} "
                  f"dropped={stats['dropped']}")

    stop.set()
    out_q.put(None)
    writer_th.join()
    writer.release()
    with open(OUT_EVENTS, "w") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")

    wall = time.perf_counter() - t0
    lat, nd = np.array(lat), np.array(n_dets)
    budget = 1000.0 / TARGET_FPS
    cap_fps = n / (t_det + t_emb + t_trk + t_io)
    print(f"\nprocessed {n} frames, {stats['dropped']} dropped by backpressure "
          f"-> {n / wall:.2f} fps effective")
    print(f"detect {t_det / n * 1000:.1f} | embed {t_emb / n * 1000:.1f} | "
          f"track {t_trk / n * 1000:.1f} | handoff {t_io / n * 1000:.1f} ms  ->  "
          f"{cap_fps:.1f} fps capacity ({cap_fps / TARGET_FPS:.1f}x target)")
    print(f"per-frame latency ms: p50={np.percentile(lat, 50):.1f} "
          f"p90={np.percentile(lat, 90):.1f} p99={np.percentile(lat, 99):.1f} "
          f"max={lat.max():.1f}  | over {budget:.0f}ms budget: "
          f"{(lat > budget).mean() * 100:.1f}% of frames")
    print(f"detections/frame: p50={np.percentile(nd, 50):.0f} max={nd.max()}")
    print(f"wall {wall:.1f}s  |  total ids issued: {len(seen_ids)}, "
          f"removed: {sum(1 for e in events if e['event'] == 'TRACK_REMOVED')}")
    print(f"track-frames reported on a predicted box: {n_pred} "
          f"({n_pred / max(n, 1):.2f} per frame)")
    if voter:
        final = [roles.get(i, "?") for i in seen_ids]
        print(f"roles: staff={final.count('staff')} "
              f"customer={final.count('customer')} unresolved={final.count('?')}")
        tot = sum(voter.anchor_src.values()) or 1
        print("anchor source: " + "  ".join(
            f"{k}={v / tot * 100:.0f}%" for k, v in voter.anchor_src.items()))
    print(f"saved {OUT_VIDEO}, {OUT_EVENTS}")


def cli():
    global MODEL, REID_MODEL, VIDEO, OUT_VIDEO, OUT_EVENTS, CONF, TARGET_FPS
    global REALTIME, ZONES, STAFF_SIDE
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--video", default=VIDEO)
    p.add_argument("--model", default=MODEL)
    p.add_argument("--reid", default=REID_MODEL)
    p.add_argument("--out", help="default: <video stem>_tracked.mp4 beside the input")
    p.add_argument("--events", help="default: <video stem>_events.jsonl")
    p.add_argument("--conf", type=float, default=CONF)
    p.add_argument("--fps", type=float, default=TARGET_FPS)
    p.add_argument("--offline", action="store_true",
                   help="process every frame instead of dropping under backpressure")
    p.add_argument("--zones", help="zone_editor JSON; enables staff/customer roles")
    p.add_argument("--flip-side", action="store_true",
                   help="staff is on the other side of the divider")
    a = p.parse_args()

    VIDEO, MODEL, REID_MODEL = a.video, a.model, a.reid
    CONF, TARGET_FPS, REALTIME = a.conf, a.fps, not a.offline
    ZONES, STAFF_SIDE = a.zones, (+1 if a.flip_side else -1)
    stem = os.path.splitext(VIDEO)[0]
    OUT_VIDEO = a.out or f"{stem}_tracked.mp4"
    OUT_EVENTS = a.events or f"{os.path.basename(stem)}_events.jsonl"
    main()


if __name__ == "__main__":
    cli()
