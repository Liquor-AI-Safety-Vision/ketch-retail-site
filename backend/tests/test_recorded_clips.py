"""Evidence clips: browser-playable H.264, and cut from the recording at native fps.

No recording server here: the fetch is a stub. That MediaMTX serves the range
it is asked for is checked by running it, not asserted in a unit test.

    python backend/tools/run_tests_nopytest.py backend/tests/test_recorded_clips.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _frame(idx, ts):
    return SimpleNamespace(frame_idx=idx, ts_utc=ts, image_bgr=np.zeros((48, 64, 3), np.uint8))


def test_a_recorded_camera_buffers_timestamps_not_pixels():
    from backend.src.pipelines.alert_clip_buffer import AlertClipBuffer

    t0 = datetime(2026, 9, 17, tzinfo=timezone.utc)
    buf = AlertClipBuffer(5.0, pre_seconds=1, post_seconds=1, keep_images=False)
    for i in range(10):
        buf.push(_frame(i, t0 + timedelta(seconds=i / 5)))
    buf.open_clip("a1", event_frame_idx=9)
    ready = []
    for i in range(10, 16):
        buf.push(_frame(i, t0 + timedelta(seconds=i / 5)))
        ready += buf.collect_ready(i)
    assert len(ready) == 1
    clip = ready[0]
    assert all(f.image_bgr is None for f in clip.frames)
    assert clip.started_at == t0 + timedelta(seconds=4 / 5)
    assert clip.ended_at == t0 + timedelta(seconds=14 / 5)


def test_a_clip_is_fetched_after_its_range_is_recorded_then_recorded_against_its_alert(tmp_path):
    from backend.src.infra.local_media_store import LocalMediaStore
    from backend.src.infra.recorded_clips import ClipWorker, RecordingClipMediaStore
    from backend.src.pipelines.alert_clip_buffer import FinalizedAlertClip, persist_clip

    transcoded, rows = [], []

    def transcode(src, dst):
        transcoded.append(src.read_bytes())
        dst.write_bytes(b"h264")

    worker = ClipWorker(settle_s=0.2, retry_s=5.0, transcode=transcode)
    store = RecordingClipMediaStore(LocalMediaStore(tmp_path), worker,
                                    playback_url="http://127.0.0.1:9996", path="st01_storage")
    requested = []

    def fake_fetch(playback_url, path, start, duration_s, dst, timeout_s=60.0):
        requested.append((path, start, round(duration_s, 1), time.time()))
        dst.write_bytes(b"native-fps-recording")

    import backend.src.infra.recorded_clips as rc
    real = rc.fetch_recording
    rc.fetch_recording = fake_fetch
    try:
        now = datetime.now(timezone.utc)
        clip = FinalizedAlertClip("alert-1", frames=[], started_at=now - timedelta(seconds=30),
                                  ended_at=now, metadata={"camera_id": "st01_storage"})
        outbox = SimpleNamespace(add_alert_media=lambda **kw: rows.append(kw))
        persist_clip(store, outbox, clip, fps=3.0)
        assert rows == []          # nothing is recorded before the file exists
        worker.close(timeout_s=10)
    finally:
        rc.fetch_recording = real

    assert requested[0][:3] == ("st01_storage", now - timedelta(seconds=30), 30.0)
    assert requested[0][3] >= now.timestamp() + 0.2    # waited for the tail to be written
    assert transcoded == [b"native-fps-recording"]
    assert len(rows) == 1 and rows[0]["kind"] == "clip"
    assert rows[0]["path"].endswith("alert-1/evidence.mp4")
    assert not (tmp_path / "alert-1" / "evidence_raw.mp4").exists()


def test_one_failing_clip_does_not_stop_the_next(tmp_path):
    from backend.src.infra.local_media_store import LocalMediaStore
    from backend.src.infra.recorded_clips import ClipWorker, _ClipJob

    done = []
    worker = ClipWorker(settle_s=0.0, retry_s=0.0, transcode=lambda s, d: d.write_bytes(b"x"))
    past = datetime.now(timezone.utc) - timedelta(seconds=60)

    def bad(dst):
        raise RuntimeError("recording server returned an empty clip")

    for alert_id, fetch in (("bad", bad), ("good", lambda dst: dst.write_bytes(b"r"))):
        worker.submit(_ClipJob(alert_id, past, past, lambda path, a=alert_id: done.append(a),
                               fetch, tmp_path / alert_id))
    worker.close(timeout_s=10)
    assert done == ["good"] and worker.failed == 1


def test_frame_built_clips_come_out_as_h264(tmp_path):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return  # nothing to assert without the encoder; the box has it
    from backend.src.infra.local_media_store import LocalMediaStore
    from backend.src.pipelines.alert_clip_buffer import BufferedFrame, FinalizedAlertClip

    t0 = datetime(2026, 9, 17, tzinfo=timezone.utc)
    frames = [BufferedFrame(i, t0, np.full((180, 320, 3), i * 20, np.uint8)) for i in range(10)]
    path = LocalMediaStore(tmp_path).save_clip("a1", FinalizedAlertClip("a1", frames, t0, t0), 5.0)
    codec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name,pix_fmt", "-of", "csv=p=0", path],
                           capture_output=True, text=True).stdout.strip()
    assert codec == "h264,yuv420p"
    assert not (tmp_path / "a1" / "evidence_mp4v.mp4").exists()


def test_the_simulator_records_and_writes_a_recording_manifest(tmp_path):
    import json

    sys.path.insert(0, str(ROOT / "backend" / "tools"))
    import rtsp_sim

    prepared = []
    real_prepare = rtsp_sim.prepare_playlist
    rtsp_sim.prepare_playlist = lambda cam, pl, wd: prepared.append((cam, pl)) or Path("/c.txt")
    try:
        config = rtsp_sim.build_config([("cam_a", Path("/v/a.mp4"))], port=8554, user="u",
                                       password="p", record_dir=tmp_path / "rec")
    finally:
        rtsp_sim.prepare_playlist = real_prepare
    assert "playback: yes" in config and "record: yes" in config
    assert "recordFormat: fmp4" in config and "action: playback" in config
    # recording forces every stream through the H.264 normalisation, since the
    # recorder cannot parse the cameras' HEVC
    assert prepared == [("cam_a", [(Path("/v/a.mp4"), 1)])]
    assert "-f concat" in config

    src = tmp_path / "m.json"
    src.write_text(json.dumps({"store_id": "s", "timezone": "UTC", "cameras": [{
        "camera_id": "cam_a", "display_name": "a", "camera_type": "counter",
        "stream": {"input_mode": "video_file", "uri": "/v/a.mp4",
                   "simulation": {"strategy": "forever"},
                   "playlist": [{"uri": "/v/a.mp4", "repeat": 2}]},
        "pipelines": []}]}))
    dest = tmp_path / "rtsp.json"
    rtsp_sim.write_rtsp_manifest(src, dest, [("cam_a", None)], host="127.0.0.1", port=8554,
                                 user="u", password="p", forever=True,
                                 playback_url="http://127.0.0.1:9996")
    stream = json.loads(dest.read_text())["cameras"][0]["stream"]
    assert stream["strategy"] == "forever" and "playlist" not in stream
    assert stream["recording"] == {"playback_url": "http://127.0.0.1:9996", "path": "cam_a"}
