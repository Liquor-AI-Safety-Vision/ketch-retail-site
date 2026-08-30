"""Problem 2, Pipeline A: the drawer-to-body concealment FSM.

    python3 scripts/problem2_fsm.py --clip cvlp_h3_00043_ --side Left

Reads the per-frame perception dump written by probe_wrist.py rather than
re-running the models. Perception costs 220 ms/frame and does not change while
the rule thresholds are being tuned, so replaying the dump turns a 30-second
iteration into a 30-millisecond one. The dump is already decimated to 10 fps,
so one frame is 0.1 s and every threshold below is stated in seconds.

Writes an event JSONL in the section 9.3 shape and, with --video, an annotated
MP4 showing the state, the reason codes and the alert banner.
"""
import argparse
import json
import os
from collections import deque

import cv2
import numpy as np

from backend.src.perception import zones

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SHO_L, SHO_R, ELB_L, ELB_R, WRI_L, WRI_R, HIP_L, HIP_R = 5, 6, 7, 8, 9, 10, 11, 12
FPS = 10.0

# Phu luc A, converted to frames at 10 fps
WRIST_CONF_TH = 0.30
HAND_INSIDE_CONFIRM = 3      # 0.3 s
HAND_EXIT_CONFIRM = 3        # 0.3 s
POSE_GRACE = 5               # 0.5 s
DWELL_CONFIRM = 5            # 0.5 s at the concealment target
STATE_TIMEOUT = 50           # 5.0 s in any transient state
POST_EVENT = 30              # 3.0 s
TRAJ_WINDOW = 25             # 2.5 s of wrist history, hand_traj_window
TRAJ_LOOKBACK = 5            # compare against the sample k valid frames ago

# geometry, all in units of torso length so they survive distance from camera
DRAWER_ROI_EXPAND = 0.25     # fraction of the till box, section 6.2
#
# Section 6.2 tests wrist-inside-drawer-ROI. That test cannot work at this
# camera: the till box measures 83x66 px against a 67 px torso, so the drawer
# is nearly the size of the person, and the employee stands against it. Their
# pocket and the drawer ROI are the same region of the image - measured on
# cvlp_h3_00043_, box_dist(wrist, ROI) reads 0.00 for all 80 frames, both while
# the hand is in the cash and while it is at the hip.
#
# What does separate them is distance over time, the section 6.3 feature.
# Measured on cvlp_h3_00043_, active (right) hand, torso units:
#
#                       in the drawer      at the pocket
#   d(wrist, till c)    0.51 - 0.68        0.96 - 1.07
#   d(wrist, pocket)    0.47 - 0.59        0.14 - 0.29
#
# Both separate, and the pocket distance separates wider. Drawer entry and exit
# get two thresholds with a gap between them so a wrist hovering at the boundary
# cannot chatter between states.
DRAWER_IN = 0.80             # d(wrist, till centre) at or under this = in
DRAWER_OUT = 0.90            # at or over this = out. The gap is the hysteresis
POCKET_RADIUS = 0.30         # concealment zone around the hip, section 6.4
HAND_FOLLOW_GATE = 0.60      # a wrist this far from the active hand is not it
# A hand actually in the cash reaches the till centre; a hand resting beside the
# drawer does not. Measured on cvlp_h3_00043_ with yolo26x: the hand in the till
# reads 0.09-0.12 torso, the other hand 0.57-0.69.
DEEP_IN = 0.35               # d(wrist, till centre) that counts as in the cash
WITHDRAW_DELTA = 0.25        # rise from the deepest point that counts as pulling out
ANY_HAND = False             # set by --any-hand; only gates HAND_IN_DRAWER now -
                              # HAND_EXITED and HAND_TO_BODY always check both
                              # hands (see those branches)

IDLE, DRAWER_OPEN, HAND_IN_DRAWER = "IDLE", "DRAWER_OPEN", "HAND_IN_DRAWER"
HAND_EXITED, HAND_TO_BODY = "HAND_EXITED", "HAND_TO_BODY"
CONCEALED, ENDED = "CONCEALMENT_SUSPECTED", "EVENT_END"


def load_checkout_zones(path, size):
    """The checkout polygons, in the frame size they will be tested against.

    Without POS data these are the only evidence that a drawer opening had a
    reason: a customer standing at the station being used means a transaction is
    plausible. One polygon per station, never one for the whole counter - a
    customer at the left till must not silence an alert at the right one.

    Points are fractions of the frame, so `size` is the only thing that turns
    them into pixels -- no rescale step, and nothing to forget.
    """
    d = json.load(open(path))
    W, H = float(size[0]), float(size[1])
    out = {}
    for z in d["zones"]:
        if z["name"].startswith("checkout") and z.get("type") == "polygon":
            out[z["name"]] = np.array(
                [[fx * W, fy * H] for fx, fy in zones._points_norm(z, path)],
                np.float32)
    return out


def point_in_poly(pt, poly):
    return cv2.pointPolygonTest(poly, (float(pt[0]), float(pt[1])), False) >= 0


def zone_for_till(till_c, zones_):
    """Pair the open till with its own checkout zone by nearest centroid."""
    if till_c is None or not zones_:
        return None
    best, bd = None, 1e18
    for name, poly in zones_.items():
        c = poly.mean(0)
        d = float(np.hypot(*(c - np.asarray(till_c))))
        if d < bd:
            best, bd = name, d
    return best


def torso_len(k):
    mid_s = (k[SHO_L, :2] + k[SHO_R, :2]) / 2
    mid_h = (k[HIP_L, :2] + k[HIP_R, :2]) / 2
    return max(float(np.linalg.norm(mid_s - mid_h)), 1e-3)


def expand_box(b, f):
    x1, y1, x2, y2 = b
    w, h = x2 - x1, y2 - y1
    return [x1 - f * w, y1 - f * h, x2 + f * w, y2 + f * h]


def box_dist(pt, b):
    """0 inside the box, else the euclidean distance to its edge."""
    dx = max(b[0] - pt[0], 0.0, pt[0] - b[2])
    dy = max(b[1] - pt[1], 0.0, pt[1] - b[3])
    return float(np.hypot(dx, dy))


def seg_hits_box(p, q, b):
    """Sampled elbow-wrist / ROI test. The forearm can cross the drawer while
    the wrist itself is already past it, section 6.2."""
    for t in np.linspace(0, 1, 9):
        pt = p + (q - p) * t
        if b[0] <= pt[0] <= b[2] and b[1] <= pt[1] <= b[3]:
            return True
    return False


class Sample:
    __slots__ = ("f", "x", "y", "conf", "valid")

    def __init__(self, f, x, y, conf):
        self.f, self.x, self.y, self.conf = f, x, y, conf
        self.valid = conf >= WRIST_CONF_TH

    @property
    def xy(self):
        return np.array([self.x, self.y])


class HandHistory:
    """Two deques, never merged: one hand can close the drawer while the other
    takes the cash, section 4.2.2. Invalid samples are recorded, not skipped -
    the rule must tell 'hand elsewhere' from 'hand not seen'."""

    def __init__(self):
        self.h = {"L": deque(maxlen=TRAJ_WINDOW), "R": deque(maxlen=TRAJ_WINDOW)}

    def push(self, side, s):
        self.h[side].append(s)

    def last_valid(self, side, n=1):
        v = [s for s in self.h[side] if s.valid]
        return v[-n] if len(v) >= n else None

    def moved_to_concealment(self, side, till_c, target, k=TRAJ_LOOKBACK):
        """d_hand_drawer up while d_hand_target down, section 6.3."""
        v = [s for s in self.h[side] if s.valid]
        if len(v) < k + 1:
            return False, None
        now, past = v[-1], v[-(k + 1)]
        d_till_now = np.linalg.norm(now.xy - till_c)
        d_till_past = np.linalg.norm(past.xy - till_c)
        d_tgt_now = np.linalg.norm(now.xy - target)
        d_tgt_past = np.linalg.norm(past.xy - target)
        ok = d_till_now > d_till_past and d_tgt_now < d_tgt_past
        return ok, {"d_till": (round(d_till_past, 1), round(d_till_now, 1)),
                    "d_target": (round(d_tgt_past, 1), round(d_tgt_now, 1))}


class ConcealmentEvent:
    def __init__(self, track_id, frame):
        self.track_id = track_id
        self.state = IDLE
        self.since = frame
        self.start = frame
        self.active_hand = None
        # Where the active hand was last seen. The hand is followed by this
        # position, not by its L/R label: the pose model flips the whole
        # skeleton's handedness depending on which way it reads the person as
        # facing - measured on cvlp_h3_00043_, both wrists and both shoulders
        # swap together at f6->f12 and again at f66->f72. A label that flips
        # mid-event cannot identify the hand holding the cash.
        self.hand_xy = None
        self.reasons = []
        self.inside_run = {"L": 0, "R": 0}
        self.outside_run = 0
        self.miss = 0
        # HAND_TO_BODY tracks both hands against their own pocket target in
        # parallel, not one "active" hand - a single wrist's confidence score
        # does not certify its position is right (measured on cvlp_h3_00043_:
        # the tracked wrist sat at 0.35-0.93 confidence, safely above
        # WRIST_CONF_TH, while its pixel position drifted onto the forearm,
        # a full torso-fraction away from the hand actually visible in frame -
        # so the confirmed hand never dwelled near its target while the other,
        # unexamined wrist may have been sitting right on it the whole time).
        self.body_dwell = {"L": 0, "R": 0}
        self.body_miss = {"L": 0, "R": 0}
        self.d_min = None        # deepest the active hand reached in the till
        self.deep = False        # did it reach DEEP_IN at all
        self.alerted = False
        self.end = None

    def to(self, state, frame, reason=None):
        self.state = state
        self.since = frame
        if reason and reason not in self.reasons:
            self.reasons.append(reason)


def pocket_targets(k, torso):
    """Concealment zones from pose, scaled by torso, section 6.4. Pocket sits
    below the hip on each side; chest sits between shoulder and hip."""
    out = {}
    for side, hip, sho in (("L", HIP_L, SHO_L), ("R", HIP_R, SHO_R)):
        h = k[hip, :2]
        s = k[sho, :2]
        down = (h - s)
        n = np.linalg.norm(down)
        down = down / n if n > 1e-6 else np.array([0.0, 1.0])
        out[side] = h + down * (0.15 * torso)      # upper thigh / pocket line
    out["chest"] = (k[SHO_L, :2] + k[SHO_R, :2]) / 2 * 0.5 + \
                   (k[HIP_L, :2] + k[HIP_R, :2]) / 2 * 0.5
    return out


def step(ev, frame, k, torso, till_box, till_open, hands, log):
    """One frame of the state machine for one employee track."""
    roi = expand_box(till_box, DRAWER_ROI_EXPAND) if till_box else None
    targets = pocket_targets(k, torso)
    till_c = np.array([(till_box[0] + till_box[2]) / 2,
                       (till_box[1] + till_box[3]) / 2]) if till_box else None

    def d_till(side):
        """Wrist to till centre in torso units, or None if not measurable."""
        w = WRI_L if side == "L" else WRI_R
        if till_c is None or k[w, 2] < WRIST_CONF_TH:
            return None
        return float(np.linalg.norm(k[w, :2] - till_c)) / torso

    def hand_in_drawer(side):
        d = d_till(side)
        if d is None:
            return False
        if d <= DRAWER_IN:
            return True
        # the forearm can cross the drawer with the wrist already past it
        e = ELB_L if side == "L" else ELB_R
        w = WRI_L if side == "L" else WRI_R
        return (roi is not None and k[e, 2] >= WRIST_CONF_TH
                and seg_hits_box(k[e, :2], k[w, :2], roi))

    def hand_out_of_drawer(side):
        d = d_till(side)
        return d is not None and d >= DRAWER_OUT

    def wrist_xy(side):
        w = WRI_L if side == "L" else WRI_R
        return k[w, :2] if k[w, 2] >= WRIST_CONF_TH else None

    def follow_hand(ev):
        """Re-identify the active hand this frame by nearest position, so a
        handedness flip in the pose output does not hand the event to the other
        arm. Returns the side to use, or None if neither wrist is close enough."""
        if ev.hand_xy is None:
            return ev.active_hand
        best, best_d = None, HAND_FOLLOW_GATE * torso
        for s in ("L", "R"):
            p = wrist_xy(s)
            if p is None:
                continue
            d = float(np.linalg.norm(p - ev.hand_xy))
            if d < best_d:
                best, best_d = s, d
        return best

    st = ev.state

    if st == IDLE:
        if till_open:
            ev.to(DRAWER_OPEN, frame, "DRAWER_OPEN_CONFIRMED")
            ev.start = frame

    elif st == DRAWER_OPEN:
        # Either hand may be the one in the cash - one hand can work the drawer
        # while the other takes from it. Both are tracked, and the hand that
        # confirms is the one reaching deepest, never a fixed L-then-R
        # preference: on cvlp_h3_00043_ both wrists sit within DRAWER_IN while
        # only one is actually in the till (0.08 torso against 0.65).
        ready = []
        for side in ("L", "R"):
            ev.inside_run[side] = ev.inside_run[side] + 1 if hand_in_drawer(side) else 0
            if ev.inside_run[side] >= HAND_INSIDE_CONFIRM:
                ready.append((d_till(side) if d_till(side) is not None else 9e9,
                              side))
        if ready:
            _, side = min(ready)
            ev.active_hand = side
            ev.hand_xy = wrist_xy(side)
            ev.outside_run = 0
            ev.to(HAND_IN_DRAWER, frame, "HAND_IN_DRAWER")
        elif frame - ev.since > STATE_TIMEOUT and not till_open:
            ev.to(ENDED, frame)

    elif st == HAND_IN_DRAWER:
        side = follow_hand(ev) or ev.active_hand
        if ANY_HAND and not hand_in_drawer(side):
            other = "L" if side == "R" else "R"
            if hand_in_drawer(other):
                side = other
        ev.active_hand = side
        if wrist_xy(side) is not None:
            ev.hand_xy = wrist_xy(side)
        d = d_till(side)
        if d is not None:
            ev.miss = 0
            if ev.d_min is None or d < ev.d_min:
                ev.d_min = d
            if d <= DEEP_IN and not ev.deep:
                ev.deep = True
                ev.reasons.append("HAND_REACHED_INTO_CASH")
        else:
            ev.miss += 1

        # Pulling out shows two ways, and at this camera the second is the
        # common one: the wrist rises off its deepest point, or it stops being
        # visible at all because the body has turned over it. Either counts,
        # but only after the hand was genuinely in the cash.
        withdrew = (d is not None and ev.d_min is not None
                    and d - ev.d_min >= WITHDRAW_DELTA)
        vanished = ev.deep and ev.miss >= POSE_GRACE
        if withdrew or hand_out_of_drawer(side):
            ev.outside_run += 1
            if ev.outside_run >= HAND_EXIT_CONFIRM:
                ev.to(HAND_EXITED, frame, "HAND_EXIT_CONFIRMED")
        elif vanished:
            ev.to(HAND_TO_BODY, frame, "HAND_VANISHED_AFTER_CASH")
        else:
            ev.outside_run = 0
        if frame - ev.since > STATE_TIMEOUT:
            ev.to(ENDED, frame)

    elif st == HAND_EXITED:
        # Trajectory is checked for both hands, not the one hand-tracking
        # picked as "active" - the hand that grabbed the cash is known from
        # the drawer phase, but which of L/R the pose model calls it by can
        # still be wrong even at high confidence (section on cvlp_h3_00043_),
        # so gating the exit-trajectory test on a single chosen side is the
        # same failure mode one step earlier. Either hand moving toward its
        # own target is enough to proceed.
        moved_any = False
        for side in ("L", "R"):
            moved, detail = hands.moved_to_concealment(side, till_c, targets[side]) \
                if till_c is not None else (False, None)
            if moved:
                moved_any = True
                log.append({"frame": frame, "side": side, "detail": detail})
        if moved_any:
            ev.to(HAND_TO_BODY, frame, "HAND_TRAJECTORY_TO_BODY")
        elif ev.deep and wrist_xy("L") is None and wrist_xy("R") is None:
            # both hands unseen - genuinely vanished, not just relabeled
            ev.miss += 1
            if ev.miss >= POSE_GRACE:
                ev.to(HAND_TO_BODY, frame, "HAND_VANISHED_AFTER_CASH")
        elif frame - ev.since > STATE_TIMEOUT:
            ev.to(ENDED, frame)

    elif st == HAND_TO_BODY:
        # Both hands tracked in parallel against their own pocket target,
        # section 6.4 revised: a single wrist's confidence score does not
        # certify its position is right, so committing to one "active" hand
        # here means a confidently-wrong keypoint can starve the dwell
        # counter for the entire state while the untracked wrist may be
        # sitting right on its target the whole time. Either hand reaching
        # its own target is what a real concealment looks like from either
        # side of the body - there is no requirement that it be the same
        # hand that was in the drawer.
        confirmed_side = None
        for side in ("L", "R"):
            w = WRI_L if side == "L" else WRI_R
            tgt = targets[side]
            if k[w, 2] >= WRIST_CONF_TH:
                ev.body_miss[side] = 0
                if np.linalg.norm(k[w, :2] - tgt) <= POCKET_RADIUS * torso:
                    ev.body_dwell[side] += 1
                else:
                    ev.body_dwell[side] = 0
            else:
                # pose lost inside the target zone is a supporting signal,
                # never used alone, section 6.3
                ev.body_miss[side] += 1
                last = hands.last_valid(side)
                if last is not None and \
                        np.linalg.norm(last.xy - tgt) <= POCKET_RADIUS * torso:
                    ev.body_dwell[side] += 1
                    if ev.body_miss[side] == 1:
                        ev.reasons.append(f"HAND_OCCLUDED_IN_TARGET_ZONE_{side}")
            if ev.body_dwell[side] >= DWELL_CONFIRM:
                confirmed_side = side
                break
        if wrist_xy(confirmed_side or "L") is not None:
            ev.hand_xy = wrist_xy(confirmed_side or "L")
        if confirmed_side:
            ev.active_hand = confirmed_side
            ev.to(CONCEALED, frame, "CONCEALMENT_DWELL_CONFIRMED")
        elif (ev.deep and max(ev.body_miss.values()) >= DWELL_CONFIRM
              and "HAND_TRAJECTORY_TO_BODY" in ev.reasons):
            # Occlusion supports the conclusion, it never reaches it alone -
            # section 6.3. Measured: on cash_till_open_2, a legitimate opening
            # at t=68.7s raised an alert whose only evidence was
            # HAND_VANISHED_AFTER_CASH then HAND_OCCLUDED_AT_BODY, with the
            # movement toward the body never once observed. Requiring the
            # trajectory to have been seen first drops that alert and keeps
            # both staged concealments.
            occ_side = max(ev.body_miss, key=ev.body_miss.get)
            ev.active_hand = occ_side
            ev.to(CONCEALED, frame, "HAND_OCCLUDED_AT_BODY")
        elif frame - ev.since > STATE_TIMEOUT:
            ev.to(ENDED, frame)

    elif st == CONCEALED:
        if frame - ev.since > POST_EVENT:
            ev.to(ENDED, frame)

    return ev


def run(clip, side, dump_dir, video_path, out_events, out_video, zones_path):
    d = json.load(open(f"{dump_dir}/{clip}.json"))
    W, H = d["size"]
    div, _ = zones.load_divider(zones_path, size=(W, H))
    voter = zones.RoleVoter(div)
    checkout = load_checkout_zones(zones_path, (W, H))
    print(f"  checkout zones: {', '.join(sorted(checkout)) or 'none'}")

    events, per_frame, traj_log = [], {}, []
    evs, hands_by_track = {}, {}
    last_till = {"box": None, "open": False, "centre": None}
    # zone name -> last frame a customer's anchor point fell inside it. A
    # customer who was served, stepped back, and left the polygon a moment
    # before the till closes and the hand comes back must still count as
    # "served" - checking only the instant of confirmation missed exactly
    # that sequence (pay, customer walks off, staff closes the drawer and
    # pockets the change a beat later), so this is a trailing window instead.
    last_customer_seen = {}
    CUSTOMER_WINDOW_F = round(3.0 * FPS)

    for fr in d["frames"]:
        f = fr["f"]
        opened = [t for t in fr["tills"] if t["state"] == "Open"]
        if opened:
            last_till["open"] = True
            if opened[0]["box"]:
                last_till["box"] = opened[0]["box"]
        else:
            last_till["open"] = any(t["state"] == "Open" for t in fr["tills"])

        till_c = None
        if last_till["box"]:
            b = last_till["box"]
            till_c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        zname = zone_for_till(till_c, checkout)

        for name, poly in checkout.items():
            for t in fr["tracks"]:
                if voter.role(t["id"]) != zones.CUSTOMER:
                    continue
                pt, _ = zones.anchor(t["ltrb"], np.array(t["kpts"])
                                     if t["kpts"] else None)
                if point_in_poly(pt, poly):
                    last_customer_seen[name] = f
                    break

        served = (zname is not None and zname in last_customer_seen
                 and f - last_customer_seen[zname] <= CUSTOMER_WINDOW_F)

        shown = []
        for t in fr["tracks"]:
            role, _ = voter.update(t["id"], t["ltrb"], np.array(t["kpts"])
                                   if t["kpts"] else None)
            if role != zones.STAFF or not t["kpts"]:
                shown.append((t, role, None))
                continue
            k = np.array(t["kpts"])
            torso = torso_len(k)
            hands = hands_by_track.setdefault(t["id"], HandHistory())
            for sname, wi in (("L", WRI_L), ("R", WRI_R)):
                hands.push(sname, Sample(f, k[wi, 0], k[wi, 1], k[wi, 2]))

            ev = evs.get(t["id"])
            if ev is None or ev.state == ENDED:
                ev = ConcealmentEvent(t["id"], f)
                evs[t["id"]] = ev
            before = ev.state
            step(ev, f, k, torso, last_till["box"], last_till["open"], hands,
                 traj_log)
            if ev.state == CONCEALED and not ev.alerted and served:
                # a customer was at this station within the last 3s: the
                # opening has a plausible reason, so this is logged, not
                # raised. Without POS this is the only check standing between
                # the rule and every act of making change for a customer.
                ev.alerted = True
                events.append({"event_id": f"{clip}{side}_{f:04d}",
                               "event": "SUPPRESSED", "frame": f,
                               "t": round(f / FPS, 1),
                               "reason": "CUSTOMER_AT_CHECKOUT_RECENT",
                               "zone": zname, "track": ev.track_id})
            elif ev.state == CONCEALED and not ev.alerted:
                ev.alerted = True
                events.append({
                    "event_id": f"{clip}{side}_{f:04d}",
                    "camera_id": "GNG23_counter_top",
                    "event_type": "SUSPECTED_CASH_CONCEALMENT",
                    "severity": "high",
                    "employee_track_id": ev.track_id,
                    "start_frame": ev.start, "start_t": round(ev.start / FPS, 1),
                    "alert_frame": f, "alert_t": round(f / FPS, 1),
                    "active_hand": "RIGHT" if ev.active_hand == "R" else "LEFT",
                    "warning_sent": True,
                    "reason_codes": list(ev.reasons),
                })
            if before != ev.state:
                events.append({"event_id": f"{clip}{side}_{ev.start:04d}",
                               "event": "STATE", "frame": f,
                               "t": round(f / FPS, 1),
                               "from": before, "to": ev.state,
                               "track": t["id"]})
            # snapshot, not the live object: the event mutates every frame, so
            # storing the reference would make every rendered frame show the
            # state the event happened to finish in
            shown.append((t, role, {"state": ev.state,
                                    "active_hand": ev.active_hand,
                                    "reasons": list(ev.reasons),
                                    "track_id": ev.track_id}))
        per_frame[f] = (shown, dict(last_till))

    with open(out_events, "w") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")

    if out_video:
        render(video_path, out_video, per_frame, W, H)
    return events, per_frame


SKEL = [(5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (5, 11), (6, 12), (11, 12)]
STATE_COL = {IDLE: (170, 170, 170), DRAWER_OPEN: (0, 190, 255),
             HAND_IN_DRAWER: (0, 140, 255), HAND_EXITED: (0, 90, 255),
             HAND_TO_BODY: (0, 40, 255), CONCEALED: (0, 0, 255),
             ENDED: (120, 120, 120)}


def render(video, out, per_frame, W, H):
    cap = cv2.VideoCapture(video)
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    wr = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    step_, acc, idx = FPS / src_fps, 0.0, 0
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

        if till["box"]:
            b = [int(v) for v in till["box"]]
            c = (0, 0, 255) if till["open"] else (90, 200, 90)
            cv2.rectangle(frame, (b[0], b[1]), (b[2], b[3]), c, 2)
            cv2.putText(frame, "DRAWER " + ("OPEN" if till["open"] else "CLOSED"),
                        (b[0], b[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 2)

        alert = None
        for t, role, ev in shown:
            x1, y1, x2, y2 = [int(v) for v in t["ltrb"]]
            col = STATE_COL.get(ev["state"] if ev else IDLE,
                                (170, 170, 170)) if role == zones.STAFF \
                else (200, 200, 200)
            cv2.rectangle(frame, (x1, y1), (x2, y2), col, 2)
            tag = f"ID{t['id']} {role}"
            if ev:
                tag += f"  {ev['state']}"
                if ev["active_hand"]:
                    tag += f" [{ev['active_hand']}]"
            cv2.putText(frame, tag, (x1, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, col, 2)
            if t["kpts"]:
                k = np.array(t["kpts"])
                for a, b_ in SKEL:
                    if k[a, 2] > 0.3 and k[b_, 2] > 0.3:
                        cv2.line(frame, tuple(k[a, :2].astype(int)),
                                 tuple(k[b_, :2].astype(int)), col, 1)
                if ev and ev["active_hand"]:
                    wi = WRI_L if ev["active_hand"] == "L" else WRI_R
                    if k[wi, 2] > WRIST_CONF_TH:
                        cv2.circle(frame, tuple(k[wi, :2].astype(int)), 7,
                                   (0, 255, 255), 2)
            if ev and ev["state"] == CONCEALED:
                alert = ev

        cv2.rectangle(frame, (0, 0), (W, 26), (0, 0, 0), -1)
        cv2.putText(frame, f"f{idx:3d}  t={idx/FPS:4.1f}s", (8, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        if alert:
            cv2.rectangle(frame, (0, 0), (W, H), (0, 0, 255), 6)
            bh = 74
            cv2.rectangle(frame, (0, H - bh), (W, H), (0, 0, 200), -1)
            cv2.putText(frame, "! SUSPECTED CASH CONCEALMENT", (12, H - bh + 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(frame, " | ".join(alert["reasons"][:4]), (12, H - bh + 52),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
            cv2.putText(frame, f"track {alert['track_id']}  hand "
                        f"{'RIGHT' if alert['active_hand'] == 'R' else 'LEFT'}",
                        (12, H - bh + 68), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                        (255, 255, 255), 1)
        wr.write(frame)
        idx += 1
    cap.release()
    wr.release()
    from backend.src.perception.video_io import to_h264
    to_h264(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--clip", required=True)
    p.add_argument("--side", choices=["Left", "Right"],
                   help="which station the clip stages; also locates the video "
                        "under videos/counter-generated/. Omit and pass --video "
                        "for clips outside that set")
    p.add_argument("--video", help="path to the source clip, for footage that "
                                   "does not live under counter-generated/")
    p.add_argument("--out-dir")
    p.add_argument("--dump", default=f"{HERE}/outputs/counter-generated/frames_json")
    p.add_argument("--zones",
                   default=f"{HERE}/outputs/top-side-view/top-side-view_zones.json")
    p.add_argument("--no-video", action="store_true")
    p.add_argument("--wrist-conf", type=float,
                   help="override WRIST_CONF_TH. yolo26m returns an occluded "
                        "wrist at 0.38-0.58, above the 0.30 default, so the "
                        "valid flag cannot tell a hidden hand from a seen one; "
                        "yolo26x returns 0.07-0.09 and needs no override")
    p.add_argument("--any-hand", action="store_true",
                   help="let either hand satisfy each leg after drawer entry, "
                        "instead of holding one hand for the whole event")
    a = p.parse_args()
    globals()["ANY_HAND"] = a.any_hand
    if a.wrist_conf is not None:
        globals()["WRIST_CONF_TH"] = a.wrist_conf

    out_dir = a.out_dir or f"{HERE}/outputs/counter-generated/problem2"
    os.makedirs(out_dir, exist_ok=True)
    if a.video:
        video = a.video
    elif a.side:
        video = f"{HERE}/videos/counter-generated/{a.side}/{a.clip}.mp4"
    else:
        raise SystemExit("need --side (for counter-generated clips) or --video")
    sfx = "_anyhand" if a.any_hand else ""
    stem = f"{a.clip}{a.side or ''}"
    out_e = f"{out_dir}/{stem}_fsm{sfx}.jsonl"
    out_v = None if a.no_video else f"{out_dir}/{stem}_fsm{sfx}.mp4"

    events, per_frame = run(a.clip, a.side or "", a.dump, video, out_e, out_v,
                            a.zones)

    print(f"{stem}: {len(per_frame)} frames @ {FPS:g} fps")
    print("\nstate transitions")
    for e in events:
        if e.get("event") == "STATE":
            print(f"  t={e['t']:4.1f}s  track{e['track']}  "
                  f"{e['from']:<22} -> {e['to']}")
    sup = [e for e in events if e.get("event") == "SUPPRESSED"]
    if sup:
        print(f"\nsuppressed (customer at the station): {len(sup)}")
        for e in sup:
            print(f"  t={e['t']:4.1f}s  track{e['track']}  zone={e['zone']}")
    alerts = [e for e in events if e.get("event_type")]
    print(f"\nalerts: {len(alerts)}")
    for e in alerts:
        print(json.dumps(e, indent=2))
    print(f"\nsaved {out_e}" + (f"\n      {out_v}" if out_v else ""))


if __name__ == "__main__":
    main()
