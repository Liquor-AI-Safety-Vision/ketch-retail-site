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
    for dead in ("runtime.device", "perception.person_detector.nms_thres"):
        assert dead in joined, f"{dead} configures nothing and should be named"
    # match_thres was on this list until it was wired through to the tracker.
    # It reaches Config.STEP_A_MAX_COST now, so naming it here would be wrong.
    assert "person_tracker.match_thres" not in joined


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


def test_tracker_tuning_is_per_camera_and_reaches_the_tracker():
    """person_tracker.match_thres used to configure nothing, in any commit.

    Tracker already accepted a `cfg`; nothing ever passed one, so all sixteen
    cameras shared the class defaults while each manifest declared its own
    threshold. A value someone tuned for a corridor camera did nothing to it.
    """
    from backend.src.perception.uae_tracker import Config, camera_config

    counter = camera_config({"match_thres": 0.65}, camera_id="counter_1")
    storage = camera_config({"match_thres": 0.5}, camera_id="storage_2")

    assert counter.STEP_A_MAX_COST == 0.65
    assert storage.STEP_A_MAX_COST == 0.5
    assert Config.STEP_A_MAX_COST == 0.25, (
        "the shared Config was mutated -- in the split tier several cameras "
        "are threads in one process, so that retunes the others")


def test_a_tracker_setting_the_tracker_does_not_have_is_refused():
    from backend.src.perception.uae_tracker import camera_config

    try:
        camera_config({"mach_thres": 0.5}, camera_id="cam_x")
    except ValueError as e:
        assert "mach_thres" in str(e) and "cam_x" in str(e)
    else:
        raise AssertionError("a nonexistent tracker setting was accepted")


def test_device_is_accepted_as_forward_looking_config():
    """Not a typo and not junk: it is placement for the multi-GPU/multi-node
    shape this is heading toward. Nothing reads it yet, so it warns -- but it
    must not be an error, and it must not be dropped from the schema."""
    data = _load()
    data["cameras"][0]["runtime"]["device"] = "cuda:1"
    warnings = validate_manifest(data)
    assert any("runtime.device" in w for w in warnings)
