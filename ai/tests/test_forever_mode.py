"""strategy=forever: a run that ends when it is told to, not when a clip does.

What has to hold for a replayed clip to stand in for a camera indefinitely:

  * the file source loops past the end of the clip with no duration set, and
    a clip that yields nothing refuses to spin instead of looping on EOF
  * a finite strategy still requires a duration, so a forgotten one is an
    error rather than a benchmark that never finishes
  * the decoder stops on stop_evt and returns normally, so the ordinary
    ordered shutdown -- including the outbox flush -- runs after it
  * the business tier flushes its outbox before reporting stats, since the
    parent starts its terminate countdown on those stats
  * the first SIGINT/SIGTERM asks for that shutdown; the second forces it

    python ai/tools/run_tests_nopytest.py ai/tests/test_forever_mode.py
"""
from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _clip(tmp_path, frames=12, fps=10.0) -> Path:
    import cv2

    path = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 48))
    assert writer.isOpened(), "mp4v unavailable in this OpenCV build"
    for i in range(frames):
        writer.write(np.full((48, 64, 3), i * 10 % 255, np.uint8))
    writer.release()
    return path


def _spec(path, **overrides):
    from ai.src.domain.models import CameraStreamSpec

    base = dict(
        company_id="test_co", store_id="store_A", store_display_name="Store A",
        store_timezone="Asia/Ho_Chi_Minh", camera_id="counter_1",
        display_name="Counter 1", camera_type="counter", source_kind="video_file",
        source_uri=str(path), source_path=str(path), strategy="forever", start_s=0.0,
        target_duration_s=0.0, target_fps=5.0, runtime_profile={},
        perception_profile={}, simulation_profile={}, pipeline_bindings=[],
        raw_config={},
    )
    base.update(overrides)
    return CameraStreamSpec(**base)


def test_a_file_on_strategy_forever_loops_past_its_end_with_no_duration(tmp_path):
    from ai.src.runtime.virtual_video_stream import VirtualVideoStreamSource

    # 12 frames at 10 fps read at 5 fps: 6 frames per pass.
    source = VirtualVideoStreamSource(_spec(_clip(tmp_path)))
    frames = []
    for frame, _timing in source.iter_timed_frames(pace_realtime=False):
        frames.append(frame)
        if len(frames) == 20:
            break

    assert [f.frame_idx for f in frames] == list(range(20))
    assert frames[-1].loop_index >= 3
    assert source.metrics["reopen_count"] >= 3


def test_a_finite_strategy_still_requires_a_duration(tmp_path):
    from ai.src.runtime.virtual_video_stream import VirtualVideoStreamSource

    with pytest.raises(ValueError) as excinfo:
        VirtualVideoStreamSource(_spec(_clip(tmp_path), strategy="loop_to_duration"))
    assert "target_duration_s" in str(excinfo.value)


def test_a_clip_that_yields_nothing_is_refused_instead_of_looped_on(tmp_path):
    """start_s past the end used to reopen-and-EOF in a tight loop forever."""
    from ai.src.runtime.virtual_video_stream import VirtualVideoStreamSource

    source = VirtualVideoStreamSource(_spec(_clip(tmp_path), start_s=60.0))
    with pytest.raises(RuntimeError) as excinfo:
        next(source.iter_timed_frames(pace_realtime=False))
    assert "without emitting a frame" in str(excinfo.value)


def test_a_live_camera_on_strategy_forever_needs_no_duration():
    from ai.src.domain.models import StreamSourceInfo
    from ai.src.runtime import rtsp_stream

    uri = "rtsp://cam.local/stream"
    real_probe = rtsp_stream.RtspStreamSource._probe

    def fake_probe(self):
        self.native_fps_source = "reported"
        return StreamSourceInfo(path=uri, native_fps=15.0, frame_count=0,
                                duration_s=0.0, width=1920, height=1080)

    rtsp_stream.RtspStreamSource._probe = fake_probe
    try:
        source = rtsp_stream.RtspStreamSource(
            _spec(uri, source_kind="rtsp", strategy="forever"))
        assert source.plan.endless
        with pytest.raises(ValueError):
            rtsp_stream.RtspStreamSource(_spec(uri, source_kind="rtsp", strategy="trim"))
    finally:
        rtsp_stream.RtspStreamSource._probe = real_probe


class _EndlessSource:
    """Stands in for any source on strategy=forever."""

    class plan:
        endless = True

    def __init__(self):
        self.paced = None
        self.closed = False

    def iter_timed_frames(self, pace_realtime=True):
        from ai.src.domain.models import FrameEnvelope
        from ai.src.runtime.virtual_video_stream import utc_now

        self.paced = pace_realtime
        idx = 0
        try:
            while True:
                yield FrameEnvelope(
                    company_id="c", store_id="s", camera_id="counter_1",
                    camera_type="counter", frame_idx=idx, ts_utc=utc_now(),
                    simulated_local_ts=None, source_kind="video_file", source_uri="x",
                    source_path="x", image_bgr=np.zeros((48, 64, 3), np.uint8),
                    source_frame_idx=idx, source_time_s=0.0, loop_index=0,
                ), {"source_grab_s": 0.0, "source_retrieve_s": 0.0}
                idx += 1
        finally:
            self.closed = True


class _Ring:
    class spec:
        frame_shape = (1080, 1920, 3)

    def frame_slot(self, slot, shape):
        return np.empty(shape, np.uint8)

    def blob_slot(self, slot):
        return np.empty((3, 64, 64), np.float32)


def test_the_decoder_stops_on_stop_evt_and_returns_its_stats():
    from ai.src.runtime import decode_tier, rtsp_stream

    source = _EndlessSource()
    stop_evt = threading.Event()
    free_q: queue.Queue = queue.Queue()
    for slot in range(8):
        free_q.put(slot)

    class _DecodeQ:
        def __init__(self):
            self.n = 0

        def put(self, msg):
            free_q.put(msg.slot)  # what the business tier would do
            self.n += 1
            if self.n == 5:
                stop_evt.set()  # the parent's signal handler, mid-run

    real_open = rtsp_stream.open_stream_source
    rtsp_stream.open_stream_source = lambda spec: source
    try:
        stats = decode_tier._run_camera(
            _spec("x"), decode_tier.CameraDecodeConfig(conf=0.35), _Ring(), free_q,
            _DecodeQ(), 64, np.float32, stop_evt)
    finally:
        rtsp_stream.open_stream_source = real_open

    assert stats.frames == 5
    assert source.closed, "the capture must be released now, not at GC"
    # A looping file stands in for a camera, so it is read at camera pace.
    assert source.paced is True


def test_business_flushes_its_outbox_before_reporting_stats():
    """The parent starts its terminate countdown when business stats arrive.
    Flushing after reporting raced that countdown with the R2 uploads."""
    from ai.src.runtime import business_tier

    order: list[str] = []

    class _Outbox:
        def __init__(self, _factory):
            self.closed = False

        def start_run(self, *a, **k):
            return "run"

        def finish_run(self, *a, **k):
            pass

        def close(self):
            if not self.closed:
                order.append("outbox closed")
            self.closed = True

    class _StatsQ:
        def put(self, stats):
            order.append("stats reported")

    class _Queue:
        def put(self, _item):
            pass

    class _FakeRing:
        def __init__(self, _spec):
            pass

        def close(self):
            pass

    business_q: queue.Queue = queue.Queue()
    business_q.put(None)  # perception already over
    spec = _spec("x")

    real_ring = business_tier.FrameRing
    business_tier.FrameRing = _FakeRing
    try:
        from concurrent.futures import ThreadPoolExecutor

        business_tier._run_business_process(
            [spec], {spec.camera_id: None}, {spec.camera_id: queue.Queue()},
            {spec.camera_id: business_q}, "unused.sqlite", "unused_media",
            None, _Queue(), _Go(), _StatsQ(),
            lambda n: None, ThreadPoolExecutor, lambda root: object(), _Outbox)
    finally:
        business_tier.FrameRing = real_ring

    assert order == ["outbox closed", "stats reported"]


class _Go:
    def wait(self):
        return True


def test_first_stop_signal_drains_and_the_second_forces():
    import signal

    from ai.src.app import run_split_pipeline

    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    stop_evt = threading.Event()
    try:
        run_split_pipeline._stop_on_signal(stop_evt)
        handler = signal.getsignal(signal.SIGTERM)

        handler(signal.SIGTERM, None)
        assert stop_evt.is_set()

        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGTERM, None)
    finally:
        for sig, h in saved.items():
            signal.signal(sig, h)
