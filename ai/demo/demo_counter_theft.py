"""Counter theft demo: same FSM as cash_concealment_fsm.py, a cleaner render.

    python3 demo_counter_theft.py --clip cvlp_h3_00043_ --side Left
    python3 demo_counter_theft.py --clip cvlp_h3_00013_ --side Right

cash_concealment_fsm.render() was built to show every internal signal at once (id,
role, state, active hand, reason codes) for debugging the FSM itself, and a
6px full-frame border plus a 74px banner on every CONCEALED frame. That is
the right view for tuning thresholds and the wrong one for a demo: a viewer
should see "staff, ordinary" then "staff, THEFT SUSPECTED", not a state
machine's internals scrolling past. This reuses cash_concealment_fsm.run() for the
FSM itself - untouched, so the two views can never disagree on when an
alert fires - and only replaces render().
"""
import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[2]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import os

import cv2
import numpy as np

from ai.src.perception import cash_concealment_fsm as fsm
from ai.src.perception import zones

HERE = str(_Path(__file__).resolve().parents[2])

STAFF_COL = {
    fsm.IDLE: (0, 140, 255), fsm.DRAWER_OPEN: (0, 140, 255),
    fsm.HAND_IN_DRAWER: (0, 140, 255), fsm.HAND_EXITED: (0, 140, 255),
    fsm.HAND_TO_BODY: (0, 90, 255), fsm.CONCEALED: (0, 0, 255),
    fsm.ENDED: (0, 140, 255),
}
STAFF_TAG = {
    fsm.DRAWER_OPEN: "STAFF - drawer open",
    fsm.HAND_IN_DRAWER: "STAFF - hand in till",
    fsm.HAND_TO_BODY: "STAFF - checking",
}
CUSTOMER_COL = (60, 200, 60)


def draw_zones(img, div, checkout, w, h):
    if div is not None:
        p = div.p.copy()
        for i, j in ((0, 1), (-1, -2)):
            d = p[i] - p[j]
            p[i] = p[i] + d / (np.linalg.norm(d) + 1e-6) * (w + h)
        cv2.polylines(img, [p.astype(np.int32)], False, (210, 210, 210), 1,
                      cv2.LINE_AA)
    for name, poly in (checkout or {}).items():
        cv2.polylines(img, [poly.astype(np.int32)], True, (210, 210, 210), 1,
                      cv2.LINE_AA)


def render(video, out, per_frame, W, H, div, checkout):
    cap = cv2.VideoCapture(video)
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    wr = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*"mp4v"), fsm.FPS, (W, H))
    s = max(1.0, H / 540.0)
    th, fs = int(round(2 * s)), 0.55 * s
    step_, acc, idx = fsm.FPS / src_fps, 0.0, 0
    while True:
        if not cap.grab():
            break
        acc += step_
        if acc < 1.0:
            continue
        acc -= 1.0
        ok, frame = cap.retrieve()
        if not ok or idx not in per_frame:
            break
        shown, till = per_frame[idx]

        draw_zones(frame, div, checkout, W, H)

        if till["box"]:
            b = [int(v) for v in till["box"]]
            c = (0, 0, 255) if till["open"] else (90, 200, 90)
            cv2.rectangle(frame, (b[0], b[1]), (b[2], b[3]), c, th)
            label = "TILL OPEN" if till["open"] else "TILL CLOSED"
            cv2.putText(frame, label, (b[0], b[1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, fs * 0.8, c, max(1, th - 1))

        alert = None
        for t, role, ev in shown:
            x1, y1, x2, y2 = [int(v) for v in t["ltrb"]]
            state = ev["state"] if ev else fsm.IDLE
            if role == zones.STAFF:
                col = STAFF_COL.get(state, STAFF_COL[fsm.IDLE])
                tag = STAFF_TAG.get(state, "STAFF")
            else:
                col = CUSTOMER_COL
                tag = "CUSTOMER"
            cv2.rectangle(frame, (x1, y1), (x2, y2), col, th)
            (tw, tht), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
            cv2.rectangle(frame, (x1, y1 - tht - 8), (x1 + tw + 8, y1), col, -1)
            cv2.putText(frame, tag, (x1 + 4, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX,
                        fs, (0, 0, 0), th)
            if ev and ev["state"] == fsm.CONCEALED:
                alert = ev

        cv2.putText(frame, f"t={idx/fsm.FPS:4.1f}s", (10, H - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45 * s, (255, 255, 255), 1,
                    cv2.LINE_AA)

        if alert:
            bw, bh = int(340 * s), int(56 * s)
            x0, y0 = W - bw - 12, 12
            ov = frame.copy()
            cv2.rectangle(ov, (x0, y0), (x0 + bw, y0 + bh), (0, 0, 180), -1)
            cv2.addWeighted(ov, 0.75, frame, 0.25, 0, frame)
            cv2.rectangle(frame, (x0, y0), (x0 + bw, y0 + bh), (0, 0, 255),
                          max(1, th - 1))
            cv2.putText(frame, "! THEFT SUSPECTED", (x0 + 10, y0 + int(24 * s)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6 * s, (255, 255, 255), th,
                        cv2.LINE_AA)
            cv2.putText(frame, "cash taken from drawer", (x0 + 10, y0 + int(44 * s)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42 * s, (255, 255, 255), 1,
                        cv2.LINE_AA)
            cv2.rectangle(frame, (0, 0), (W, H), (0, 0, 255), max(2, th - 1))

        wr.write(frame)
        idx += 1
    cap.release()
    wr.release()
    from ai.src.perception.video_io import to_h264
    to_h264(out)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--clip", required=True)
    p.add_argument("--side", required=True, choices=["Left", "Right"])
    p.add_argument("--dump", default=f"{HERE}/ai/dev/outputs/counter-generated/frames_json")
    p.add_argument("--zones",
                   default=f"{HERE}/ai/dev/outputs/top-side-view/top-side-view_zones.json")
    p.add_argument("--out")
    a = p.parse_args()

    video = f"{HERE}/data/videos/counter-generated/{a.side}/{a.clip}.mp4"
    out = a.out or f"{HERE}/ai/demo/counter-generated/{a.clip}{a.side}.mp4"
    os.makedirs(os.path.dirname(out), exist_ok=True)

    import json
    d = json.load(open(f"{a.dump}/{a.clip}.json"))
    W, H = d["size"]
    div, _ = zones.load_divider(a.zones, size=(W, H))
    checkout = fsm.load_checkout_zones(a.zones, (W, H))

    events, per_frame = fsm.run(a.clip, a.side, a.dump, video,
                                out_events=f"{HERE}/ai/demo/counter-generated/{a.clip}{a.side}_events.jsonl",
                                out_video=None, zones_path=a.zones)
    n_alert = sum(1 for e in events if e.get("event_type"))
    print(f"{a.clip}{a.side}: {n_alert} theft alert(s)")
    render(video, out, per_frame, W, H, div, checkout)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
