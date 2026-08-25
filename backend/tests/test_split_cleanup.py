"""Resources allocated before a failure must still be released.

Both the driver and the tier-ladder tool acquire two kinds of resource that
outlive a Python exception: shared-memory rings, which persist until their
owner unlinks them, and tier processes, which keep running until somebody
joins or terminates them. Adding try/finally around the *run* covered the
common case and left two gaps that only open on the failure path:

  partial allocation  the ring loop itself sits above the try. Sixteen
                      cameras is sixteen allocations of ~90MB; the second one
                      failing leaves the first one mapped and never unlinked.

  early failure       the tool starts its processes, then waits. await_message
                      can now raise -- that was the point of adding it -- and
                      when it does, nothing joins or terminates the processes
                      that are already running.

Both are invisible in a passing run and both are exactly the kind of thing
reading the code does not settle, so these drive the real functions with the
failure injected and count what was cleaned up.

    python backend/tools/run_tests_nopytest.py backend/tests/test_split_cleanup.py
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _FakeSpec:
    """Only what the code touches before it reaches the resource it allocates."""

    def __init__(self, camera_id: str):
        self.camera_id = camera_id
        self.camera_type = "counter"
        self.perception_profile: dict = {}
        self.pipeline_bindings: list = []
        self.target_fps = 5.0


class _FakeRing:
    created: list["_FakeRing"] = []

    def __init__(self):
        self.closed = False
        self.spec = object()
        _FakeRing.created.append(self)

    def close(self):
        self.closed = True


class _FakeQueue:
    def __init__(self, *_a, **_k):
        self.items: list = []

    def put(self, item, *_a, **_k):
        self.items.append(item)

    def get(self, *_a, **_k):
        raise AssertionError("the test should never block on a queue")

    def qsize(self):
        return len(self.items)


class _FakeProcess:
    started: list["_FakeProcess"] = []

    def __init__(self, target=None, args=(), kwargs=None, name="fake"):
        self.name = name
        self.did_start = False
        self.did_terminate = False
        self.joins = 0
        self.exitcode = None
        _FakeProcess.started.append(self)

    def start(self):
        self.did_start = True

    def join(self, timeout=None):
        self.joins += 1

    def terminate(self):
        self.did_terminate = True
        self.exitcode = -15

    def is_alive(self):
        return self.did_start and not self.did_terminate


class _FakeCtx:
    Queue = _FakeQueue
    Process = _FakeProcess

    @staticmethod
    def Event():
        return threading.Event()


def _fail_on_nth_ring(n: int):
    """A FrameRing.create that succeeds n-1 times, then raises."""
    calls = {"n": 0}

    def create(*_a, **_k):
        calls["n"] += 1
        if calls["n"] >= n:
            raise MemoryError("no shared memory left")
        return _FakeRing(), _FakeQueue()

    return create


def test_driver_releases_rings_allocated_before_an_allocation_failure(monkeypatch=None):
    from backend.src.app import run_split_pipeline
    from backend.src.runtime import frame_ring

    _FakeRing.created.clear()
    original = frame_ring.FrameRing.create
    frame_ring.FrameRing.create = staticmethod(_fail_on_nth_ring(2))
    try:
        specs = [_FakeSpec("cam_1"), _FakeSpec("cam_2")]
        with pytest.raises(MemoryError):
            run_split_pipeline.run(
                specs, decoders=1, inferers=1, infer_threads=1, businesses=1,
                pose_model="p", reid_model="r", till_model=None, till_imgsz=640,
                face_model="f", arcface_model="a", db_path="d", media_root="m",
                gallery_db_path="g", slots_per_camera=2)
    finally:
        frame_ring.FrameRing.create = original

    created = _FakeRing.created
    assert created, "the first ring should have been allocated before the failure"
    unclosed = [r for r in created if not r.closed]
    assert not unclosed, (
        f"{len(unclosed)} of {len(created)} rings allocated before the failure were never "
        f"closed; a ring nobody unlinks is shared memory held until the machine reboots")


def test_ladder_tool_releases_rings_allocated_before_an_allocation_failure():
    from backend.src.runtime import frame_ring
    from backend.tools import diag_split_tiers

    _FakeRing.created.clear()
    original = frame_ring.FrameRing.create
    frame_ring.FrameRing.create = staticmethod(_fail_on_nth_ring(2))
    try:
        specs = [_FakeSpec("cam_1"), _FakeSpec("cam_2")]
        with pytest.raises(MemoryError):
            diag_split_tiers.run_layer(
                specs, "decode", decoders=1, inferers=1, infer_threads=1, businesses=1,
                models={"pose": "p", "reid": "r", "till": None, "face": "f", "arcface": "a"},
                paths={"db": "d", "media": "m", "gallery": "g"}, slots_per_camera=2)
    finally:
        frame_ring.FrameRing.create = original

    unclosed = [r for r in _FakeRing.created if not r.closed]
    assert not unclosed, f"{len(unclosed)} rings leaked from the ladder tool"


def test_ladder_tool_stops_its_processes_when_waiting_fails():
    """await_message raising is the whole point of it existing; the processes
    it was waiting on must not outlive the failure."""
    import multiprocessing as mp

    from backend.src.runtime import frame_ring, tier_status
    from backend.tools import diag_split_tiers

    _FakeRing.created.clear()
    _FakeProcess.started.clear()

    original_ring = frame_ring.FrameRing.create
    original_ctx = mp.get_context
    original_await = tier_status.await_message

    frame_ring.FrameRing.create = staticmethod(lambda *_a, **_k: (_FakeRing(), _FakeQueue()))
    mp.get_context = lambda *_a, **_k: _FakeCtx
    tier_status.await_message = lambda *_a, **_k: (_ for _ in ()).throw(
        tier_status.TierProcessError("a tier died during load"))
    try:
        specs = [_FakeSpec("cam_1"), _FakeSpec("cam_2")]
        with pytest.raises(tier_status.TierProcessError):
            diag_split_tiers.run_layer(
                specs, "decode", decoders=2, inferers=1, infer_threads=1, businesses=1,
                models={"pose": "p", "reid": "r", "till": None, "face": "f", "arcface": "a"},
                paths={"db": "d", "media": "m", "gallery": "g"}, slots_per_camera=2)
    finally:
        frame_ring.FrameRing.create = original_ring
        mp.get_context = original_ctx
        tier_status.await_message = original_await

    started = [p for p in _FakeProcess.started if p.did_start]
    assert started, "the test did not get far enough to start any process"
    alive = [p for p in started if p.is_alive()]
    assert not alive, (
        f"{len(alive)} of {len(started)} processes were left running after the wait failed; "
        f"each one holds a ring mapping open, so the owner's unlink frees nothing")
    assert not [r for r in _FakeRing.created if not r.closed], "rings leaked too"
