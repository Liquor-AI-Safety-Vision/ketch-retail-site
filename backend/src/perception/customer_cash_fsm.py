"""Problem 3: cash taken from the customer's hand and pocketed.

The employee is handed money across the counter and puts it on their body
instead of into the till. No drawer opening is involved, which is what makes
this a separate machine rather than another branch of problem2_fsm: that FSM
is entered from DRAWER_OPEN_CONFIRMED, and measured on the four
cctv_counter_v2 clips the till detector reports Close on 48 of 48 samples in
every one of them. problem2_fsm would sit in IDLE through the whole event.

It is also the more serious behaviour of the two. Money out of an open till
is at least money the till knows about; money that never reaches the drawer
leaves no record anywhere, so there is nothing to reconcile against at the
end of the shift.

What separates theft from an ordinary sale is not the exchange -- every
transaction has one -- but where the hand goes afterwards. So the machine
watches one window: hand crosses to the customer, hand comes back, and then
either the cash reaches the drawer (a sale) or it reaches the body (an
alert).

Tuning is passed in, never read from module globals. problem2_fsm keeps its
thresholds in globals and that cost this repo a real bug: several cameras
run as threads in one process, so the second camera to bootstrap retuned the
first one's FSM. That was fixed by giving each pipeline a private copy of the
module; a new machine should not need the workaround, so `Tuning` is an
argument here from the start.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from backend.src.perception.problem2_fsm import (
    WRI_L, WRI_R, pocket_targets,
)

IDLE = "IDLE"
#: A customer is (or was just) at a station this employee is working. From
#: here the machine is watching where that employee's hands go.
TRANSACTION = "TRANSACTION_OPEN"
CONCEALED = "SUSPECTED_CASH_FROM_CUSTOMER"
RESOLVED = "RESOLVED_CASH_TO_DRAWER"
ENDED = "EVENT_END"

#: Kept as an alias so older traces and callers still read: the reach across
#: the counter is now a corroborating observation, not a state. Measured on
#: clip 00018 it could not be a state -- during the hand-off the two people
#: merge into one detection and the pose model hands the customer's forearm
#: to the employee's track, putting a "staff" wrist inside the checkout
#: polygon on the very frames the employee's real hand is at their pocket.
HAND_AT_EXCHANGE = TRANSACTION

#: States the event can finish in. RESOLVED is a finish, not an alert.
TERMINAL = frozenset({CONCEALED, RESOLVED, ENDED})

#: Alias for the removed intermediate state, so imports do not break.
HAND_WITHDRAWN = TRANSACTION


@dataclass(frozen=True)
class Tuning:
    """Thresholds in frames, built from seconds by `from_seconds`.

    Seconds are the only honest unit to state these in: the appendix numbers
    were measured at 10 fps, the v2 clips are 24 fps and the real counter
    footage is 15 fps, so a threshold written as a frame count means three
    different durations across the material this has to run on.
    """

    #: wrist must be inside the checkout polygon this long to count as a reach
    exchange_confirm: int = 3
    #: and back out of it this long to count as withdrawn
    withdraw_confirm: int = 3
    #: how long after the withdrawal the drawer may still redeem the event.
    #: Measured on cash_till_open_3, a legitimate sale: the customer is at the
    #: counter until t=8s but the drawer does not open until t=14.6s. A window
    #: shorter than that gap turns every slow-but-honest sale into an alert,
    #: which is the one failure mode that would get this switched off.
    decision_window: int = 180
    #: wrist within pocket_radius of its own target this long = concealed
    dwell_confirm: int = 5
    #: wrist unseen this long while it was last seen at the target
    pose_grace: int = 5
    #: no transient state may outlive this
    state_timeout: int = 150
    #: wrist history kept per hand
    traj_window: int = 25
    #: compare against the sample this many valid frames back
    traj_lookback: int = 5

    #: below this the wrist coordinate is not evidence of anything
    wrist_conf_th: float = 0.30
    #: concealment zone around the hip, in torso lengths
    pocket_radius: float = 0.30
    #: wrist this close to the till centre counts as cash reaching the drawer
    drawer_in: float = 0.80
    #: How far OUTSIDE a checkout polygon a wrist still counts as reaching
    #: into it, in torso lengths. Not zero, and that is the whole point: the
    #: polygon marks where the customer stands, but the money changes hands
    #: over the counter between them, which is the polygon's edge. Measured
    #: on clip 00018 the staff wrist entered checkout_right on 4 frames of 96
    #: and never while the customer was also inside it, so a containment test
    #: never fires on a real exchange.
    exchange_reach: float = 0.50
    #: A customer seen in the zone this recently still makes an exchange
    #: plausible. Same reasoning as the suppression window already in
    #: counter_cash_theft: the customer steps back the moment they have
    #: handed the money over, so requiring them on the confirming frame
    #: tests for the wrong instant.
    customer_recent: int = 30
    #: A wrist must get this many pocket-radii away from its own hip before a
    #: later dwell counts as arriving. Hands rest at the hips; without it the
    #: idle hand is permanently "concealing".
    away_factor: float = 1.6

    @staticmethod
    def from_seconds(fps: float, **seconds) -> "Tuning":
        """Build from durations in seconds at this camera's frame rate.

        Unknown keys raise rather than being ignored: a misspelled threshold
        that silently keeps its default is the failure this repo already hit
        once with pipeline ids, and it is invisible until an alert does not
        fire.
        """
        fps = float(fps) or 1.0
        scale = {
            "exchange_confirm_s": "exchange_confirm",
            "withdraw_confirm_s": "withdraw_confirm",
            "decision_window_s": "decision_window",
            "dwell_confirm_s": "dwell_confirm",
            "pose_grace_s": "pose_grace",
            "state_timeout_s": "state_timeout",
            "traj_window_s": "traj_window",
            "traj_lookback_s": "traj_lookback",
            "customer_recent_s": "customer_recent",
        }
        passthrough = {"wrist_conf_th", "pocket_radius", "drawer_in",
                       "exchange_reach", "away_factor"}
        kwargs: dict[str, object] = {}
        for key, value in seconds.items():
            if key in scale:
                kwargs[scale[key]] = max(1, int(round(float(value) * fps)))
            elif key in passthrough:
                kwargs[key] = float(value)
            else:
                known = ", ".join(sorted(set(scale) | passthrough))
                raise TypeError(
                    f"unknown tuning key {key!r}. Known keys: {known}")
        return Tuning(**kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Observation:
    """Everything about the frame that is not this employee's own pose.

    Grouped rather than passed as six positional arguments so that adding a
    signal later does not touch every call site.
    """

    #: zone name -> polygon, in pixels for this frame size
    checkout_zones: dict[str, np.ndarray] = field(default_factory=dict)
    #: zone name -> the last frame index a customer was seen inside it. A
    #: trailing record rather than a per-frame set, because the customer
    #: withdraws as soon as the money is handed over and the interesting
    #: part of the event happens after that.
    customer_last_seen: dict[str, int] = field(default_factory=dict)
    #: any till on this camera currently confirmed Open
    till_open: bool = False
    #: last known open-till box, or None
    till_box: list[float] | None = None

    def customer_recent(self, zone: str, frame: int, window: int) -> bool:
        last = self.customer_last_seen.get(zone)
        return last is not None and 0 <= frame - last <= window


class Sample:
    """One wrist reading. Invalid samples are recorded, never skipped.

    The rule has to tell "hand somewhere else" from "hand not seen", and a
    gap in the deque cannot carry that difference.
    """

    __slots__ = ("f", "x", "y", "conf", "valid")

    def __init__(self, f, x, y, conf, conf_th):
        self.f, self.x, self.y, self.conf = f, float(x), float(y), float(conf)
        self.valid = self.conf >= conf_th

    @property
    def xy(self):
        return np.array([self.x, self.y])


class HandHistory:
    """Two deques, never merged.

    One hand can take the money while the other works the keyboard, and the
    variants in the v2 clips include receiving with the left and moving the
    cash to the right before pocketing it -- so a merged trajectory would
    describe a movement neither hand made.
    """

    def __init__(self, maxlen: int):
        self.h = {"L": deque(maxlen=maxlen), "R": deque(maxlen=maxlen)}

    def push(self, side: str, s: Sample):
        self.h[side].append(s)

    def last_valid(self, side: str, n: int = 1):
        v = [s for s in self.h[side] if s.valid]
        return v[-n] if len(v) >= n else None

    def moved_toward(self, side: str, target, k: int):
        """True when this hand is closer to `target` than it was k valid
        samples ago. Used only as supporting evidence for the dwell."""
        v = [s for s in self.h[side] if s.valid]
        if len(v) < k + 1 or target is None:
            return False
        now, past = v[-1], v[-(k + 1)]
        return bool(np.linalg.norm(now.xy - target)
                    < np.linalg.norm(past.xy - target))


class CustomerCashEvent:
    """One employee track's progress through the machine."""

    def __init__(self, track_id: int, frame: int):
        self.track_id = int(track_id)
        self.state = IDLE
        self.since = frame
        self.start = frame
        self.reasons: list[str] = []
        #: the checkout zone the hand reached into, and the customer that
        #: made it a transaction rather than a reach across an empty counter
        self.zone: str | None = None
        #: per hand, so a handedness flip in the pose output cannot strand
        #: the event on the wrong arm
        self.inside_run = {"L": 0, "R": 0}
        self.outside_run = 0
        self.body_dwell = {"L": 0, "R": 0}
        self.body_miss = {"L": 0, "R": 0}
        #: has this hand been away from its own pocket during the event? A
        #: hand that never left cannot "arrive".
        self.was_away = {"L": False, "R": False}
        #: which hand was seen inside the exchange zone, for the alert record
        self.exchange_hand: str | None = None
        #: which hand reached the pocket. Need not be the same hand.
        self.active_hand: str | None = None
        #: the frame the reach was confirmed on, which is what the customer
        #: evidence is dated against
        self.exchange_frame: int | None = None
        #: has a customer been seen at this station near the exchange? Set as
        #: the event runs, tested only when an alert is about to be raised.
        self.customer_seen = False
        self.alerted = False
        self.end: int | None = None

    def to(self, state: str, frame: int, reason: str | None = None):
        self.state = state
        self.since = frame
        if reason and reason not in self.reasons:
            self.reasons.append(reason)


def _wrist(k, side: str, conf_th: float):
    """Wrist position, or None when the reading is not evidence."""
    idx = WRI_L if side == "L" else WRI_R
    return k[idx, :2] if k[idx, 2] >= conf_th else None


def _at_exchange(k, side, obs: Observation, torso: float, tun: "Tuning"):
    """The checkout station this wrist is reaching towards, if any.

    Two things measured on clip 00018 shaped this, and both cost a rewrite:

    Reaching is a DISTANCE, not containment. The polygon covers the floor the
    customer stands on; the hand-off happens above the counter between the two
    of them, which is the polygon's boundary. A containment test put the staff
    wrist inside checkout_right on 4 frames of 96.

    And the customer is NOT tested here. The obvious design gates the reach on
    a customer standing at that station -- but measured across all three ways
    of asking (pelvis anchor, bbox overlap, the customer's own wrist) the
    customer track does not exist at all on the five frames of the exchange:
    leaning over the counter to hand money across merges them with the
    employee into one detection. Requiring them at that instant asks
    perception for the one thing it cannot give. So the customer becomes
    corroboration at decision time instead, where there has been time to see
    them -- see `step`.
    """
    p = _wrist(k, side, tun.wrist_conf_th)
    if p is None:
        return None
    reach = tun.exchange_reach * torso
    best, best_d = None, None
    for name, poly in obs.checkout_zones.items():
        # signed: positive inside, negative outside by that many pixels
        d = cv2.pointPolygonTest(poly, (float(p[0]), float(p[1])), True)
        if d >= -reach and (best_d is None or d > best_d):
            best, best_d = name, d
    return best


def _reached_drawer(k, obs: Observation, torso: float, conf_th: float,
                    drawer_in: float) -> bool:
    """Did the cash go where it belongs?

    Two ways to say yes, and the till state is the load-bearing one. On
    cash_till_open_3 the drawer opens 6.6 s after the customer leaves, long
    after the wrist has stopped being anywhere near where it will open --
    so a test that only asked "is the wrist at the till box" would miss the
    honest sale it exists to recognise.
    """
    if obs.till_open:
        return True
    if obs.till_box is None:
        return False
    c = np.array([(obs.till_box[0] + obs.till_box[2]) / 2.0,
                  (obs.till_box[1] + obs.till_box[3]) / 2.0])
    for side in ("L", "R"):
        p = _wrist(k, side, conf_th)
        if p is not None and float(np.linalg.norm(p - c)) / torso <= drawer_in:
            return True
    return False


def step(ev: CustomerCashEvent, frame: int, k, torso: float,
         obs: Observation, hands: HandHistory, tun: Tuning) -> CustomerCashEvent:
    """Advance one employee track by one frame.

    `k` is that track's keypoints for THIS frame; callers must not pass the
    pose of a track that was only predicted, or the machine confirms states
    from a position nobody observed.
    """
    targets = pocket_targets(k, torso)

    # Corroboration accumulates for as long as the event is alive, in both
    # directions around the hand-off. The customer is invisible during the
    # hand-off itself -- they merge with the employee -- so the evidence that
    # this was a transaction arrives a beat late, which is fine because
    # nothing is claimed until the concealment confirms.
    if ev.zone is not None and obs.customer_recent(
            ev.zone, frame, tun.customer_recent):
        ev.customer_seen = True

    # A hand is only "arriving" at the pocket if it was somewhere else first.
    # Without this an employee standing with their hands at their sides is
    # permanently in concealment: the resting wrist sits inside pocket_radius
    # of its own hip and the dwell counter never resets. Measured on clip
    # 00018 that is exactly what fired -- the idle left hand confirmed a
    # dwell while the right hand was still at the counter.
    for side in ("L", "R"):
        p = _wrist(k, side, tun.wrist_conf_th)
        if p is None:
            continue
        d = float(np.linalg.norm(p - targets[side])) / torso
        if d > tun.pocket_radius * tun.away_factor:
            ev.was_away[side] = True

    if ev.state == IDLE:
        # The customer opens the window, not the reach. The reach is the more
        # descriptive signal and it is recorded when seen, but it cannot be
        # required: on clip 00018 the only frames where a "staff" wrist was
        # inside the checkout polygon were frames where that wrist actually
        # belonged to the customer.
        for name in obs.checkout_zones:
            if obs.customer_recent(name, frame, tun.customer_recent):
                ev.zone = name
                ev.start = frame
                ev.customer_seen = True
                ev.to(TRANSACTION, frame, "CUSTOMER_AT_STATION")
                break

    elif ev.state == TRANSACTION:
        # Note the reach if it happens, purely as evidence for the reviewer.
        if ev.exchange_frame is None:
            for side in ("L", "R"):
                if _at_exchange(k, side, obs, torso, tun) == ev.zone:
                    ev.inside_run[side] += 1
                    if ev.inside_run[side] >= tun.exchange_confirm:
                        ev.exchange_frame = frame
                        ev.exchange_hand = side
                        ev.reasons.append("HAND_REACHED_TO_CUSTOMER_SIDE")
                else:
                    ev.inside_run[side] = 0

        # The drawer redeems the event at any point, and is checked first: a
        # hand that passes the hip on its way to the till must not alert
        # before the till has been consulted.
        if _reached_drawer(k, obs, torso, tun.wrist_conf_th, tun.drawer_in):
            ev.to(RESOLVED, frame, "RESOLVED_CASH_TO_DRAWER")
            ev.end = frame
            return ev

        confirmed = None
        for side in ("L", "R"):
            idx = WRI_L if side == "L" else WRI_R
            tgt = targets[side]
            if not ev.was_away[side]:
                ev.body_dwell[side] = 0
                continue
            if k[idx, 2] >= tun.wrist_conf_th:
                ev.body_miss[side] = 0
                near = (float(np.linalg.norm(k[idx, :2] - tgt))
                        <= tun.pocket_radius * torso)
                ev.body_dwell[side] = ev.body_dwell[side] + 1 if near else 0
            else:
                # Pose lost inside the target zone supports the conclusion;
                # it never reaches it alone. problem2_fsm learned this the
                # expensive way -- an alert on cash_till_open_2 whose only
                # evidence was the hand vanishing.
                ev.body_miss[side] += 1
                last = hands.last_valid(side)
                if last is not None and float(
                        np.linalg.norm(last.xy - tgt)) <= tun.pocket_radius * torso:
                    ev.body_dwell[side] += 1
                    if ev.body_miss[side] == 1:
                        ev.reasons.append(f"HAND_OCCLUDED_IN_TARGET_ZONE_{side}")
            if ev.body_dwell[side] >= tun.dwell_confirm:
                confirmed = side
                break

        if confirmed:
            if not ev.customer_seen:
                ev.to(ENDED, frame, "NO_CUSTOMER_AT_STATION")
                ev.end = frame
                return ev
            if hands.moved_toward(confirmed, targets[confirmed], tun.traj_lookback):
                ev.reasons.append("HAND_TO_POCKET_TRAJECTORY")
            ev.active_hand = confirmed
            ev.to(CONCEALED, frame, "CONCEALMENT_DWELL_CONFIRMED")
            ev.end = frame
        elif frame - ev.since > tun.decision_window:
            # Neither the till nor the pocket. Nothing is claimed.
            ev.to(ENDED, frame)
            ev.end = frame

    return ev
