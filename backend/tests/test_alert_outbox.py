"""AlertOutbox must behave exactly like a direct SqliteAlertStore call, just
later: the id it hands back synchronously has to be the id that actually
lands, in the order that satisfies alert_media's FK on alerts, and close()
has to guarantee every enqueued write is durable (or raise) before returning
-- not just empty the queue into a writer thread that may still be running.

    python backend/tools/run_tests_nopytest.py backend/tests/test_alert_outbox.py
"""
from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _spec(camera_id: str, store_id: str = "store_A"):
    from backend.src.domain.models import CameraStreamSpec

    return CameraStreamSpec(
        company_id="test_co", store_id=store_id, store_display_name=f"Store {store_id}",
        store_timezone="Asia/Ho_Chi_Minh",
        camera_id=camera_id, display_name=camera_id, camera_type="storage",
        source_kind="file", source_uri=f"videos/{camera_id}.mp4",
        source_path=f"videos/{camera_id}.mp4", strategy="trim", start_s=0.0,
        target_duration_s=10.0, target_fps=5.0, runtime_profile={},
        perception_profile={}, simulation_profile={}, pipeline_bindings=[],
        raw_config={},
    )


def _seeded_db(tmp_path) -> Path:
    from backend.src.infra.sqlite_alert_store import seed_store_topology

    db_path = tmp_path / "alerts.sqlite"
    seed_store_topology(db_path, [_spec("counter_1")])
    return db_path


def test_ids_returned_before_close_match_what_is_actually_in_the_db(tmp_path):
    """A pipeline uses the alert_id to name a snapshot dir the instant
    create_alert() returns -- it must already be the row's real id, not a
    placeholder swapped for a different one once the write lands."""
    from backend.src.infra.sqlite_alert_store import SqliteAlertStore
    from backend.src.runtime.alert_outbox import AlertOutbox

    db_path = _seeded_db(tmp_path)
    outbox = AlertOutbox(lambda: SqliteAlertStore(db_path))
    try:
        run_id = outbox.start_run(camera_id="counter_1", source_ref="test")
        alert_id = outbox.create_alert(
            store_id="store_A", camera_id="counter_1", run_id=run_id,
            pipeline_type="storage_intrusion_v1", event_type="intrusion",
            severity="warning", opened_at="2026-08-26T00:00:00Z",
            identity_person_id=None, identity_label=None, identity_score=None,
            reason_codes=[])
        media_id = outbox.add_alert_media(
            alert_id=alert_id, kind="snapshot", path="/tmp/x.jpg")
    finally:
        outbox.close()

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT run_id FROM pipeline_runs WHERE run_id = ?", (run_id,)
        ).fetchone() == (run_id,)
        assert conn.execute(
            "SELECT alert_id FROM alerts WHERE alert_id = ?", (alert_id,)
        ).fetchone() == (alert_id,)
        assert conn.execute(
            "SELECT media_id, alert_id FROM alert_media WHERE media_id = ?", (media_id,)
        ).fetchone() == (media_id, alert_id)
    finally:
        conn.close()


def test_alert_media_never_arrives_before_its_alert_row_under_concurrent_cameras(tmp_path):
    """Several 'cameras' (threads) hammer the same outbox at once, each
    doing its own create_alert -> add_alert_media in sequence -- like real
    ThreadPoolExecutor camera workers sharing one process's outbox. If the
    writer ever applied a media write before its own alert write, this
    would fail with a real sqlite FOREIGN KEY error (alert_media.alert_id
    references alerts.alert_id, and foreign_keys=ON)."""
    from backend.src.infra.sqlite_alert_store import SqliteAlertStore
    from backend.src.runtime.alert_outbox import AlertOutbox

    db_path = _seeded_db(tmp_path)
    outbox = AlertOutbox(lambda: SqliteAlertStore(db_path))

    def one_camera(n: int):
        run_id = outbox.start_run(camera_id="counter_1", source_ref=f"cam-{n}")
        for i in range(20):
            alert_id = outbox.create_alert(
                store_id="store_A", camera_id="counter_1", run_id=run_id,
                pipeline_type="storage_intrusion_v1", event_type="intrusion",
                severity="warning", opened_at="2026-08-26T00:00:00Z",
                identity_person_id=None, identity_label=None, identity_score=None,
                reason_codes=[])
            outbox.add_alert_media(alert_id=alert_id, kind="snapshot", path=f"/tmp/{n}-{i}.jpg")

    threads = [threading.Thread(target=one_camera, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    outbox.close()  # raises if any queued write failed (e.g. a real FK error)

    conn = sqlite3.connect(db_path)
    try:
        alerts = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        media = conn.execute("SELECT COUNT(*) FROM alert_media").fetchone()[0]
    finally:
        conn.close()
    assert alerts == 8 * 20
    assert media == 8 * 20


def test_close_raises_the_writer_thread_error_instead_of_swallowing_it(tmp_path):
    """A write failure on the writer thread is invisible to the pipeline
    thread that called create_alert() (it already got its id back) -- close()
    is the only place left to surface it, so a broken sink must not look like
    a clean shutdown."""
    from backend.src.runtime.alert_outbox import AlertOutbox

    class _BrokenSink:
        def start_run(self, *a, **k):
            raise RuntimeError("sink is down")

        def close(self):
            pass

    outbox = AlertOutbox(lambda: _BrokenSink())
    outbox.start_run(camera_id="counter_1", source_ref="test")
    with pytest.raises(RuntimeError, match="sink is down"):
        outbox.close()
