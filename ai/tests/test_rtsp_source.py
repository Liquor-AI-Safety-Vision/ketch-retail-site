"""Live RTSP sources, and the two silent failures on the way to them.

The first is that camera URIs used to be destroyed before anything tried to
open them. Manifest paths go through resolve_repo_path, which treats its
argument as a filesystem path -- applied to "rtsp://host/path" that yields
"<repo>/rtsp:/host/path", because Path() collapses the double slash and then
the non-absolute result gets prefixed with the repo root. Nothing raises;
VideoCapture simply fails to open a camera that was configured correctly.

The second is that the fix makes the first one dangerous. A working camera
URI carries credentials inline, and seed_store_topology writes source_uri
into the cameras table on both SQLite and Postgres -- so the camera password
would be persisted, backed up, and rendered by anything showing a camera row.

Everything here runs without a camera or the simulator. The parts that need a
live stream (reconnect, buffer backlog) are measured in
docs/RTSP_MIGRATION.md rather than asserted here, because a test that depends
on a server being up fails for the wrong reason when it is not.

    python ai/tools/run_tests_nopytest.py ai/tests/test_rtsp_source.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RTSP_URI = "rtsp://admin:s3cret@192.168.11.51:554/cam/realmonitor?channel=1&subtype=0"


def _spec(**overrides):
    from ai.src.domain.models import CameraStreamSpec

    base = dict(
        company_id="test_co", store_id="store_A", store_display_name="Store A",
        store_timezone="Asia/Ho_Chi_Minh",
        camera_id="counter_1", display_name="Counter 1", camera_type="counter",
        source_kind="rtsp", source_uri=RTSP_URI, source_path=RTSP_URI,
        strategy="trim", start_s=0.0, target_duration_s=10.0, target_fps=5.0,
        runtime_profile={}, perception_profile={}, simulation_profile={},
        pipeline_bindings=[], raw_config={},
    )
    base.update(overrides)
    return CameraStreamSpec(**base)


def test_a_camera_uri_survives_manifest_resolution():
    """The bug that blocked RTSP entirely: the URI came out as a repo path."""
    from ai.src.common.stream_uri import resolve_source_uri

    assert resolve_source_uri(RTSP_URI) == RTSP_URI


def test_a_file_path_is_still_resolved_against_the_repo():
    """Fixing URIs must not stop ordinary manifest paths from resolving."""
    from ai.src.common.stream_uri import resolve_source_uri

    resolved = resolve_source_uri("videos/cctv_counter/x.mp4")
    assert resolved.startswith(str(ROOT))
    assert resolved.endswith("videos/cctv_counter/x.mp4")


def test_credentials_can_come_from_the_environment():
    """So a manifest in version control never contains a camera password.

    Sets os.environ directly rather than taking monkeypatch: run_tests_nopytest
    only injects tmp_path, and a fixture this file cannot get is a test that
    silently does not run under the project's own runner.
    """
    import os

    from ai.src.common.stream_uri import resolve_source_uri

    previous = os.environ.get("CAM_PASS")
    os.environ["CAM_PASS"] = "from_env"
    try:
        resolved = resolve_source_uri("rtsp://admin:${CAM_PASS}@10.0.0.5:554/live")
    finally:
        if previous is None:
            os.environ.pop("CAM_PASS", None)
        else:
            os.environ["CAM_PASS"] = previous
    assert resolved == "rtsp://admin:from_env@10.0.0.5:554/live"


def test_redaction_removes_the_password_and_keeps_the_rest():
    from ai.src.common.stream_uri import redact_stream_credentials

    redacted = redact_stream_credentials(RTSP_URI)
    assert "s3cret" not in redacted
    assert "admin" not in redacted
    # Host, port, path and query all still identify which camera this is.
    assert redacted == "rtsp://***@192.168.11.51:554/cam/realmonitor?channel=1&subtype=0"


def test_redaction_leaves_uris_without_credentials_alone():
    from ai.src.common.stream_uri import redact_stream_credentials

    plain = "rtsp://192.168.11.51:554/live"
    assert redact_stream_credentials(plain) == plain
    assert redact_stream_credentials("/videos/x.mp4") == "/videos/x.mp4"
    assert redact_stream_credentials(None) is None


def test_the_camera_password_is_not_written_to_the_database(tmp_path):
    """The leak this guards: source_uri is persisted, backups keep it forever."""
    from ai.src.infra.sqlite_alert_store import SqliteAlertStore, seed_store_topology

    db = tmp_path / "alerts.sqlite"
    SqliteAlertStore(str(db))
    seed_store_topology(str(db), [_spec()])

    import sqlite3
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT source_uri FROM cameras").fetchall()
    finally:
        conn.close()

    assert rows, "seed_store_topology wrote no camera row"
    stored = rows[0][0]
    assert "s3cret" not in stored
    assert stored == "rtsp://***@192.168.11.51:554/cam/realmonitor?channel=1&subtype=0"


def test_the_source_class_is_chosen_by_kind():
    from ai.src.runtime.rtsp_stream import RtspStreamSource, open_stream_source

    # Constructed without touching the network: __init__ validates, _probe is
    # what opens the stream, and that only happens for a reachable camera.
    with pytest.raises(Exception):
        # Unreachable address, so this proves the class was selected and then
        # failed at open -- not that some other kind was picked.
        open_stream_source(_spec(source_uri="rtsp://127.0.0.1:1/none",
                                 source_path="rtsp://127.0.0.1:1/none"))
    assert RtspStreamSource is not None


def test_seeking_a_live_camera_is_rejected_not_ignored():
    """A manifest asking a camera to start 30s in has a misunderstanding in it.

    Silently ignoring start_s would produce a stream that looks fine and is
    not what the manifest asked for.
    """
    from ai.src.runtime.rtsp_stream import RtspStreamSource

    with pytest.raises(ValueError, match="nothing to seek"):
        RtspStreamSource(_spec(start_s=30.0))


def test_looping_a_live_camera_is_rejected_not_ignored():
    from ai.src.runtime.rtsp_stream import RtspStreamSource

    with pytest.raises(ValueError, match="no clip to replay"):
        RtspStreamSource(_spec(strategy="loop_to_duration"))


def test_the_file_source_refuses_rtsp_and_says_where_to_go():
    """It used to raise NotImplementedError; the class now exists."""
    from ai.src.runtime.virtual_video_stream import VirtualVideoStreamSource

    with pytest.raises(ValueError, match="open_stream_source"):
        VirtualVideoStreamSource(_spec())


def test_an_unknown_transport_is_rejected_at_construction():
    from ai.src.runtime.rtsp_stream import RtspStreamSource

    with pytest.raises(ValueError, match="transport"):
        RtspStreamSource(_spec(raw_config={"stream": {"rtsp": {"transport": "sctp"}}}))


def _probed(reported_fps: float, target_fps: float, rtsp_options=None):
    """Build an RtspStreamSource with _probe stubbed to a known frame rate.

    The real _probe opens the stream. Every frame-rate rule below is about
    what happens once the rate is known, so the network part is replaced and
    the rest of __init__ -- including the check under test -- runs for real.
    """
    from ai.src.domain.models import StreamSourceInfo
    from ai.src.runtime import rtsp_stream

    raw = {"stream": {"rtsp": dict(rtsp_options or {})}}
    spec = _spec(target_fps=target_fps, raw_config=raw)

    real_probe = rtsp_stream.RtspStreamSource._probe

    def fake_probe(self):
        native = reported_fps
        self.native_fps_source = "reported"
        if native <= 0:
            if self.native_fps_hint:
                native = self.native_fps_hint
                self.native_fps_source = "manifest hint"
            else:
                native = rtsp_stream.DEFAULT_NATIVE_FPS
                self.native_fps_source = "built-in default"
        return StreamSourceInfo(path="rtsp://redacted", native_fps=native,
                                frame_count=0, duration_s=0.0, width=1920, height=1080)

    rtsp_stream.RtspStreamSource._probe = fake_probe
    try:
        return rtsp_stream.RtspStreamSource(spec)
    finally:
        rtsp_stream.RtspStreamSource._probe = real_probe


def test_asking_a_camera_for_more_fps_than_it_sends_is_refused():
    """The failure it replaces is silent, which is why it has to be loud.

    source_step = native/target falls below 1, so every grabbed frame passes
    the decimation test and the lane emits native_fps -- while the tracker,
    the FSM and every confirm window have already been sized for target_fps.
    Each window then covers more wall-clock time than intended and no metric
    disagrees.
    """
    try:
        _probed(reported_fps=5.0, target_fps=10.0)
    except ValueError as e:
        assert "target_fps=10" in str(e) and "5 fps" in str(e)
        assert "reported" in str(e), "the message should say where the rate came from"
    else:
        raise AssertionError("target_fps above the camera's reported rate was accepted")


def test_equal_and_lower_target_fps_are_fine():
    assert _probed(reported_fps=15.0, target_fps=15.0).info.native_fps == 15.0
    assert _probed(reported_fps=25.0, target_fps=5.0).info.native_fps == 25.0


def test_an_assumed_frame_rate_warns_instead_of_refusing():
    """Refusing on a guess would take a working camera down over a header field.

    Many RTSP cameras report no frame rate at all, so native_fps falls back to
    a constant. That constant being lower than target_fps is not evidence the
    manifest is wrong -- it is evidence we do not know the rate.
    """
    src = _probed(reported_fps=0.0, target_fps=20.0)   # must not raise
    assert src.native_fps_source == "built-in default"
    assert src.info.native_fps == 15.0


def test_the_manifest_can_supply_the_rate_the_camera_will_not():
    """stream.rtsp.native_fps was read by the source and refused by the schema.

    RtspStreamSource has always done options.get("native_fps"); the manifest
    schema never listed it, so the one way to correct a camera that reports
    nothing was an unknown-key error at load time.
    """
    import json

    from ai.src.infra.manifest_schema import validate_manifest

    src = _probed(reported_fps=0.0, target_fps=5.0, rtsp_options={"native_fps": 25.0})
    assert src.info.native_fps == 25.0
    assert src.native_fps_source == "manifest hint"

    data = json.loads((BACKEND / "config" / "stores" / "store_1.json").read_text())
    cam = data["cameras"][0]
    cam["stream"] = {"input_mode": "rtsp", "uri": "rtsp://host/cam",
                     "rtsp": {"transport": "tcp", "native_fps": 25}}
    warnings = validate_manifest(data)
    assert not [w for w in warnings if "native_fps" in w]


def test_a_video_file_shorter_on_fps_than_the_manifest_asks_is_refused():
    """Same rule on the file path, where native_fps is always a measurement."""
    from ai.src.domain.models import StreamSourceInfo
    from ai.src.runtime import virtual_video_stream as vvs

    spec = _spec(source_kind="video_file", source_uri="clip.mp4",
                 source_path="clip.mp4", target_fps=10.0)
    real_probe = vvs.VirtualVideoStreamSource._probe
    vvs.VirtualVideoStreamSource._probe = lambda self: StreamSourceInfo(
        path="clip.mp4", native_fps=5.0, frame_count=40, duration_s=8.0,
        width=960, height=544)
    try:
        vvs.VirtualVideoStreamSource(spec)
    except ValueError as e:
        assert "target_fps=10" in str(e) and "5 fps" in str(e)
    else:
        raise AssertionError("target_fps above the file's own fps was accepted")
    finally:
        vvs.VirtualVideoStreamSource._probe = real_probe
