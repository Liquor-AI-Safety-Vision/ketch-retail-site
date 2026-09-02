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

    Only `device` is left in this state, and only because it is deliberate:
    it is placement for the multi-GPU/multi-node shape this is heading toward.
    Every other inert key has since been deleted outright, because a warning
    on a key that can never mean anything just invites someone to wire it.
    """
    warnings = validate_manifest(_load())
    joined = "\n".join(warnings)
    assert "runtime.device" in joined, "device configures nothing and should be named"
    assert "person_tracker.match_thres" not in joined


def test_keys_that_named_behaviour_the_system_lacks_are_errors_not_warnings():
    """Deleted from the schema, not demoted to inert.

    Each of these was correctly spelled and present in all seven manifests,
    and each described something the system does not do. A warning would keep
    inviting the next person to wire it; an unknown-key error says it is not
    a setting at all. nms_thres is the clearest case -- the pose head is
    NMS-free, so there is no NMS anywhere to give a threshold to.
    """
    gone = {
        "manifest.runtime": ("drop_if_lagging", True),
        "manifest": ("mode", "local_dev"),
        "camera.perception.person_detector": ("nms_thres", 0.7),
    }
    for where, (key, value) in gone.items():
        data = _load()
        if where == "manifest":
            data[key] = value
        elif where == "manifest.runtime":
            data["runtime"][key] = value
        else:
            data["cameras"][0]["perception"]["person_detector"][key] = value
        try:
            validate_manifest(data)
        except ManifestError as e:
            assert key in str(e) and "unknown key" in str(e), str(e)
        else:
            raise AssertionError(f"{where}.{key} was accepted")

    # policies had exactly two members; the whole block goes with them.
    data = _load()
    data["policies"] = {"identity_gallery_scope": "company"}
    try:
        validate_manifest(data)
    except ManifestError as e:
        assert "policies" in str(e)
    else:
        raise AssertionError("policies was accepted")


def test_the_model_key_the_pipelines_read_is_the_key_the_schema_accepts():
    """The validator used to refuse the only spelling that worked.

    The pipelines read person_detector.model_path and
    person_tracker.reid_model_path. The schema declared pose_model and
    reid_model, so writing the working name was an "unknown key" error while
    the accepted name was consumed by nobody -- the exact silent-default
    failure this module exists to prevent, sitting inside it.
    """
    data = _load()
    cam = data["cameras"][0]
    cam["perception"]["person_detector"]["model_path"] = "/models/other-pose.onnx"
    cam["perception"]["person_tracker"]["reid_model_path"] = "/models/other-reid.onnx"
    warnings = validate_manifest(data)
    assert not [w for w in warnings if "model" in w], \
        "a model path that the pipelines read was reported as configuring nothing"

    for dead_spelling in ("pose_model", "reid_model"):
        data = _load()
        block = ("person_detector" if dead_spelling == "pose_model" else "person_tracker")
        data["cameras"][0]["perception"][block][dead_spelling] = "/x.onnx"
        try:
            validate_manifest(data)
        except ManifestError as e:
            assert dead_spelling in str(e)
        else:
            raise AssertionError(f"{dead_spelling} was accepted but nothing reads it")


def test_a_reid_model_path_reaches_the_bootstrap_and_not_the_tracker():
    """It lives in person_tracker but is a filename, not a tracker setting.

    _tracker_overrides has to drop it, or camera_config() looks for a
    REID_MODEL_PATH attribute on Config, does not find one, and raises -- so
    the one key that selects a ReID model would crash the camera that set it.
    """
    from backend.src.pipelines.storage_intrusion import _tracker_overrides

    class _Spec:
        perception_profile = {"person_tracker": {
            "device": "cuda:1",
            "reid_model_path": "/models/other-reid.onnx",
            "step_a_max_cost": 0.3,
        }}

    assert _tracker_overrides(_Spec()) == {"step_a_max_cost": 0.3}


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


def test_no_manifest_still_carries_match_thres():
    """Untraceable, unread in every commit, and above the different-person mark.

    0.65 has no counterpart in uae-main's defaults.py, and uae_tracker's own
    note beside STEP_B_EMBED_GATE puts different people above 0.5 cosine on
    this OSNet. Wiring that number into the association gate would have
    loosened tracking on no evidence, so the key is gone.
    """
    for path in MANIFESTS:
        assert '"match_thres"' not in path.read_text(), \
            f"{path.name} still sets match_thres"


def test_the_tracker_default_is_still_the_one_every_benchmark_ran():
    from backend.src.perception.uae_tracker import Config

    assert Config.STEP_A_MAX_COST == 0.25, (
        "0.25 is uae-main's default and the value behind every recorded "
        "benchmark; changing it invalidates them")


def test_tracker_tuning_is_per_camera_when_a_camera_asks_for_it():
    """Tracker always accepted a `cfg`; nothing ever passed one.

    So all sixteen cameras ran the class defaults. The mechanism exists now,
    addressed by the Config attribute's own name -- no alias, because the one
    alias that existed could not be traced to a meaning.
    """
    from backend.src.perception.uae_tracker import Config, camera_config

    strict = camera_config({"step_a_max_cost": 0.2}, camera_id="counter_1")
    loose = camera_config({"step_a_max_cost": 0.3}, camera_id="storage_2")

    assert strict.STEP_A_MAX_COST == 0.2
    assert loose.STEP_A_MAX_COST == 0.3
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
