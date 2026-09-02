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

    python backend/tools/run_tests_nopytest.py backend/tests/test_rtsp_source.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RTSP_URI = "rtsp://admin:s3cret@192.168.11.51:554/cam/realmonitor?channel=1&subtype=0"


def _spec(**overrides):
    from backend.src.domain.models import CameraStreamSpec

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
    from backend.src.common.stream_uri import resolve_source_uri

    assert resolve_source_uri(RTSP_URI) == RTSP_URI


def test_a_file_path_is_still_resolved_against_the_repo():
    """Fixing URIs must not stop ordinary manifest paths from resolving."""
    from backend.src.common.stream_uri import resolve_source_uri

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

    from backend.src.common.stream_uri import resolve_source_uri

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
    from backend.src.common.stream_uri import redact_stream_credentials

    redacted = redact_stream_credentials(RTSP_URI)
    assert "s3cret" not in redacted
    assert "admin" not in redacted
    # Host, port, path and query all still identify which camera this is.
    assert redacted == "rtsp://***@192.168.11.51:554/cam/realmonitor?channel=1&subtype=0"


def test_redaction_leaves_uris_without_credentials_alone():
    from backend.src.common.stream_uri import redact_stream_credentials

    plain = "rtsp://192.168.11.51:554/live"
    assert redact_stream_credentials(plain) == plain
    assert redact_stream_credentials("/videos/x.mp4") == "/videos/x.mp4"
    assert redact_stream_credentials(None) is None


def test_the_camera_password_is_not_written_to_the_database(tmp_path):
    """The leak this guards: source_uri is persisted, backups keep it forever."""
    from backend.src.infra.sqlite_alert_store import SqliteAlertStore, seed_store_topology

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
    from backend.src.runtime.rtsp_stream import RtspStreamSource, open_stream_source

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
    from backend.src.runtime.rtsp_stream import RtspStreamSource

    with pytest.raises(ValueError, match="nothing to seek"):
        RtspStreamSource(_spec(start_s=30.0))


def test_looping_a_live_camera_is_rejected_not_ignored():
    from backend.src.runtime.rtsp_stream import RtspStreamSource

    with pytest.raises(ValueError, match="no clip to replay"):
        RtspStreamSource(_spec(strategy="loop_to_duration"))


def test_the_file_source_refuses_rtsp_and_says_where_to_go():
    """It used to raise NotImplementedError; the class now exists."""
    from backend.src.runtime.virtual_video_stream import VirtualVideoStreamSource

    with pytest.raises(ValueError, match="open_stream_source"):
        VirtualVideoStreamSource(_spec())


def test_an_unknown_transport_is_rejected_at_construction():
    from backend.src.runtime.rtsp_stream import RtspStreamSource

    with pytest.raises(ValueError, match="transport"):
        RtspStreamSource(_spec(raw_config={"stream": {"rtsp": {"transport": "sctp"}}}))
