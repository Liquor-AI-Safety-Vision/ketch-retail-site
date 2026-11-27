"""pipeline_id -> pipeline class, in one place, with unknown ids refused.

There used to be no registry. Two call sites -- the monolithic camera_runtime
and business_tier for the split path -- each carried the same if/elif chain,
and business_tier's docstring said "Same mapping camera_runtime.py uses",
which is a comment standing guard over a copy. Comments do not stop copies
drifting. (The monolithic path was retired once split became the only
deployed architecture; this module is why retiring it changed no behaviour.)

Worse than the duplication was how both chains ended: an unconditional
`return TrackingBaselinePipeline(...)`. Only three ids were ever matched by
name, so `whole_store_tracking_v1` reached the tracking pipeline by falling
through rather than by being chosen -- and so did every typo. A manifest
saying `storage_intrusion_v2` did not fail. It ran, decoded frames, tracked
people, reported healthy metrics, and never raised an alert, because the
pipeline that was supposed to raise them was never built. The camera looks
alive on every dashboard you have.

So the fallback is gone. An id nobody registered is a configuration error and
says so at bootstrap, which is the one moment somebody is still watching.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, types only
    from ai.src.domain.models import CameraPipelineBindingSpec, CameraStreamSpec


class UnknownPipelineError(ValueError):
    """A manifest named a pipeline_id that no pipeline claims."""


# The ids a manifest may bind a camera to. Adding a pipeline means adding a
# line here; that is the point -- the set is what makes a typo detectable.
COUNTER_CASH_THEFT = "counter_cash_theft_v1"
STORAGE_INTRUSION = "storage_intrusion_v1"
COLD_STORAGE_INTRUSION = "cold_storage_intrusion_v1"
WHOLE_STORE_TRACKING = "whole_store_tracking_v1"
ENTRANCE_FACE_CAPTURE = "entrance_face_capture_v1"

INTRUSION_IDS = frozenset({STORAGE_INTRUSION, COLD_STORAGE_INTRUSION})
#: Pipelines that ask the face tier to identify people. The driver starts a
#: face tier, and configures each camera's matching, for any camera bound to one.
FACE_TARGET_IDS = INTRUSION_IDS | {ENTRANCE_FACE_CAPTURE}
KNOWN_PIPELINE_IDS = frozenset(
    {COUNTER_CASH_THEFT, WHOLE_STORE_TRACKING} | FACE_TARGET_IDS
)


def check_pipeline_id(pipeline_id: str, *, camera_id: str | None = None) -> str:
    """Refuse an unregistered id, naming what was expected.

    Separate from build_pipeline so callers that only dispatch on the id --
    business_tier's per-frame handler picks a call signature, it does not
    construct anything -- can validate against the same set.
    """
    if pipeline_id in KNOWN_PIPELINE_IDS:
        return pipeline_id
    where = f" (camera {camera_id})" if camera_id else ""
    known = ", ".join(sorted(KNOWN_PIPELINE_IDS))
    raise UnknownPipelineError(
        f"unknown pipeline_id {pipeline_id!r}{where}. Known ids: {known}. "
        "This used to fall through to the tracking pipeline, so the camera ran "
        "normally and simply never alerted."
    )


def build_pipeline(
    spec: "CameraStreamSpec",
    binding: "CameraPipelineBindingSpec",
    *,
    alert_store: Any,
    media_store: Any,
    run_id: str,
):
    """Build the pipeline a binding names."""
    # Imported inside the function: these modules pull in the perception
    # stack, and the registry is imported by callers that only want
    # KNOWN_PIPELINE_IDS.
    from ai.src.pipelines.counter_cash_theft import CounterCashTheftPipeline
    from ai.src.pipelines.entrance_face_capture import EntranceFaceCapturePipeline
    from ai.src.pipelines.storage_intrusion import StorageIntrusionPipeline
    from ai.src.pipelines.tracking_baseline import TrackingBaselinePipeline

    pipeline_id = check_pipeline_id(binding.pipeline_id, camera_id=spec.camera_id)

    if pipeline_id == COUNTER_CASH_THEFT:
        return CounterCashTheftPipeline(spec, binding, alert_store, media_store, run_id)
    if pipeline_id == ENTRANCE_FACE_CAPTURE:
        return EntranceFaceCapturePipeline(spec, binding, alert_store, media_store, run_id)
    if pipeline_id in INTRUSION_IDS:
        return StorageIntrusionPipeline(spec, binding, alert_store, media_store, run_id)
    return TrackingBaselinePipeline(spec, binding)
