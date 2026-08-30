"""An unregistered pipeline_id must fail loudly, not fall through to tracking.

Both dispatch chains used to end in `return TrackingBaselinePipeline(...)`
with no condition. Only three ids were matched by name, so a manifest with a
typo -- `storage_intrusion_v2`, say -- built the tracking pipeline instead.
Nothing raised. The camera decoded, tracked, and reported healthy frame counts
and zero alerts, which is indistinguishable from a quiet shift. The one
pipeline meant to raise alerts was never constructed.

That is the failure this file exists to prevent, so the first test is the typo
itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.src.infra.local_config import camera_specs_from_manifest  # noqa: E402
from backend.src.pipelines.registry import (  # noqa: E402
    COUNTER_CASH_THEFT,
    INTRUSION_IDS,
    KNOWN_PIPELINE_IDS,
    UnknownPipelineError,
    check_pipeline_id,
)


def test_a_typo_in_pipeline_id_is_refused():
    try:
        check_pipeline_id("storage_intrusion_v2", camera_id="storage_1")
    except UnknownPipelineError as e:
        msg = str(e)
        assert "storage_intrusion_v2" in msg, "the bad id must be named"
        assert "storage_1" in msg, "the camera must be named"
        assert "storage_intrusion_v1" in msg, "the known ids must be listed"
    else:
        raise AssertionError(
            "an unknown pipeline_id was accepted -- it would silently become "
            "the tracking pipeline and the camera would never alert"
        )


def test_every_known_id_passes():
    for pid in KNOWN_PIPELINE_IDS:
        assert check_pipeline_id(pid) == pid


def test_whole_store_tracking_is_matched_by_name_not_by_fallback():
    """It used to reach its pipeline only by falling off the end of the chain.

    Which meant the id was never actually checked, and the one line that was
    supposed to handle it handled everything else too.
    """
    assert "whole_store_tracking_v1" in KNOWN_PIPELINE_IDS


def test_every_id_in_every_shipped_manifest_is_registered():
    """The registry is only useful if it covers what the manifests actually bind."""
    unknown = []
    for manifest in sorted((BACKEND / "config" / "stores").glob("*.json")):
        for spec in camera_specs_from_manifest(str(manifest)):
            for binding in spec.pipeline_bindings:
                if binding.pipeline_id not in KNOWN_PIPELINE_IDS:
                    unknown.append(f"{manifest.name}/{spec.camera_id}: "
                                   f"{binding.pipeline_id}")
    assert not unknown, "manifests bind pipeline ids the registry does not know:\n  " + \
        "\n  ".join(unknown)


def test_the_two_architectures_cannot_disagree_about_the_mapping():
    """camera_runtime and business_tier both go through build_pipeline now.

    They used to hold separate copies of the same if/elif chain, kept in step
    by a docstring saying "Same mapping camera_runtime.py uses". A comment does
    not stop a copy drifting, and a camera behaving differently depending on
    which architecture ran it is the exact bug that would follow.
    """
    import ast

    offenders = []
    for name in ("runtime/camera_runtime.py", "runtime/business_tier.py"):
        path = BACKEND / "src" / name
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            called = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if called.endswith("Pipeline"):
                offenders.append(f"{name}:{node.lineno} constructs {called} directly")
    assert not offenders, (
        "a pipeline is being built outside registry.build_pipeline:\n  "
        + "\n  ".join(offenders)
    )


def test_intrusion_ids_cover_both_storage_kinds():
    assert INTRUSION_IDS == {"storage_intrusion_v1", "cold_storage_intrusion_v1"}
    assert COUNTER_CASH_THEFT not in INTRUSION_IDS
