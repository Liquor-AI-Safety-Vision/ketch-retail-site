"""Run the take-from-customer FSM over a clip and report what it decided.

    python ai/tools/run_customer_cash.py --clip data/videos/cctv_counter_v2/cvlp_h3_zone_composite_00018_.mp4

Perception here is the same stack the pipeline uses -- pose, re-id, tracker,
till -- so what this prints is what the pipeline would decide, not an
approximation of it. The point is to be able to point at a clip and see the
state trace, before any of this is wired into alerting.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from ai.src.perception import stream_track as S
from ai.src.perception import zones
from ai.src.perception.customer_cash_fsm import (
    CONCEALED, RESOLVED, TERMINAL,
    CustomerCashEvent, HandHistory, Observation, Sample, Tuning, step,
)
from ai.src.perception.cash_concealment_fsm import (
    WRI_L, WRI_R, load_checkout_zones, torso_len,
)
from ai.src.perception.till_state import Till
from ai.src.perception.person_tracker import Detection, Tracker


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--clip", required=True)
    p.add_argument("--zones",
                   default=str(ROOT / "ai/config/zones/top_side_view_zones.json"))
    p.add_argument("--pose-model", default=str(ROOT / "ai/models/yolo26m-pose.onnx"))
    p.add_argument("--till-model", default=str(ROOT / "ai/models/till_seg_v4_640.onnx"))
    p.add_argument("--target-fps", type=float, default=10.0,
                   help="decimate to this rate, as the pipeline does")
    p.add_argument("--till-every", type=int, default=2,
                   help="run the till model every Nth processed frame")
    p.add_argument("--decision-window-s", type=float, default=12.0)
    p.add_argument("--json", help="write the state trace here")
    a = p.parse_args()

    cap = cv2.VideoCapture(a.clip)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    stride = max(1, int(round(src_fps / a.target_fps)))
    eff_fps = src_fps / stride

    S.MODEL = a.pose_model
    S.CONF = 0.35
    S.ORT_PROVIDERS = ["CPUExecutionProvider"]
    det = S.Detector()
    emb = S.Embedder()
    till = Till(a.till_model, 640, 0.70, confirm=3,
                providers=["CPUExecutionProvider"])

    tun = Tuning.from_seconds(
        eff_fps,
        exchange_confirm_s=0.3, withdraw_confirm_s=0.3,
        decision_window_s=a.decision_window_s, dwell_confirm_s=0.5,
        pose_grace_s=0.5, state_timeout_s=15.0,
        traj_window_s=2.5, traj_lookback_s=0.5,
        customer_recent_s=4.0)

    ok, first = cap.read()
    if not ok:
        raise SystemExit(f"cannot read {a.clip}")
    h, w = first.shape[:2]
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    divider, _ = zones.load_divider(a.zones, size=(w, h))
    voter = zones.RoleVoter(divider)
    checkout = load_checkout_zones(a.zones, (w, h))
    tracker = Tracker(img_h=h, img_w=w, fps=eff_fps)

    events: dict[int, CustomerCashEvent] = {}
    hands_by_track: dict[int, HandHistory] = {}
    last_till_box, last_till_open = None, False
    customer_last_seen: dict[str, int] = {}
    trace, alerts = [], []

    raw = idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if raw % stride:
            raw += 1
            continue
        raw += 1

        boxes, kpts = det(frame)
        feats = emb(frame, boxes)
        dets = [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i])
                for i in range(len(boxes))]
        live, removed = tracker.update(dets, idx)
        for tid in removed:
            voter.drop(tid)

        if idx % a.till_every == 0:
            items = till(frame)
            open_boxes = []
            for name, _score, poly in items:
                if name == "Open":
                    open_boxes.append([float(poly[:, 0].min()), float(poly[:, 1].min()),
                                       float(poly[:, 0].max()), float(poly[:, 1].max())])
            last_till_open = bool(open_boxes)
            if open_boxes:
                last_till_box = open_boxes[0]

        # roles first: the customer set is an input to every employee's FSM
        live_items = []
        for track, source in live:
            role, _anchor = voter.update(track.track_id, track.ltrb, track.kpts)
            live_items.append((track, role, source))

        # A trailing record, not a per-frame set: the customer is merged
        # with the employee during the hand-off itself, so the evidence that
        # a transaction happened arrives just after the reach.
        for name, poly in checkout.items():
            for track, role, _src in live_items:
                if role != zones.CUSTOMER:
                    continue
                pt, _ = zones.anchor(
                    track.ltrb,
                    np.array(track.kpts) if track.kpts is not None else None)
                if cv2.pointPolygonTest(poly, (float(pt[0]), float(pt[1])), False) >= 0:
                    customer_last_seen[name] = idx
                    break

        obs = Observation(checkout_zones=checkout,
                          customer_last_seen=dict(customer_last_seen),
                          till_open=last_till_open, till_box=last_till_box)

        for track, role, source in live_items:
            if role != zones.STAFF or track.kpts is None:
                continue
            # docs 4.2.3: a Kalman-only box carries last frame's pose, so it
            # may keep an event alive but never confirm a new state from it.
            if source != "detected":
                continue
            k = np.array(track.kpts)
            hands = hands_by_track.setdefault(
                track.track_id, HandHistory(tun.traj_window))
            for side, wi in (("L", WRI_L), ("R", WRI_R)):
                hands.push(side, Sample(idx, k[wi, 0], k[wi, 1], k[wi, 2],
                                        tun.wrist_conf_th))

            ev = events.get(track.track_id)
            if ev is None or ev.state in TERMINAL:
                if ev is not None and ev.state in TERMINAL:
                    continue          # one decision per track per clip
                ev = CustomerCashEvent(track.track_id, idx)
                events[track.track_id] = ev
            before = ev.state
            step(ev, idx, k, torso_len(k), obs, hands, tun)
            if ev.state != before:
                row = {"frame": idx, "t": round(idx / eff_fps, 2),
                       "track": track.track_id, "from": before, "to": ev.state,
                       "zone": ev.zone, "reasons": list(ev.reasons)}
                trace.append(row)
                print(f"  t={row['t']:5.2f}s track{row['track']:<3} "
                      f"{before:<22} -> {ev.state}")
                if ev.state == CONCEALED:
                    alerts.append(row)
        idx += 1

    cap.release()
    print(f"\n{Path(a.clip).name}: {idx} frames processed @ {eff_fps:.1f} fps "
          f"(source {src_fps:g}, stride {stride})")
    print(f"  till at end: {'Open' if last_till_open else 'Close'}")
    concealed = [e for e in events.values() if e.state == CONCEALED]
    resolved = [e for e in events.values() if e.state == RESOLVED]
    print(f"  ALERTS (cash to body): {len(concealed)}")
    for e in concealed:
        print(f"    track {e.track_id}  zone={e.zone}  "
              f"exchange_hand={e.exchange_hand} pocket_hand={e.active_hand}"
              f" customer_seen={e.customer_seen}")
        print(f"    reasons: {e.reasons}")
    print(f"  resolved to drawer:    {len(resolved)}")

    if a.json:
        Path(a.json).write_text(json.dumps(
            {"clip": a.clip, "fps": eff_fps, "trace": trace,
             "alerts": alerts}, indent=2))
        print(f"  wrote {a.json}")


if __name__ == "__main__":
    main()
