"""Foreign keys on alerts/pipeline_runs are on, and now have something to
point at.

Turning on PRAGMA foreign_keys was tried once before and reverted: a fresh
database, no seeding of stores/cameras, and the very first start_run() died
with "FOREIGN KEY constraint failed" before a single frame had been
processed. This file keeps that failure reproducible as a test instead of
something that has to be rediscovered by hand -- the first test below is
that exact scenario -- and proves the fix: seed_store_topology() populates
stores/cameras from the run's own CameraStreamSpecs, called once from run()
before any tier starts writing, and after that the same start_run() call
succeeds.

    python backend/tools/run_tests_nopytest.py backend/tests/test_alert_store_fk.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
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


def test_start_run_fails_on_a_fresh_unseeded_database(tmp_path):
    """The exact failure that shipped once: fresh db, no seed, first write."""
    from backend.src.infra.sqlite_alert_store import SqliteAlertStore

    store = SqliteAlertStore(tmp_path / "alerts.sqlite")
    try:
        with pytest.raises(Exception) as excinfo:
            store.start_run(camera_id="counter_1", source_ref="test")
        assert "FOREIGN KEY" in str(excinfo.value)
    finally:
        store.close()


def test_seed_store_topology_makes_start_run_succeed(tmp_path):
    from backend.src.infra.sqlite_alert_store import SqliteAlertStore, seed_store_topology

    db_path = tmp_path / "alerts.sqlite"
    seed_store_topology(db_path, [_spec("counter_1"), _spec("counter_2")])

    store = SqliteAlertStore(db_path)
    try:
        run_id = store.start_run(camera_id="counter_1", source_ref="test")
        assert run_id
    finally:
        store.close()


def test_seed_store_topology_is_idempotent_and_updates_in_place(tmp_path):
    """Every restart reuses the same db_path -- re-seeding must not fail on
    the existing rows, and must pick up a manifest edit (display_name here)
    rather than leaving the first run's value stuck."""
    from backend.src.infra.sqlite_alert_store import seed_store_topology
    import sqlite3

    db_path = tmp_path / "alerts.sqlite"
    seed_store_topology(db_path, [_spec("counter_1")])

    renamed = _spec("counter_1")
    renamed.display_name = "Counter One (renamed)"
    seed_store_topology(db_path, [renamed])  # must not raise

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT display_name FROM cameras WHERE camera_id = ?", ("counter_1",)
    ).fetchone()
    conn.close()
    assert row[0] == "Counter One (renamed)"


def test_the_store_row_carries_the_manifest_name_not_the_store_id(tmp_path):
    """stores.display_name is NOT NULL and read by every dashboard.

    It used to be filled with the store_id repeated, so a manifest saying
    "Store 1" produced a row saying "store_1" -- and the ON CONFLICT clause
    refreshed only the timezone, so fixing the manifest changed nothing on
    the next run either.
    """
    from backend.src.infra.sqlite_alert_store import seed_store_topology
    import sqlite3

    db_path = tmp_path / "alerts.sqlite"
    seed_store_topology(db_path, [_spec("counter_1", store_id="store_1")])

    def stored_name():
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT display_name FROM stores WHERE store_id = ?", ("store_1",)
        ).fetchone()
        conn.close()
        return row[0]

    assert stored_name() == "Store store_1", "the manifest name did not reach the row"

    renamed = _spec("counter_1", store_id="store_1")
    renamed.store_display_name = "Downtown Liquor"
    seed_store_topology(db_path, [renamed])
    assert stored_name() == "Downtown Liquor", "a renamed store stayed stale on re-seed"


def test_a_manifest_with_no_display_name_falls_back_to_the_store_id(tmp_path):
    """The column is NOT NULL, so the fallback has to produce something."""
    import json
    from backend.src.infra.local_config import camera_specs_from_manifest

    manifest = json.loads(
        (BACKEND / "config" / "stores" / "store_1.json").read_text())
    del manifest["display_name"]
    path = tmp_path / "no_name.json"
    path.write_text(json.dumps(manifest))

    specs = camera_specs_from_manifest(path)
    assert specs[0].store_display_name == "store_1"


def test_a_camera_moved_to_a_different_store_updates_not_duplicates(tmp_path):
    from backend.src.infra.sqlite_alert_store import seed_store_topology
    import sqlite3

    db_path = tmp_path / "alerts.sqlite"
    seed_store_topology(db_path, [_spec("counter_1", store_id="store_A")])
    seed_store_topology(db_path, [_spec("counter_1", store_id="store_B")])

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT store_id FROM cameras WHERE camera_id = ?", ("counter_1",)
    ).fetchall()
    conn.close()
    assert rows == [("store_B",)]
