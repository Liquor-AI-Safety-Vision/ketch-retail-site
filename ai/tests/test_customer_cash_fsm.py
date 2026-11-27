"""The take-from-customer machine, one decision per test.

The behaviour worth protecting is not "does it alert" -- it is that the same
opening move, hand across the counter and back, ends in an alert or in
silence depending only on where the cash went afterwards. A change that
alerts on both is useless in a shop; a change that alerts on neither is
worse, because it looks like it is working.

    python ai/tools/run_tests_nopytest.py ai/tests/test_customer_cash_fsm.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.src.perception.customer_cash_fsm import (  # noqa: E402
    CONCEALED, ENDED, IDLE, RESOLVED, TRANSACTION,
    CustomerCashEvent, HandHistory, Observation, Sample, Tuning, step,
)
from ai.src.perception.cash_concealment_fsm import (  # noqa: E402
    HIP_L, HIP_R, SHO_L, SHO_R, WRI_L, WRI_R,
)

FPS = 10.0
TUN = Tuning()

# One checkout polygon, far from the body, standing in for the counter the
# employee reaches across.
ZONE = np.array([[200, 0], [300, 0], [300, 100], [200, 100]], np.float32)
ZONES = {"checkout_right": ZONE}


#: Neither in the checkout polygon nor within pocket_radius of either hip.
#: Worth stating explicitly: the obvious "next to the body" choice of (0, 100)
#: sits 25 px from the right pocket target, inside the 30 px radius, so it
#: reads as a concealment and every silence test passes for the wrong reason.
NEUTRAL = (120, 40)


def pose(wri_l=NEUTRAL, wri_r=NEUTRAL, conf=0.9):
    """A person 100 px tall in torso, hips at y=100, shoulders at y=0.

    torso_len is 100, so a pocket_radius of 0.30 is 30 px and the pocket
    target sits 15 px below the hip.
    """
    k = np.zeros((17, 3), np.float32)
    k[SHO_L] = (-20, 0, 0.9)
    k[SHO_R] = (20, 0, 0.9)
    k[HIP_L] = (-20, 100, 0.9)
    k[HIP_R] = (20, 100, 0.9)
    k[WRI_L] = (wri_l[0], wri_l[1], conf)
    k[WRI_R] = (wri_r[0], wri_r[1], conf)
    return k


def pocket(side):
    """Exactly the target pocket_targets computes, so dwell is unambiguous."""
    hip = np.array([-20.0, 100.0]) if side == "L" else np.array([20.0, 100.0])
    sho = np.array([-20.0, 0.0]) if side == "L" else np.array([20.0, 0.0])
    down = hip - sho
    down = down / np.linalg.norm(down)
    return hip + down * (0.15 * 100.0)


IN_ZONE = (250, 50)      # inside the checkout polygon
AT_SIDE = NEUTRAL        # withdrawn, and demonstrably not at either pocket


def run(frames, obs_for, tun=TUN, hands=None):
    """Feed a list of poses, returning the event and its state trace."""
    ev = CustomerCashEvent(track_id=1, frame=0)
    hands = hands or HandHistory(tun.traj_window)
    trace = []
    for i, k in enumerate(frames):
        for side, idx in (("L", WRI_L), ("R", WRI_R)):
            hands.push(side, Sample(i, k[idx, 0], k[idx, 1], k[idx, 2],
                                    tun.wrist_conf_th))
        step(ev, i, k, 100.0, obs_for(i), hands, tun)
        trace.append(ev.state)
    return ev, trace


def customer_present(seen_at=0, **kw):
    """A customer was last seen at the right-hand station on frame `seen_at`.

    Recorded as a timestamp rather than a flag because the FSM reads it as a
    trailing window: measured on clip 00018 the customer track does not exist
    at all during the five frames of the hand-off -- leaning across the
    counter merges them with the employee -- so the evidence always arrives
    either side of the reach, never on it.
    """
    return Observation(checkout_zones=ZONES,
                       customer_last_seen={"checkout_right": seen_at}, **kw)


def no_customer(**kw):
    return Observation(checkout_zones=ZONES, customer_last_seen={}, **kw)


# --------------------------------------------------------------- the trigger

def test_a_customer_at_the_station_opens_the_window():
    """The customer opens it, not the reach.

    The reach would be the more descriptive trigger and it is what the first
    draft used. It had to go: measured on clip 00018 the only frames with a
    "staff" wrist inside the checkout polygon were frames where the pose
    model had handed the employee's track the CUSTOMER's forearm, while the
    employee's real hand was already at their pocket. A trigger that fires on
    a mis-assigned keypoint is not a trigger."""
    ev, _ = run([pose()], lambda i: customer_present())
    assert ev.state == TRANSACTION
    assert ev.zone == "checkout_right"
    assert ev.customer_seen is True


def test_the_reach_is_recorded_when_it_is_seen():
    """Still worth capturing for the reviewer, just not depended on."""
    frames = [pose(wri_r=IN_ZONE) for _ in range(TUN.exchange_confirm + 2)]
    ev, _ = run(frames, lambda i: customer_present())
    assert ev.exchange_frame is not None
    assert "HAND_REACHED_TO_CUSTOMER_SIDE" in ev.reasons


def test_reaching_across_an_empty_counter_raises_no_alert():
    """No customer at the station means there was no money to take.

    The reach itself is allowed to open the event -- perception cannot tell
    at that instant whether anyone is there, so refusing to start would lose
    the real ones too. What must not happen is an ALERT, and the customer is
    what gates that, at the end where the evidence has had time to arrive."""
    frames = (_through_withdrawal()
              + [pose(wri_r=tuple(pocket("R"))) for _ in range(TUN.dwell_confirm * 3)])
    ev, _ = run(frames, lambda i: no_customer())
    assert ev.state == IDLE
    assert ev.alerted is False


def test_a_brief_touch_of_the_zone_is_not_recorded_as_a_reach():
    """One frame in the polygon is a wrist clipping its edge, not a reach."""
    frames = [pose(wri_r=IN_ZONE)] + [pose(wri_r=AT_SIDE) for _ in range(5)]
    ev, _ = run(frames, lambda i: customer_present())
    assert ev.exchange_frame is None


# ------------------------------------------------------------ the two endings

def _through_withdrawal():
    """Frames that reach HAND_WITHDRAWN, shared by the ending tests."""
    return ([pose(wri_r=IN_ZONE) for _ in range(TUN.exchange_confirm)]
            + [pose(wri_r=AT_SIDE) for _ in range(TUN.withdraw_confirm)])


def test_a_customer_seen_only_after_the_handoff_still_corroborates():
    """The measured case. On clip 00018 the customer track appears on the
    frame the hand leaves the counter, not before -- so an event that
    demanded them at the reach would never alert on real footage."""
    reach = TUN.exchange_confirm
    frames = (_through_withdrawal()
              + [pose(wri_r=tuple(pocket("R"))) for _ in range(TUN.dwell_confirm)])
    # nothing until after the reach has been confirmed
    ev, _ = run(frames, lambda i: (no_customer() if i < reach
                                   else customer_present(seen_at=i)))
    assert ev.state == CONCEALED
    assert ev.customer_seen is True


def test_hand_to_pocket_after_the_exchange_alerts():
    frames = _through_withdrawal() + [
        pose(wri_r=tuple(pocket("R"))) for _ in range(TUN.dwell_confirm)]
    ev, _ = run(frames, lambda i: customer_present())
    assert ev.state == CONCEALED
    assert ev.active_hand == "R"
    assert "CONCEALMENT_DWELL_CONFIRMED" in ev.reasons


def test_cash_reaching_the_drawer_resolves_without_an_alert():
    """The same opening move, ended honestly. This is the test that keeps the
    machine usable: every real sale looks exactly like the one above until
    the till is consulted."""
    frames = _through_withdrawal() + [pose(wri_r=AT_SIDE) for _ in range(5)]

    def obs(i):
        # drawer opens once the hand is back on the employee's side
        return customer_present(till_open=i >= len(_through_withdrawal()))

    ev, _ = run(frames, obs)
    assert ev.state == RESOLVED
    assert ev.alerted is False
    assert "RESOLVED_CASH_TO_DRAWER" in ev.reasons


def test_the_drawer_is_consulted_before_the_pocket():
    """A hand at the hip on the same frame the till reads Open must not
    alert -- passing the hip on the way to the drawer is a normal path."""
    frames = _through_withdrawal() + [
        pose(wri_r=tuple(pocket("R"))) for _ in range(TUN.dwell_confirm * 3)]
    ev, _ = run(frames, lambda i: customer_present(till_open=True))
    assert ev.state == RESOLVED


def test_a_slow_but_honest_sale_still_resolves():
    """Measured on cash_till_open_3: the drawer opened 6.6 s after the
    customer left. At 10 fps that is 66 frames, well inside the default
    window, and the event must still be alive to see it."""
    gap = 66
    frames = (_through_withdrawal()
              + [pose(wri_r=AT_SIDE) for _ in range(gap)]
              + [pose(wri_r=AT_SIDE)])
    opens_at = len(_through_withdrawal()) + gap
    ev, _ = run(frames, lambda i: customer_present(till_open=i >= opens_at))
    assert ev.state == RESOLVED


def test_neither_till_nor_pocket_closes_quietly():
    frames = _through_withdrawal() + [
        pose(wri_r=AT_SIDE) for _ in range(TUN.decision_window + 2)]
    ev, _ = run(frames, lambda i: customer_present())
    assert ev.state == ENDED
    assert ev.alerted is False


# ------------------------------------------------------- both hands, in turn

def test_receiving_with_one_hand_and_pocketing_with_the_other_alerts():
    """The variant the v2 clips were generated for: take with the left, move
    the cash across, pocket with the right. Committing the event to the hand
    that reached would lose it at the handover."""
    frames = ([pose(wri_l=IN_ZONE) for _ in range(TUN.exchange_confirm)]
              + [pose(wri_l=AT_SIDE) for _ in range(TUN.withdraw_confirm)]
              + [pose(wri_r=tuple(pocket("R"))) for _ in range(TUN.dwell_confirm)])
    ev, _ = run(frames, lambda i: customer_present())
    assert ev.state == CONCEALED
    assert ev.active_hand == "R"


def test_a_hand_lost_at_the_pocket_still_counts_but_is_labelled():
    """Occlusion supports the dwell; the reason code has to say so, because a
    reviewer reading the alert needs to know the hand was inferred."""
    seen = [pose(wri_r=tuple(pocket("R")))]
    hidden = [pose(wri_r=tuple(pocket("R")), conf=0.0)
              for _ in range(TUN.dwell_confirm)]
    ev, _ = run(_through_withdrawal() + seen + hidden,
                lambda i: customer_present())
    assert ev.state == CONCEALED
    assert any(r.startswith("HAND_OCCLUDED_IN_TARGET_ZONE") for r in ev.reasons)


def test_occlusion_alone_without_ever_seeing_the_hand_does_not_alert():
    """No valid sample at the target ever means there is nothing to support.
    This is the false positive cash_concealment_fsm shipped and had to take back."""
    hidden = [pose(conf=0.0) for _ in range(TUN.decision_window + 2)]
    ev, _ = run(_through_withdrawal() + hidden, lambda i: customer_present())
    assert ev.state == ENDED


# ------------------------------------------------------------------- tuning

def test_seconds_convert_at_the_cameras_frame_rate():
    a = Tuning.from_seconds(10.0, dwell_confirm_s=0.5)
    b = Tuning.from_seconds(24.0, dwell_confirm_s=0.5)
    assert a.dwell_confirm == 5
    assert b.dwell_confirm == 12


def test_two_tunings_are_independent_objects():
    """The bug cash_concealment_fsm had: one camera's settings reaching another. A
    frozen value object cannot express it, and this asserts that stays true."""
    a = Tuning.from_seconds(10.0, dwell_confirm_s=0.5)
    b = Tuning.from_seconds(24.0, dwell_confirm_s=2.0)
    assert a.dwell_confirm == 5 and b.dwell_confirm == 48
