from __future__ import annotations

from ai.src.pipelines.base import Pipeline, PipelineOutput, TrackedFrame


class TrackingBaselinePipeline(Pipeline):
    """Tracking only, for cameras without business rules yet. Raises no alerts.

    The camera's tracker runs in the business tier for every camera
    (perception/camera_tracker.py); this pipeline adds nothing on top. Its
    tracking metrics are the camera's own.
    """

    def on_frame(self, tracked: TrackedFrame) -> PipelineOutput:
        return PipelineOutput()
