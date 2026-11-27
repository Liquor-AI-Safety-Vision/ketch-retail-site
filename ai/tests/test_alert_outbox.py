"""AlertOutbox must behave exactly like a direct SqliteAlertStore call, just
later: the id it hands back synchronously has to be the id that actually
lands, in the order that satisfies alert_media's FK on alerts, and close()
has to guarantee every enqueued write is durable (or raise) before returning
-- not just empty the queue into a writer thread that may still be running.

    python ai/tools/run_tests_nopytest.py ai/tests/test_alert_outbox.py
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
    from ai.src.domain.models import CameraStreamSpec

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
    from ai.src.infra.sqlite_alert_store import seed_store_topology

    db_path = tmp_path / "alerts.sqlite"
    seed_store_topology(db_path, [_spec("counter_1")])
    return db_path


def test_ids_returned_before_close_match_what_is_actually_in_the_db(tmp_path):
    """A pipeline uses the alert_id to name a snapshot dir the instant
    create_alert() returns -- it must already be the row's real id, not a
    placeholder swapped for a different one once the write lands."""
    from ai.src.infra.sqlite_alert_store import SqliteAlertStore
    from ai.src.runtime.alert_outbox import AlertOutbox

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
    from ai.src.infra.sqlite_alert_store import SqliteAlertStore
    from ai.src.runtime.alert_outbox import AlertOutbox

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
    from ai.src.runtime.alert_outbox import AlertOutbox

    class _BrokenSink:
        def start_run(self, *a, **k):
            raise RuntimeError("sink is down")

        def close(self):
            pass

    outbox = AlertOutbox(lambda: _BrokenSink())
    outbox.start_run(camera_id="counter_1", source_ref="test")
    with pytest.raises(RuntimeError, match="sink is down"):
        outbox.close()


class _RecordingSink:
    """A sink that fails on cue, and says which failures are transient."""

    def __init__(self, fail_plan):
        # fail_plan: {method_name: [exception, exception, ...]} consumed in order
        self.fail_plan = {k: list(v) for k, v in fail_plan.items()}
        self.landed: list[tuple[str, str]] = []
        self.attempts: dict[str, int] = {}

    @staticmethod
    def is_transient(exc):
        return isinstance(exc, ConnectionError)

    def _maybe_fail(self, name):
        self.attempts[name] = self.attempts.get(name, 0) + 1
        plan = self.fail_plan.get(name)
        if plan:
            raise plan.pop(0)

    def start_run(self, camera_id, source_ref, status, metrics, run_id):
        self._maybe_fail("start_run")
        self.landed.append(("run", run_id))

    def finish_run(self, run_id, status, metrics):
        self._maybe_fail("finish_run")
        self.landed.append(("finish", run_id))

    def create_alert(self, *, alert_id, **kwargs):
        self._maybe_fail("create_alert")
        self.landed.append(("alert", alert_id))

    def add_alert_media(self, *, media_id, alert_id, **kwargs):
        self._maybe_fail("add_alert_media")
        self.landed.append(("media", alert_id))

    def touch_camera(self, camera_id, at):
        self._maybe_fail("touch_camera")
        self.landed.append(("seen", camera_id))

    def close(self):
        pass


def _fast_retries():
    """Shrink the backoff for the duration of a test; returns a restore fn."""
    from ai.src.runtime import alert_outbox

    saved = (alert_outbox.RETRY_BACKOFF_S, alert_outbox.RETRY_BACKOFF_MAX_S)
    alert_outbox.RETRY_BACKOFF_S = alert_outbox.RETRY_BACKOFF_MAX_S = 0.01

    def restore():
        alert_outbox.RETRY_BACKOFF_S, alert_outbox.RETRY_BACKOFF_MAX_S = saved
    return restore


def _alert(outbox, run_id):
    return outbox.create_alert(
        store_id="store_A", camera_id="counter_1", run_id=run_id,
        pipeline_type="counter_cash_theft_v1", event_type="theft", severity="critical",
        opened_at="2026-09-17T00:00:00Z", identity_person_id=None, identity_label=None,
        identity_score=None, reason_codes=[])


def test_a_transient_failure_is_retried_in_place_and_nothing_after_it_is_lost():
    """The failure this replaced: one dropped Neon connection used to make the
    outbox skip every later write until close(). A connection error must now
    be waited out, and the write order the FK depends on must survive it."""
    from ai.src.runtime.alert_outbox import AlertOutbox

    restore = _fast_retries()
    sink = _RecordingSink({"create_alert": [ConnectionError("neon went away")] * 3})
    try:
        outbox = AlertOutbox(lambda: sink)
        run_id = outbox.start_run(camera_id="counter_1", source_ref="test")
        first = _alert(outbox, run_id)
        outbox.add_alert_media(alert_id=first, kind="snapshot", path="/x.jpg")
        second = _alert(outbox, run_id)
        outbox.close()  # must not raise: everything eventually landed
    finally:
        restore()

    assert sink.attempts["create_alert"] == 3 + 2
    assert sink.landed == [("run", run_id), ("alert", first), ("media", first), ("alert", second)]


def test_a_permanent_failure_drops_that_write_only_and_still_raises_at_close():
    from ai.src.runtime.alert_outbox import AlertOutbox

    restore = _fast_retries()
    sink = _RecordingSink({"create_alert": [ValueError("bad row")]})
    try:
        outbox = AlertOutbox(lambda: sink)
        run_id = outbox.start_run(camera_id="counter_1", source_ref="test")
        bad = _alert(outbox, run_id)
        outbox.add_alert_media(alert_id=bad, kind="snapshot", path="/bad.jpg")
        good = _alert(outbox, run_id)
        outbox.add_alert_media(alert_id=good, kind="snapshot", path="/good.jpg")
        with pytest.raises(ValueError) as excinfo:
            outbox.close()
    finally:
        restore()

    assert "bad row" in str(excinfo.value)
    # Not retried: a ValueError is not something waiting fixes.
    assert sink.attempts["create_alert"] == 2
    # The dropped alert's media was skipped rather than attempted against a
    # row that does not exist; the next alert was unaffected by either.
    assert sink.landed == [("run", run_id), ("alert", good), ("media", good)]


def test_transient_retries_give_up_once_close_has_waited_long_enough():
    """While running an outage is waited out; at shutdown it cannot be, or a
    Neon outage would hang the business tier until the parent kills it."""
    from ai.src.runtime import alert_outbox
    from ai.src.runtime.alert_outbox import AlertOutbox

    restore = _fast_retries()
    saved_budget = alert_outbox.CLOSE_RETRY_BUDGET_S
    alert_outbox.CLOSE_RETRY_BUDGET_S = 0.2
    sink = _RecordingSink({"start_run": [ConnectionError("down")] * 10_000})
    try:
        outbox = AlertOutbox(lambda: sink)
        outbox.start_run(camera_id="counter_1", source_ref="test")
        with pytest.raises(ConnectionError):
            outbox.close()
    finally:
        alert_outbox.CLOSE_RETRY_BUDGET_S = saved_budget
        restore()
    assert sink.landed == []


def test_a_sink_that_cannot_be_built_fails_close_instead_of_hanging():
    from ai.src.runtime.alert_outbox import AlertOutbox

    def factory():
        raise RuntimeError("no credentials")

    outbox = AlertOutbox(factory)
    outbox.start_run(camera_id="counter_1", source_ref="test")
    with pytest.raises(RuntimeError) as excinfo:
        outbox.close()
    assert "no credentials" in str(excinfo.value)


def test_close_twice_is_harmless():
    """business_tier closes once before reporting stats and again in its
    finally; the second must neither block nor raise a second time."""
    from ai.src.runtime.alert_outbox import AlertOutbox

    outbox = AlertOutbox(lambda: _RecordingSink({}))
    outbox.close()
    outbox.close()


def test_camera_heartbeats_land_but_a_failed_one_never_blocks_or_fails_alerts():
    """cameras.last_frame_at is best effort: during an outage a heartbeat is
    not retried (alerts behind it would wait on stale data) and its failure
    does not make close() report a dropped write."""
    from ai.src.runtime.alert_outbox import AlertOutbox

    sink = _RecordingSink({"touch_camera": [ConnectionError("neon went away"), ValueError("odd")]})
    outbox = AlertOutbox(lambda: sink)
    run_id = outbox.start_run(camera_id="counter_1", source_ref="test")
    outbox.touch_camera("counter_1", "2026-11-27T00:00:00+00:00")   # transient failure: skipped
    first = _alert(outbox, run_id)
    outbox.touch_camera("counter_1", "2026-11-27T00:00:30+00:00")   # permanent failure: skipped
    outbox.touch_camera("counter_1", "2026-11-27T00:01:00+00:00")   # lands
    outbox.close()  # must not raise

    assert sink.attempts["touch_camera"] == 3
    assert sink.landed == [("run", run_id), ("alert", first), ("seen", "counter_1")]


def test_sqlite_records_last_frame_and_upgrades_an_older_database(tmp_path):
    from ai.src.infra.sqlite_alert_store import SqliteAlertStore

    db_path = _seeded_db(tmp_path)
    # A database from before the column existed.
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE cameras DROP COLUMN last_frame_at")
    conn.commit()
    conn.close()

    store = SqliteAlertStore(db_path)
    store.touch_camera("counter_1", "2026-11-27T01:02:03+00:00")
    store.close()
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT last_frame_at FROM cameras WHERE camera_id='counter_1'").fetchone() == (
        "2026-11-27T01:02:03+00:00",)
    conn.close()
