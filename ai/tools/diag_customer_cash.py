"""Why did the take-from-customer FSM stay in IDLE?

Walks the same perception path as run_customer_cash.py but reports each
precondition separately, so the layer that fails is named rather than
inferred. The FSM needs all four of these to be true at once:

    1. people are detected at all
    2. one of them is assigned STAFF, one CUSTOMER
    3. the customer's anchor falls inside a checkout polygon
    4. the staff wrist enters that same polygon

    python ai/tools/diag_customer_cash.py --clip <mp4>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from ai.src.perception import stream_track as S
from ai.src.perception import zones
from ai.src.perception.cash_concealment_fsm import (
    WRI_L, WRI_R, load_checkout_zones, torso_len)
from ai.src.perception.person_tracker import Detection, Tracker


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--clip", required=True)
    p.add_argument("--zones",
                   default=str(ROOT / "ai/config/zones/top_side_view_zones.json"))
    p.add_argument("--pose-model", default=str(ROOT / "ai/models/yolo26m-pose.onnx"))
    p.add_argument("--target-fps", type=float, default=12.0)
    p.add_argument("--conf", type=float, default=0.35)
    a = p.parse_args()

    cap = cv2.VideoCapture(a.clip)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    stride = max(1, int(round(src_fps / a.target_fps)))

    S.MODEL, S.CONF = a.pose_model, a.conf
    S.ORT_PROVIDERS = ["CPUExecutionProvider"]
    det, emb = S.Detector(), S.Embedder()

    ok, first = cap.read()
    if not ok:
        raise SystemExit(f"cannot read {a.clip}")
    h, w = first.shape[:2]
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    divider, _ = zones.load_divider(a.zones, size=(w, h))
    voter = zones.RoleVoter(divider)
    checkout = load_checkout_zones(a.zones, (w, h))
    tracker = Tracker(img_h=h, img_w=w, fps=src_fps / stride)

    tally = {
        "frames": 0, "with_detections": 0, "det_total": 0,
        "staff_frames": 0, "customer_frames": 0,
        "customer_in_zone_frames": 0, "staff_wrist_in_zone_frames": 0,
        "both_frames": 0, "staff_kpts_frames": 0,
        "customer_box_overlap_frames": 0, "customer_wrist_frames": 0,
        "both_box_frames": 0,
    }
    roles_seen: dict[int, set] = {}
    wrist_min_dist = {}      # zone -> closest a staff wrist ever got (px)
    timeline: list[dict] = []
    raw = idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if raw % stride:
            raw += 1
            continue
        raw += 1
        tally["frames"] += 1

        boxes, kpts = det(frame)
        tally["det_total"] += len(boxes)
        if len(boxes):
            tally["with_detections"] += 1
        feats = emb(frame, boxes)
        dets = [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i])
                for i in range(len(boxes))]
        live, removed = tracker.update(dets, idx)
        for tid in removed:
            voter.drop(tid)

        staff, customer = [], []
        for track, source in live:
            role, _ = voter.update(track.track_id, track.ltrb, track.kpts)
            roles_seen.setdefault(track.track_id, set()).add(role)
            (staff if role == zones.STAFF else
             customer if role == zones.CUSTOMER else []).append((track, source))

        if staff:
            tally["staff_frames"] += 1
        if customer:
            tally["customer_frames"] += 1

        # Three candidate tests for "a customer is using this station". The
        # anchor is the pelvis, and behind a counter the pelvis is the one
        # landmark that is NOT visible -- so the obvious test may be the
        # wrong one. Measured side by side rather than argued about.
        cust_zones, cust_by_box, cust_by_wrist = set(), set(), set()
        for name, poly in checkout.items():
            px1, py1 = poly[:, 0].min(), poly[:, 1].min()
            px2, py2 = poly[:, 0].max(), poly[:, 1].max()
            for track, _src in customer:
                kk = np.array(track.kpts) if track.kpts is not None else None
                pt, _ = zones.anchor(track.ltrb, kk)
                if cv2.pointPolygonTest(poly, (float(pt[0]), float(pt[1])), False) >= 0:
                    cust_zones.add(name)
                x1, y1, x2, y2 = [float(v) for v in track.ltrb]
                if x1 < px2 and x2 > px1 and y1 < py2 and y2 > py1:
                    cust_by_box.add(name)
                if kk is not None:
                    for wi in (WRI_L, WRI_R):
                        if kk[wi, 2] < 0.30:
                            continue
                        if cv2.pointPolygonTest(
                                poly, (float(kk[wi, 0]), float(kk[wi, 1])), False) >= 0:
                            cust_by_wrist.add(name)
        if cust_zones:
            tally["customer_in_zone_frames"] += 1
        if cust_by_box:
            tally["customer_box_overlap_frames"] += 1
        if cust_by_wrist:
            tally["customer_wrist_frames"] += 1

        wrist_hit = set()
        best_torso = None          # closest approach this frame, torso units
        for track, _src in staff:
            if track.kpts is None:
                continue
            tally["staff_kpts_frames"] += 1
            k = np.array(track.kpts)
            torso = torso_len(k)
            for wi in (WRI_L, WRI_R):
                if k[wi, 2] < 0.30:
                    continue
                pt = (float(k[wi, 0]), float(k[wi, 1]))
                for name, poly in checkout.items():
                    d = cv2.pointPolygonTest(poly, pt, True)   # signed, px
                    prev = wrist_min_dist.get(name)
                    # d > 0 inside; track the largest value = closest approach
                    if prev is None or d > prev:
                        wrist_min_dist[name] = d
                    if d >= 0:
                        wrist_hit.add(name)
                    # report against the zone the customer is using, so the
                    # number answers "how far from the exchange", not "how
                    # far from the other till"
                    if name in (cust_zones or {"checkout_right"}):
                        dt = d / torso
                        if best_torso is None or dt > best_torso:
                            best_torso = dt
        if wrist_hit:
            tally["staff_wrist_in_zone_frames"] += 1
        if wrist_hit & cust_zones:
            tally["both_frames"] += 1
        if wrist_hit & cust_by_box:
            tally["both_box_frames"] += 1
        timeline.append({"f": idx, "cust": bool(cust_zones),
                         "box": bool(cust_by_box), "cw": bool(cust_by_wrist),
                         "d_torso": best_torso})
        idx += 1

    cap.release()

    print(f"\n{Path(a.clip).name}  ({tally['frames']} frames @ "
          f"{src_fps/stride:.1f} fps)")
    print(f"  1. detections           : {tally['with_detections']}/{tally['frames']} frames"
          f"  ({tally['det_total']/max(1,tally['frames']):.1f} people/frame)")
    print(f"  2. STAFF present        : {tally['staff_frames']}/{tally['frames']}")
    print(f"     CUSTOMER present     : {tally['customer_frames']}/{tally['frames']}")
    print(f"     staff has keypoints  : {tally['staff_kpts_frames']}/{tally['frames']}")
    print(f"  3. customer test A, anchor in zone : {tally['customer_in_zone_frames']}/{tally['frames']}")
    print(f"     customer test B, bbox overlaps  : {tally['customer_box_overlap_frames']}/{tally['frames']}")
    print(f"     customer test C, wrist in zone  : {tally['customer_wrist_frames']}/{tally['frames']}")
    print(f"  4. staff wrist IN a zone: {tally['staff_wrist_in_zone_frames']}/{tally['frames']}")
    print(f"  => both at once (A)     : {tally['both_frames']}/{tally['frames']}   <- current trigger")
    print(f"  => both at once (B)     : {tally['both_box_frames']}/{tally['frames']}   <- with bbox test")
    print("\n  closest a staff wrist came to each zone "
          "(px; >=0 means it entered):")
    for name, d in sorted(wrist_min_dist.items()):
        print(f"    {name:<16} {d:+8.1f}")
    print("\n  roles each track was ever given:")
    for tid, rs in sorted(roles_seen.items()):
        print(f"    track {tid:<3} {sorted(rs)}")

    print("\n  timeline (C = customer in zone, W = wrist distance to that zone")
    print("  in torso lengths; negative is outside, 0 is on the boundary):")
    print("    frame  ABC  d_wrist_torso   (A=anchor B=bbox C=cust wrist)")
    for row in timeline:
        mark = ("A" if row["cust"] else "-") + ("B" if row["box"] else "-") + ("C" if row["cw"] else "-")
        d = row["d_torso"]
        d_s = "   n/a" if d is None else f"{d:+6.2f}"
        print(f"    {row['f']:5d}  {mark}  {d_s}")


if __name__ == "__main__":
    main()
