"""Two cameras must not silently retune each other's concealment FSM.

problem2_fsm keeps its tuning in module globals. One camera per process, that
is fine. The split business tier runs several cameras as threads in one
process, which makes the second camera to bootstrap overwrite the first one's
thresholds -- and from then on one camera's theft alerts are decided using
another camera's settings, with nothing in the log to say so.

Every counter in the current manifest happens to share identical parameters,
so all of them write the same values and nothing goes wrong. That is why this
has never been seen, and it is also why it needs a test: the failure appears
the first time a store runs two counters at different frame rates, which is a
configuration change, not a code change, and nobody will be looking at this
file when it happens.

The real fix is for the FSM to take its tuning as an argument. Until then the
requirement is narrower but testable: same settings must keep working, and
different settings must fail loudly instead of quietly.

    python backend/tools/run_tests_nopytest.py backend/tests/test_fsm_globals_guard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
    method reads self.spec and self.params and writes module globals.
    """

    def __init__(self, camera_id: str, target_fps: float, params: dict | None = None):
        from backend.src.pipelines.counter_cash_theft import CounterCashTheftPipeline

        self.spec = _FakeSpec(camera_id, target_fps)
        self.params = params or {}
        self._configure = CounterCashTheftPipeline._configure_fsm_globals.__get__(self)

    def configure(self):
        self._configure()


def _reset():
    from backend.src.pipelines import counter_cash_theft as ct

    ct._FSM_GLOBALS_OWNER.clear()


def test_two_cameras_with_identical_settings_both_configure_fine():
    """The normal case, and the one the current manifest exercises."""
    _reset()
    _FakePipeline("counter_1", 5.0).configure()
    _FakePipeline("counter_2", 5.0).configure()  # must not raise

    import problem2_fsm as F

    assert F.FPS == 5.0


def test_same_camera_reconfiguring_is_allowed():
    _reset()
    cam = _FakePipeline("counter_1", 5.0)
    cam.configure()
    cam.configure()


def test_a_second_camera_needing_different_tuning_is_refused():
    _reset()
    _FakePipeline("counter_1", 5.0).configure()
    with pytest.raises(RuntimeError) as excinfo:
        _FakePipeline("counter_2", 10.0).configure()

    message = str(excinfo.value)
    # The message has to name both cameras and what actually differs, or the
    # operator is left with "something about the FSM" and no next step.
    assert "counter_2" in message and "counter_1" in message
    assert "FPS" in message


def test_a_differing_boolean_parameter_is_also_refused():
    """any_hand changes which hand gates the alert -- a silent flip of it
    across cameras is exactly the corruption this guards."""
    _reset()
    _FakePipeline("counter_1", 5.0, {"any_hand": False}).configure()
    with pytest.raises(RuntimeError) as excinfo:
        _FakePipeline("counter_2", 5.0, {"any_hand": True}).configure()
    assert "ANY_HAND" in str(excinfo.value)


def test_the_globals_actually_get_written():
    """The guard must not have replaced the thing it guards."""
    _reset()
    _FakePipeline("counter_1", 10.0, {"wrist_conf_th": 0.42}).configure()

    import problem2_fsm as F

    assert F.FPS == 10.0
    assert F.WRIST_CONF_TH == 0.42
    assert F.HAND_INSIDE_CONFIRM == 3  # scale(3) at 10 fps is 3
