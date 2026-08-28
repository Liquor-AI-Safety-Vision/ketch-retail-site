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

    python backend/tools/run_tests_nopytest.py backend/tests/test_fsm_globals_guard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


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

    def __init__(self, camera_id: str, target_fps: float, params: dict | None = None):
        from backend.src.pipelines.counter_cash_theft import (
            CounterCashTheftPipeline, load_private_fsm)

        self.spec = _FakeSpec(camera_id, target_fps)
        self.params = params or {}
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
    import problem2_fsm as shared

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
    from backend.src.pipelines.counter_cash_theft import load_private_fsm

    first, second = load_private_fsm(), load_private_fsm()
    assert first is not second
    # And neither is the shared one.
    import problem2_fsm as shared
    assert first is not shared

    # Not registered in sys.modules: the canonical name keeps meaning the
    # canonical module for every other importer.
    assert sys.modules.get("problem2_fsm") is shared
    assert first.__name__ not in sys.modules
