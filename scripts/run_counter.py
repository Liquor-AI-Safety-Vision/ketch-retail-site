"""Counter camera: person tracking + staff/customer roles + cash-till state.

    python3 run_counter.py --video videos/top-side-view.mp4

Two models on one frame. They share nothing except the pixels: the pose model
feeds the tracker, the segmentation model reads the till. Keeping them apart is
deliberate - the till does not move, does not need an identity, and a missed
till frame costs nothing, whereas a missed person frame breaks a track.

Offline by design: every downsampled frame is processed and none dropped, which
is what makes the output reviewable frame by frame.
"""
import argparse
import json
import os
import sys
import time
import warnings

import cv2
import numpy as np

import stream_track as S
import zones
from uae_tracker import Detection, Tracker

warnings.filterwarnings("ignore", message=".*encountered in matmul",
                        category=RuntimeWarning)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TILL_COLOR = {"Open": (60, 60, 255), "Close": (90, 200, 90)}


class Till:
    """Cash-till segmentation on raw onnxruntime, CoreML first.

    Not through ultralytics' own predict(): it builds its ONNX session with the
    CPU provider unless told otherwise, which measured 516 ms/frame here against
    72 ms for the same graph on CoreML. The decode below is the price of that
    7x - it is the standard YOLO-seg assembly, and it is checked against
    ultralytics' output rather than assumed correct.

    Output layout mirrors the pose head stream_track decodes: (1, 300, 38) is
    box(4) + conf(1) + cls(1) + 32 mask coefficients, NMS-free; the second
    output is the 32 prototypes at stride 4.
    """

    def __init__(self, path, imgsz, conf, names=("Close", "Open"), confirm=5,
                 providers=None):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.log_severity_level = 3
        if providers is None:
            providers = (
                ["CoreMLExecutionProvider", "CPUExecutionProvider"]
                if sys.platform == "darwin"
                else ["CUDAExecutionProvider", "CPUExecutionProvider"]
            )
        self.sess = ort.InferenceSession(
            path, so, providers=providers)
        inp_meta = self.sess.get_inputs()[0]
        self.inp = inp_meta.name
        self.input_dtype = np.float16 if inp_meta.type == "tensor(float16)" else np.float32
        self.imgsz, self.conf = imgsz, conf
        self.names = {i: n for i, n in enumerate(names)}
        self.provider = self.sess.get_providers()[0]
        self.reg = TillRegistry(confirm)
        self.last_profile = {}
        self._letterbox_canvas = np.empty((self.imgsz, self.imgsz, 3), dtype=np.uint8)

    def __call__(self, frame):
        t0 = time.perf_counter()
        lb, r, dx, dy = S.letterbox(frame, size=self.imgsz, canvas=self._letterbox_canvas)
        t1 = time.perf_counter()
        blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
        blob = np.ascontiguousarray(blob, self.input_dtype)[None] / self.input_dtype(255.0)
        t2 = time.perf_counter()
        det, proto = self.sess.run(None, {self.inp: blob})
        t3 = time.perf_counter()
        det, proto = det[0], proto[0]                     # (300, 38), (32, g, g)
        g = proto.shape[-1]
        stride = self.imgsz / g                           # proto grid -> letterbox px
        flat = proto.reshape(proto.shape[0], -1)

        cand = det[det[:, 4] >= self.conf]
        nms_keep = np.arange(len(cand), dtype=np.int32)
        if len(cand):
            boxes_xywh = np.stack(
                [
                    cand[:, 0] - cand[:, 2] / 2,
                    cand[:, 1] - cand[:, 3] / 2,
                    cand[:, 2],
                    cand[:, 3],
                ],
                axis=1,
            )
            classes = cand[:, 5].astype(np.int32)
            keep_idx = []
            for cls_id in np.unique(classes):
                cls_mask = classes == cls_id
                cls_boxes = boxes_xywh[cls_mask]
                cls_scores = cand[cls_mask, 4]
                idx = cv2.dnn.NMSBoxes(
                    cls_boxes.tolist(),
                    cls_scores.tolist(),
                    self.conf,
                    0.5,
                )
                if len(idx) == 0:
                    continue
                cls_pos = np.flatnonzero(cls_mask)
                keep_idx.extend(cls_pos[np.array(idx).reshape(-1)].tolist())
            if keep_idx:
                nms_keep = np.array(sorted(set(keep_idx)), dtype=np.int32)
                cand = cand[nms_keep]

        out = []
        survivors = 0
        for d in cand:
            survivors += 1
            logits = np.asarray(d[6:] @ flat, dtype=np.float32).reshape(g, g)
            m = 1.0 / (1.0 + np.exp(-logits))
            x1, y1 = max(0, int(d[0])), max(0, int(d[1]))
            x2 = min(self.imgsz, int(np.ceil(d[2])))
            y2 = min(self.imgsz, int(np.ceil(d[3])))
            if x2 <= x1 or y2 <= y1:
                continue
            # Decode only the mask ROI covered by this box instead of
            # upsampling the whole 640x640 canvas and zeroing most of it out.
            gx1 = max(0, min(g - 1, int(np.floor(x1 / stride))))
            gy1 = max(0, min(g - 1, int(np.floor(y1 / stride))))
            gx2 = max(gx1 + 1, min(g, int(np.ceil(x2 / stride))))
            gy2 = max(gy1 + 1, min(g, int(np.ceil(y2 / stride))))
            roi = m[gy1:gy2, gx1:gx2]
            if roi.size == 0:
                continue
            binm = cv2.resize(
                roi,
                (x2 - x1, y2 - y1),
                interpolation=cv2.INTER_LINEAR,
            )
            binm = (binm > 0.5).astype(np.uint8)
            cnts, _ = cv2.findContours(binm, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                continue
            poly = max(cnts, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)
            poly += np.array([x1, y1], dtype=np.float32)
            # letterboxed pixels -> original frame
            poly = (poly - [dx, dy]) / r
            out.append((self.names[int(d[5])], float(d[4]),
                        poly.astype(np.int32)))
        t4 = time.perf_counter()
        self.last_profile = {
            "provider": self.provider,
            "letterbox_s": t1 - t0,
            "preprocess_s": t2 - t1,
            "infer_s": t3 - t2,
            "decode_masks_s": t4 - t3,
            "total_s": t4 - t0,
            "num_candidates_before_nms": int(len(det[det[:, 4] >= self.conf])),
            "num_candidates_after_nms": survivors,
            "num_outputs": len(out),
        }
        return out


class TillState:
    """Confirmed open/close state, on the same principle as the tracker's N_INIT.

    A till does not open and close ten times a second, so a single frame is not
    evidence of anything - measured on cash_till_open_2, the raw per-frame label
    changed 87 times in 3.5 minutes. A new state has to hold for CONFIRM
    consecutive frames before it is believed.

    Starts Closed by assumption, not by observation: a drawer at rest is closed,
    and starting from 'unknown' would emit a state change on the first frame of
    every clip that says nothing about the till.

    A frame with no detection never changes the state - it is a missing
    measurement, not evidence of the opposite. It does reset a pending
    candidate, because "5 consecutive frames" is meant literally: a run
    interrupted by a blind frame is not a run. The till is detected in ~100% of
    frames on this camera, so this rarely fires; if that ever drops, this is the
    line to revisit, and a gap-tolerant version would be the fix.
    """

    def __init__(self, confirm=5, start="Close", centre=None):
        self.confirm = confirm
        self.state = start
        self.centre = centre        # tills do not move; this is their identity
        self.candidate, self.streak = None, 0
        self.raw_changes = 0
        self._last_raw = start

    def update(self, observed):
        """observed: winning class this frame, or None. Returns the confirmed
        state and, when it just changed, the streak that carried it."""
        if observed is not None and observed != self._last_raw:
            self.raw_changes += 1
        if observed is not None:
            self._last_raw = observed

        if observed is None or observed == self.state:
            self.candidate, self.streak = None, 0
            return self.state, None

        if observed == self.candidate:
            self.streak += 1
        else:
            self.candidate, self.streak = observed, 1

        if self.streak >= self.confirm:
            self.state = self.candidate
            held = self.streak
            self.candidate, self.streak = None, 0
            return self.state, held
        return self.state, None


class TillRegistry:
    """One confirmed state per till, keyed by where the till sits.

    A till is furniture: it does not move, so its identity is its position and
    no appearance matching is needed. Anything within MATCH_PX of a known till
    is that till; anything else is a new one.

    This replaces a single frame-wide state taken from the highest-scoring
    detection. That was wrong once the model started seeing both tills at once:
    the idle left till scored 0.96 while the right one was being used, so the
    frame-wide reading described whichever till happened to score higher, not
    the one that changed.
    """

    MATCH_PX = 200

    def __init__(self, confirm=5):
        self.confirm = confirm
        self.tills = []

    def _match(self, centre):
        for t in self.tills:
            if np.hypot(*(np.asarray(centre) - t.centre)) < self.MATCH_PX:
                return t
        t = TillState(self.confirm, centre=np.asarray(centre, np.float32))
        self.tills.append(t)
        return t

    def update(self, dets):
        """dets: [(name, score, poly)]. Returns [(till, name, score, poly)] with
        each detection carrying the confirmed state of the till it belongs to."""
        best = {}
        for name, score, poly in dets:
            t = self._match((poly[:, 0].mean(), poly[:, 1].mean()))
            # one reading per till per frame: the model emits both hypotheses
            # for the same object, so the higher score is this till's reading
            if id(t) not in best or score > best[id(t)][2]:
                best[id(t)] = (t, name, score, poly)
        changes = []
        for t, name, score, poly in best.values():
            state, confirmed = t.update(name)
            if confirmed:
                changes.append((t, state, score, confirmed))
        seen = {id(t) for t, *_ in best.values()}
        for t in self.tills:                    # a till nobody saw holds its state
            if id(t) not in seen:
                t.update(None)
        return list(best.values()), changes

    def index(self, till):
        return self.tills.index(till)


def draw_till(img, items, reg, th):
    """items: [(till, raw_name, score, poly)] - drawn by CONFIRMED state.

    The mask carries the state the system is actually reporting, not this
    frame's raw guess: a mask that flickers while the reported state holds
    steady is a picture of two different things at once.
    """
    for till, raw, score, poly in items:
        state = till.state
        c = TILL_COLOR.get(state, (200, 200, 200))
        ov = img.copy()
        cv2.fillPoly(ov, [poly], c)
        cv2.addWeighted(ov, 0.35, img, 0.65, 0, img)
        cv2.polylines(img, [poly], True, c, th)
        x, y = poly[:, 0].min(), poly[:, 1].min()
        tag = f"TILL{reg.index(till)} {state}"
        if till.candidate:                       # a change being counted out
            tag += f"  ->{till.candidate} {till.streak}/{till.confirm}"
        (tw, tht), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.6, th)
        cv2.rectangle(img, (x, y - tht - 8), (x + tw + 8, y), c, -1)
        cv2.putText(img, tag, (x + 4, y - 5), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 0, 0), th)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--video", default=f"{HERE}/videos/top-side-view.mp4")
    p.add_argument("--pose", default=f"{HERE}/models/yolo26m-pose.onnx")
    p.add_argument("--reid", default=f"{HERE}/models/osnet_x0_25_msmt17.onnx")
    # v4, from till_seg_m640_last_v4.pt. Measured on the left till over the
    # first 25 s of cash_till_open_1, where the drawer is closed throughout:
    # v2 called it Open in 84% of frames and 89-92% of the frames where a person
    # overlapped it; v4 calls it Open in 0 of 249, holding Close at ~0.92
    # whether the till is occluded or not - while still reading a genuinely open
    # drawer at 0.85-0.91. Older exports (till_seg_480, till_seg_640,
    # till_seg_v2_640) are kept only for comparison.
    p.add_argument("--till", default=f"{HERE}/models/till_seg_v4_640.onnx")
    p.add_argument("--till-imgsz", type=int, default=640)
    p.add_argument("--till-conf", type=float, default=0.70)
    p.add_argument("--till-confirm", type=int, default=5,
                   help="consecutive frames a new till state must hold")
    p.add_argument("--conf", type=float, default=0.35, help="person confidence")
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--zones", default=f"{HERE}/outputs/top-side-view/top-side-view_zones.json")
    p.add_argument("--flip-side", action="store_true")
    p.add_argument("--out")
    p.add_argument("--events")
    p.add_argument("--limit", type=int, help="stop after N frames (wiring check)")
    a = p.parse_args()

    S.MODEL, S.REID_MODEL, S.CONF = a.pose, a.reid, a.conf
    stem = os.path.splitext(a.video)[0]
    out_video = a.out or f"{stem}_counter.mp4"
    room = os.path.basename(stem)
    out_events = a.events or f"{HERE}/outputs/{room}/{room}_counter_events.jsonl"
    # only the default path owns a per-room directory; making it regardless left
    # an empty outputs/<clip>/ behind for every run that passed --events
    os.makedirs(os.path.dirname(out_events), exist_ok=True)

    cap = cv2.VideoCapture(a.video)
    W, H = int(cap.get(3)), int(cap.get(4))
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    det, emb = S.Detector(), S.Embedder()
    till = Till(a.till, a.till_imgsz, a.till_conf, confirm=a.till_confirm)
    tracker = Tracker(img_h=H, img_w=W, fps=a.fps)

    div = voter = None
    if a.zones and os.path.exists(a.zones):
        div, zname = zones.load_divider(a.zones, flip=a.flip_side, size=(W, H))
        voter = zones.RoleVoter(div)

    print(f"{os.path.basename(a.video)}: {W}x{H} @ {src_fps:.2f} -> {a.fps} fps, "
          f"{total} frames")
    print(f"person: {os.path.basename(a.pose)} @{S.IMGSZ} conf={a.conf}")
    print(f"till  : {os.path.basename(a.till)} @{a.till_imgsz} "
          f"conf={a.till_conf}  classes={list(till.names.values())}")
    print(f"        one state per till, each starts 'Close'; a change needs "
          f"{a.till_confirm} consecutive frames "
          f"({a.till_confirm / a.fps:.1f}s at {a.fps:g} fps)")
    print(f"roles : {'divider ' + repr(zname) if div else 'disabled'}")

    writer = cv2.VideoWriter(out_video, cv2.VideoWriter_fourcc(*"mp4v"),
                             a.fps, (W, H))

    step, acc, idx = a.fps / src_fps, 0.0, 0
    events, seen, roles = [], set(), {}
    till_since = {}
    t_pose = t_till = t_rest = 0.0
    n_person, n_till = [], 0
    t0 = time.perf_counter()

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

        ti = time.perf_counter()
        boxes, kpts = det(frame)
        t_pose += time.perf_counter() - ti

        ti = time.perf_counter()
        tills = till(frame)
        t_till += time.perf_counter() - ti

        ti = time.perf_counter()
        feats = emb(frame, boxes)
        live, removed = tracker.update(
            [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i])
             for i in range(len(boxes))], idx)

        for tid in removed:
            if tid in seen:
                events.append({"frame": idx, "t": round(idx / a.fps, 2),
                               "event": "TRACK_REMOVED", "id": tid,
                               "role": roles.get(tid, "?")})
            if voter:
                voter.drop(tid)

        items = []
        for t, source in live:
            if t.track_id not in seen:
                seen.add(t.track_id)
                events.append({"frame": idx, "t": round(idx / a.fps, 2),
                               "event": "TRACK_NEW", "id": t.track_id})
            role, foot = None, None
            if voter:
                role, foot = voter.update(t.track_id, t.ltrb, t.kpts)
                roles[t.track_id] = role
            items.append((t.track_id, t.ltrb, t.kpts, source, role, foot))
        t_rest += time.perf_counter() - ti

        # Each till keeps its own state; a reading is not a state.
        items_till, changes = till.reg.update(tills)
        n_till += len(items_till) > 0
        for t, state, score, over in changes:
            i = till.reg.index(t)
            events.append({"frame": idx, "t": round(idx / a.fps, 2),
                           "event": "TILL_STATE", "till": i, "state": state,
                           "conf": round(score, 3), "confirmed_over": over,
                           "held_frames": idx - till_since.get(i, 0)})
            till_since[i] = idx

        n_person.append(len(items))
        hud = (f"f{idx}  t={idx/a.fps:6.1f}s  persons={len(items)}  "
               + "  ".join(f"till{till.reg.index(t)}={t.state}"
                           for t in till.reg.tills))
        s = max(1.0, H / 540.0)
        draw_till(frame, items_till, till.reg, int(round(2 * s)))
        S.draw(frame, items, hud, div)
        writer.write(frame)
        idx += 1

    cap.release()
    writer.release()
    from run_warehouse import to_h264
    to_h264(out_video)
    with open(out_events, "w") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")

    wall = time.perf_counter() - t0
    print(f"\nprocessed {idx} frames in {wall:.0f}s ({idx/wall:.1f} fps)")
    print(f"  pose {t_pose/idx*1000:5.0f} ms/f   till {t_till/idx*1000:5.0f} ms/f"
          f"   reid+track+roles {t_rest/idx*1000:5.0f} ms/f")
    print(f"persons/frame: mean={np.mean(n_person):.2f} max={max(n_person)}  "
          f"ids seen={len(seen)}")
    print(f"till detected in {n_till}/{idx} frames ({n_till/idx*100:.0f}%)")
    for i, t in enumerate(till.reg.tills):
        n_ev = sum(1 for e in events
                   if e["event"] == "TILL_STATE" and e["till"] == i)
        print(f"  till{i} at ({t.centre[0]:.0f},{t.centre[1]:.0f}): "
              f"raw label changes {t.raw_changes} -> confirmed {n_ev}, "
              f"ends {t.state}")
    kinds = {}
    for e in events:
        kinds[e["event"]] = kinds.get(e["event"], 0) + 1
    print("events: " + "  ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    for e in events:
        if e["event"] == "TILL_STATE":
            print(f"  t={e['t']:7.1f}s  till -> {e['state']:<5} conf={e['conf']}")
    if voter:
        final = [roles.get(i, "?") for i in seen]
        print(f"roles: staff={final.count('staff')} "
              f"customer={final.count('customer')} unresolved={final.count('?')}")
    print(f"saved {out_video}\n      {out_events}")


if __name__ == "__main__":
    main()
