"""Two cameras must not retune each other's concealment FSM.

problem2_fsm keeps its tuning in module globals -- FPS, the confirm counts,
ANY_HAND, WRIST_CONF_TH -- and reads them from inside step(), the function
that decides whether a concealment becomes a theft alert. One camera per
process, that is fine. The split business tier runs several cameras as
threads in one process, so a shared module meant the second camera to
bootstrap overwrote the first one's thresholds, and from then on one camera's
theft alerts were decided using another camera's settings, with nothing in
the log to say so.

Every counter in the current manifest happens to share identical parameters,
so all of them wrote the same values and nothing went wrong. That is why this
was never seen in practice, and why it needed a test: the failure appears the
first time a store runs two counters at different frame rates, which is a
configuration change, not a code change, and nobody will be reading this file
when it happens.

This file used to assert the narrow requirement -- same settings keep
working, different settings fail loudly -- because the fix was considered too
large to make. It now asserts the real one: each pipeline gets its own copy of
the FSM module (load_private_fsm), so different settings simply work and
neither camera can reach the other's thresholds.

    python ai/tools/run_tests_nopytest.py ai/tests/test_fsm_globals_guard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _FakeSpec:
    def __init__(self, camera_id: str, target_fps: float):
        self.camera_id = camera_id
        self.target_fps = target_fps


class _FakePipeline:
    """Borrows the real _configure_fsm_globals, without the rest of the class.

    Building a real CounterCashTheftPipeline would need a manifest, an alert
    store and a media store, none of which this behaviour depends on -- the
    method reads self.spec and self.params and writes into self.F.
    """

    def __init__(self, camera_id: str, target_fps: float, tuning: dict | None = None):
        from ai.src.pipelines.counter_cash_theft import (
            CounterCashTheftPipeline, load_private_fsm)

        self.spec = _FakeSpec(camera_id, target_fps)
        # FSM tuning is a `fsm` block inside the binding's params. Passed as
        # bare params here once, which meant every override in this file was
        # silently ignored -- two of these tests then failed outright and two
        # passed vacuously, comparing defaults against defaults.
        self.params = {"fsm": dict(tuning)} if tuning else {}
        self.F = load_private_fsm()
        self._configure = CounterCashTheftPipeline._configure_fsm_globals.__get__(self)

    def configure(self):
        self._configure()
        return self


def test_two_cameras_at_different_frame_rates_keep_their_own_tuning():
    """The failure this whole file exists for, now a supported configuration.

    Before the per-camera module, the second configure() either overwrote the
    first camera's FPS or (with the interim guard) refused to run at all.
    """
    a = _FakePipeline("counter_1", 5.0).configure()
    b = _FakePipeline("counter_2", 10.0).configure()

    assert a.F.FPS == 5.0
    assert b.F.FPS == 10.0
    # The frame-scaled counts have to follow the frame rate independently,
    # not just the FPS value itself.
    assert a.F.STATE_TIMEOUT != b.F.STATE_TIMEOUT


def test_a_differing_boolean_parameter_stays_per_camera():
    """any_hand changes which hand gates the alert; a silent flip across
    cameras is exactly the corruption this file was written about."""
    a = _FakePipeline("counter_1", 5.0, {"any_hand": False}).configure()
    b = _FakePipeline("counter_2", 5.0, {"any_hand": True}).configure()

    assert a.F.ANY_HAND is False
    assert b.F.ANY_HAND is True


def test_configuring_the_second_camera_does_not_move_the_first():
    """Order matters: the damage used to happen on the second write."""
    a = _FakePipeline("counter_1", 5.0, {"wrist_conf_th": 0.42}).configure()
    before = (a.F.FPS, a.F.WRIST_CONF_TH, a.F.HAND_INSIDE_CONFIRM)

    _FakePipeline("counter_2", 10.0, {"wrist_conf_th": 0.11}).configure()

    assert (a.F.FPS, a.F.WRIST_CONF_TH, a.F.HAND_INSIDE_CONFIRM) == before


def test_the_shared_module_is_left_alone():
    """Nothing may write into the canonical problem2_fsm any more.

    Other callers -- the standalone scripts the FSM was written for -- still
    import it normally, and a pipeline reaching into it would retune them.
    """
    from ai.src.perception import problem2_fsm as shared

    baseline = (shared.FPS, shared.ANY_HAND, shared.WRIST_CONF_TH)
    _FakePipeline("counter_1", 7.0, {"any_hand": True, "wrist_conf_th": 0.9}).configure()

    assert (shared.FPS, shared.ANY_HAND, shared.WRIST_CONF_TH) == baseline


def test_the_globals_actually_get_written():
    """The isolation must not have quietly replaced the thing it isolates."""
    cam = _FakePipeline("counter_1", 10.0, {"wrist_conf_th": 0.42}).configure()

    assert cam.F.FPS == 10.0
    assert cam.F.WRIST_CONF_TH == 0.42
    assert cam.F.HAND_INSIDE_CONFIRM == 3  # scale(3) at 10 fps is 3


def test_each_pipeline_gets_a_distinct_module_object():
    """The mechanism itself, asserted directly rather than only through its
    effects -- if load_private_fsm ever starts returning a cached module, the
    tests above would still pass for identical settings and fail only for the
    configuration nobody runs yet."""
    from ai.src.pipelines.counter_cash_theft import load_private_fsm

    first, second = load_private_fsm(), load_private_fsm()
    assert first is not second
    # And neither is the shared one.
    from ai.src.perception import problem2_fsm as shared
    assert first is not shared

    # Not registered in sys.modules: the canonical name keeps meaning the
    # canonical module for every other importer.
    assert sys.modules.get("ai.src.perception.problem2_fsm") is shared
    assert first.__name__ not in sys.modules


def test_the_geometry_thresholds_are_reachable_from_a_manifest():
    """DRAWER_IN and its six neighbours used to have no route from config.

    They are in units of torso length, so they survive distance from the
    camera -- but not angle. A till filmed from 3 m at a slant does not put
    the wrist at the same normalised distance from the drawer centre as one
    filmed level from 2 m, so one number cannot be right for both, and there
    was no way to say so in a manifest.
    """
    tuned = _FakePipeline("counter_1", 5.0, {
        "drawer_in": 0.60, "drawer_out": 0.75, "deep_in": 0.25,
        "withdraw_delta": 0.30, "pocket_radius": 0.35,
        "hand_follow_gate": 0.50, "drawer_roi_expand": 0.40,
    }).configure()
    plain = _FakePipeline("counter_2", 5.0).configure()

    assert (tuned.F.DRAWER_IN, tuned.F.DRAWER_OUT) == (0.60, 0.75)
    assert (tuned.F.DEEP_IN, tuned.F.WITHDRAW_DELTA) == (0.25, 0.30)
    assert (tuned.F.POCKET_RADIUS, tuned.F.HAND_FOLLOW_GATE) == (0.35, 0.50)
    assert tuned.F.DRAWER_ROI_EXPAND == 0.40
    assert plain.F.DRAWER_IN == 0.80, "the untuned camera moved with the tuned one"


def test_an_inverted_hysteresis_pair_is_refused():
    """drawer_in >= drawer_out does not fail -- it oscillates.

    Entry is at or under DRAWER_IN and exit at or over DRAWER_OUT; the gap is
    what stops a wrist resting on the boundary from flipping state every
    frame. With no gap the FSM emits a stream of entries and exits that reads
    as a very busy employee, and nothing anywhere reports a problem.
    """
    try:
        _FakePipeline("counter_1", 5.0, {"drawer_in": 0.95}).configure()
    except ValueError as e:
        assert "drawer_in" in str(e) and "counter_1" in str(e)
    else:
        raise AssertionError("an inverted hysteresis pair was accepted")


def test_timing_overrides_are_seconds_and_defaults_are_untouched():
    """The manifest says seconds; the appendix's numbers are frames at 10 fps.

    The default path still computes frames_10fps * fps / 10 exactly as it
    always did, so every recorded benchmark still describes this code. Only
    an override takes the seconds path.
    """
    for fps in (3.0, 5.0, 10.0, 15.0):
        cam = _FakePipeline("c", fps).configure()
        scale = lambda n: max(1, int(round(n * fps / 10.0)))
        assert cam.F.HAND_INSIDE_CONFIRM == scale(3)
        assert cam.F.STATE_TIMEOUT == scale(50)
        assert cam.F.TRAJ_WINDOW == scale(25)

    tuned = _FakePipeline("c", 5.0, {"dwell_confirm_s": 0.8, "state_timeout_s": 2.0}).configure()
    assert tuned.F.DWELL_CONFIRM == 4, "0.8 s at 5 fps is 4 frames"
    assert tuned.F.STATE_TIMEOUT == 10, "2.0 s at 5 fps is 10 frames"


def test_every_tunable_the_schema_advertises_actually_reaches_the_fsm():
    """The two tables and the FSM module must not drift apart.

    manifest_schema builds the `fsm` section from fsm_tuning, so a name in
    that table is a name a manifest may write. If the FSM has no such global,
    the manifest validates and the setting evaporates -- which is the whole
    class of bug the schema was introduced to end.
    """
    from ai.src.perception import problem2_fsm
    from ai.src.pipelines import fsm_tuning

    missing = []
    for const in ([c for c, _ in fsm_tuning.TIMING_FRAMES_AT_10FPS.values()]
                  + list(fsm_tuning.GEOMETRY.values())
                  + list(fsm_tuning.FLAGS.values())):
        if not hasattr(problem2_fsm, const):
            missing.append(const)
    assert not missing, f"fsm_tuning names globals problem2_fsm does not have: {missing}"
