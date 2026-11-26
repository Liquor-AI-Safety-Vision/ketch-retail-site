"""Merging manifests is how one run serves several stores.

The split pipeline's tiers were never written to know what a store is --
they shard a flat list of CameraStreamSpec, and each spec already carries
its own store_id. So running several stores together needs nothing from
decode/inference/business; it only needs their manifests' specs concatenated
before ring/queue allocation, which is what camera_specs_from_manifests does.

The one way that goes wrong silently: rings, decode queues and business
queues are all keyed by camera_id. Two stores that happen to both name a
camera "camera_1" would have the second spec's ring allocation overwrite the
first's dict entry -- one store's frames landing in another store's ring,
with nothing in the log to say so. That is the one thing this file checks
for; camera_specs_from_manifest itself (single-manifest parsing) already has
years of production manifests behind it and does not need re-testing here.

    python ai/tools/run_tests_nopytest.py ai/tests/test_multi_store_manifest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_manifest(path: Path, store_id: str, camera_ids: list[str]) -> None:
    path.write_text(json.dumps({
        "store_id": store_id,
        "company_id": "test_co",
        "timezone": "Asia/Ho_Chi_Minh",
        "target_fps": 5.0,
        "cameras": [
            {
                "camera_id": cam_id,
                "display_name": cam_id,
                "camera_type": "storage",
                "stream": {"kind": "file", "uri": f"videos/{cam_id}.mp4"},
                "pipelines": [],
            }
            for cam_id in camera_ids
        ],
    }))


def test_two_stores_merge_into_one_flat_list(tmp_path):
    from ai.src.infra.local_config import camera_specs_from_manifests

    a = tmp_path / "store_a.json"
    b = tmp_path / "store_b.json"
    _write_manifest(a, "store_A", ["cam_1", "cam_2"])
    _write_manifest(b, "store_B", ["cam_3", "cam_4"])

    specs = camera_specs_from_manifests([a, b])

    assert [s.camera_id for s in specs] == ["cam_1", "cam_2", "cam_3", "cam_4"]
    assert [s.store_id for s in specs] == ["store_A", "store_A", "store_B", "store_B"]


def test_duplicate_camera_id_across_manifests_is_refused(tmp_path):
    from ai.src.infra.local_config import camera_specs_from_manifests

    a = tmp_path / "store_a.json"
    b = tmp_path / "store_b.json"
    _write_manifest(a, "store_A", ["cam_1", "cam_2"])
    _write_manifest(b, "store_B", ["cam_2", "cam_3"])  # cam_2 collides

    with pytest.raises(ValueError) as excinfo:
        camera_specs_from_manifests([a, b])

    message = str(excinfo.value)
    # Naming the id and the two files is what makes this a fixable error
    # instead of a mystery when a real deployment hits it.
    assert "cam_2" in message
    assert str(a) in message and str(b) in message


def test_a_single_manifest_still_works_unmerged(tmp_path):
    """The common case -- one store, one manifest -- must not regress."""
    from ai.src.infra.local_config import camera_specs_from_manifests

    a = tmp_path / "store_a.json"
    _write_manifest(a, "store_A", ["cam_1", "cam_2", "cam_3"])

    specs = camera_specs_from_manifests([a])

    assert len(specs) == 3
    assert all(s.store_id == "store_A" for s in specs)
