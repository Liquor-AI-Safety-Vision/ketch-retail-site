"""cameras.metadata_json carries each camera's pipelines and zones.

The Go dashboard API reads it to show what a camera watches; it has no access
to ai/config. These tests pin that the zones come out of the very files the
pipelines read, that a broken zone file is reported rather than fatal, and
that the seeder actually writes and refreshes the column.

    python ai/tools/run_tests_nopytest.py ai/tests/test_camera_metadata.py
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AI = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MANIFEST = AI / "config" / "stores" / "store_test_01.json"


def _specs():
    from ai.src.infra.local_config import camera_specs_from_manifest

    return {s.camera_id: s for s in camera_specs_from_manifest(str(MANIFEST))}


def test_zones_are_the_ones_the_pipelines_read():
    from ai.src.infra.camera_metadata import camera_metadata

    specs = _specs()
    counter = camera_metadata(specs["st01_counter"])
    assert counter["pipelines"] == ["counter_cash_theft_v1"]
    names = {z["name"] for z in counter["zones"]}
    # load_checkout_zones keeps exactly the checkout_* polygons; both must be here.
    assert {"checkout_left", "checkout_right"} <= names
    zones_file = json.loads((AI / "config/zones/top_side_view_zones.json").read_text())
    by_name = {z["name"]: z for z in counter["zones"]}
    for zone in zones_file["zones"]:
        assert by_name[zone["name"]]["points_norm"] == zone["points_norm"]

    storage = camera_metadata(specs["st01_storage"])
    storage_file = json.loads((AI / "config/zones/storage_zones.json").read_text())
    assert storage["zones"] == [{"pipeline_id": "storage_intrusion_v1", "name": "storage_2",
                                 "type": "polygon",
                                 "points_norm": storage_file["storage_2"]["zone_norm"]}]

    entrance = camera_metadata(specs["st01_entrance"])
    assert [z["name"] for z in entrance["zones"]] == ["entrance"]

    tracking = camera_metadata(specs["st01_whole_store"])
    assert tracking == {"pipelines": ["whole_store_tracking_v1"], "zones": []}


def test_every_point_is_a_fraction_of_the_frame():
    from ai.src.infra.camera_metadata import camera_metadata

    for spec in _specs().values():
        for zone in camera_metadata(spec)["zones"]:
            for x, y in zone["points_norm"]:
                assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0, (spec.camera_id, zone["name"], x, y)


def test_a_missing_zone_file_is_reported_not_raised(tmp_path):
    from ai.src.infra.camera_metadata import camera_metadata

    spec = _specs()["st01_storage"]
    binding = dataclasses.replace(spec.pipeline_bindings[0], params={
        **spec.pipeline_bindings[0].params, "zone_config_path": str(tmp_path / "nope.json")})
    meta = camera_metadata(dataclasses.replace(spec, pipeline_bindings=[binding]))
    assert meta["pipelines"] == ["storage_intrusion_v1"]
    assert meta["zones"] == []
    assert len(meta["zone_errors"]) == 1 and "nope.json" in meta["zone_errors"][0]


def test_seeding_writes_and_refreshes_the_column(tmp_path):
    from ai.src.infra.sqlite_alert_store import seed_store_topology

    db = tmp_path / "t.sqlite"
    specs = list(_specs().values())
    seed_store_topology(db, specs)
    conn = sqlite3.connect(db)
    meta = json.loads(conn.execute(
        "SELECT metadata_json FROM cameras WHERE camera_id='st01_counter'").fetchone()[0])
    assert meta["pipelines"] == ["counter_cash_theft_v1"] and meta["zones"]

    conn.execute("UPDATE cameras SET metadata_json='{}' WHERE camera_id='st01_counter'")
    conn.commit()
    seed_store_topology(db, specs)
    meta = json.loads(conn.execute(
        "SELECT metadata_json FROM cameras WHERE camera_id='st01_counter'").fetchone()[0])
    assert meta["zones"], "a restart must refresh metadata_json from the manifest"
    conn.close()
