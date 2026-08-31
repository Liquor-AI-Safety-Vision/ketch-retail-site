"""A manifest that says something the system will not do must be refused.

Every one of these was measured against the shipped manifests before the
validator existed, and every one loaded cleanly. That is the point: the
failure mode is not a crash, it is a camera that comes up, runs, reports
healthy numbers, and quietly uses a default the operator thought they had
overridden.
"""
from __future__ import annotations

import copy
import json
import logging
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.src.infra.manifest_schema import (  # noqa: E402
    ManifestError,
    validate_manifest,
)

MANIFESTS = sorted((BACKEND / "config" / "stores").glob("*.json"))


def _load(name: str = "store_1.json") -> dict:
    return json.loads((BACKEND / "config" / "stores" / name).read_text())


def test_every_shipped_manifest_validates():
    """The validator is worthless if it cannot accept what the repo ships."""
    for path in MANIFESTS:
        validate_manifest(json.loads(path.read_text()), path=path.name)


def test_a_misspelled_key_is_refused_and_the_fix_is_named():
    """The original finding: "match_treshold" sat beside the real key.

    It was never read. The operator believed the threshold was 0.9; it was
    0.25, and no output anywhere disagreed with them.
    """
    data = _load()
    cam = next(c for c in data["cameras"] if c["camera_id"] == "storage_1")
    cam["perception"]["face_identity"]["match_treshold"] = 0.9
    try:
        validate_manifest(data)
    except ManifestError as e:
        assert "match_treshold" in str(e)
        assert "match_threshold" in str(e), "the near-miss should be suggested"
    else:
        raise AssertionError("a misspelled key was accepted")


def test_a_wrong_type_is_refused():
    data = _load()
    data["cameras"][0]["runtime"]["target_fps"] = "5"
    try:
        validate_manifest(data)
    except ManifestError as e:
        assert "target_fps" in str(e) and "str" in str(e)
    else:
        raise AssertionError('target_fps: "5" was accepted')


def test_a_bool_where_a_number_belongs_is_refused():
    """bool is a subclass of int, so isinstance alone would let this through."""
    data = _load()
    data["cameras"][0]["runtime"]["target_fps"] = True
    try:
        validate_manifest(data)
    except ManifestError:
        pass
    else:
        raise AssertionError("target_fps: true was accepted")


def test_a_missing_required_key_is_refused():
    data = _load()
    del data["cameras"][0]["stream"]["uri"]
    try:
        validate_manifest(data)
    except ManifestError as e:
        assert "uri" in str(e) and "required" in str(e)
    else:
        raise AssertionError("a camera with no stream uri was accepted")


def test_an_unknown_pipeline_param_is_refused():
    """params are per-pipeline, so a storage key on a counter camera is a typo."""
    data = _load()
    cam = next(c for c in data["cameras"] if c["camera_id"] == "counter_1")
    cam["pipelines"][0]["params"]["zone_asset_kye"] = "x"
    try:
        validate_manifest(data)
    except ManifestError as e:
        assert "zone_asset_kye" in str(e)
    else:
        raise AssertionError("a misspelled pipeline param was accepted")


def test_a_param_belonging_to_another_pipeline_is_refused():
    data = _load()
    cam = next(c for c in data["cameras"] if c["camera_id"] == "counter_1")
    cam["pipelines"][0]["params"]["entry_confirm_seconds"] = 1.0
    try:
        validate_manifest(data)
    except ManifestError as e:
        assert "entry_confirm_seconds" in str(e)
    else:
        raise AssertionError("a storage param on a counter camera was accepted")


def test_all_errors_are_reported_at_once():
    """One message per run turns a five-typo file into five runs."""
    data = _load()
    data["cameras"][0]["runtime"]["target_fps"] = "5"
    data["cameras"][0]["bogus_key"] = 1
    del data["cameras"][1]["stream"]["uri"]
    try:
        validate_manifest(data)
    except ManifestError as e:
        assert "3 problem(s)" in str(e), str(e)
    else:
        raise AssertionError("three problems were accepted")


def test_inert_keys_warn_but_do_not_fail():
    """Correctly spelled, shipped, read by nobody -- a warning, not an error.

    Deleting them is a separate decision from detecting them, and failing on
    them would break every manifest in the repo at once.
    """
    warnings = validate_manifest(_load())
    joined = "\n".join(warnings)
    for dead in ("runtime.device", "perception.person_tracker.match_thres",
                 "perception.person_detector.nms_thres"):
        assert dead in joined, f"{dead} configures nothing and should be named"


def test_inert_warnings_are_grouped_not_repeated_per_camera():
    """5 dead keys x 16 cameras was 80 identical lines -- that buries a real one."""
    data = _load("local_dev_store_16cams.json")
    warnings = validate_manifest(data)
    assert len(warnings) < 15, f"{len(warnings)} warnings is too many to read"
    assert any("16 camera(s)" in w for w in warnings), "the count should be carried"


def test_loading_a_manifest_emits_the_warnings_once_per_process(caplog=None):
    """camera_specs_from_manifest is where the warning actually reaches a human."""
    from backend.src.infra import local_config

    local_config._WARNED.clear()
    logger = logging.getLogger("backend.src.infra.local_config")
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Capture()
    logger.addHandler(handler)
    try:
        path = str(BACKEND / "config" / "stores" / "store_1.json")
        local_config.camera_specs_from_manifest(path)
        first = len(records)
        local_config.camera_specs_from_manifest(path)
        assert first > 0, "loading a manifest with dead keys should warn"
        assert len(records) == first, "the same warning fired twice for one process"
    finally:
        logger.removeHandler(handler)
        local_config._WARNED.clear()
