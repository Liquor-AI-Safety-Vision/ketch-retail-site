"""The contract between the business tier and the pipelines.

Per frame, the business tier tracks people once for the camera
(perception/camera_tracker.py) and hands the result to every pipeline bound
to it:

    pipeline.on_frame(TrackedFrame) -> PipelineOutput

A pipeline that needs faces identified returns FaceTargets; the business tier
sends them to the face tier and delivers each answer to
pipeline.on_face_answer(). finalize() runs once when the stream ends and
returns how many alerts it raised doing so.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ai.src.domain.models import FrameEnvelope


@dataclass(frozen=True)
class FaceTarget:
    """A tracked person whose face the face tier should identify."""

    track_id: int
    ltrb: Any
    store_id: str | None
    company_id: str | None


@dataclass
class TrackedFrame:
    """One frame after the camera's tracker has seen it."""

    frame: FrameEnvelope
    #: (Track, source) from Tracker.update; source is "detected" or "predicted"
    live: list
    #: track ids that ended on this frame
    removed: list
    #: drawer detections for counter cameras, None when the drawer was not
    #: sampled on this frame (till_every_n)
    till_detections: list | None = None


@dataclass
class PipelineOutput:
    alerts_emitted: int = 0
    events_emitted: int = 0
    face_targets: list[FaceTarget] = field(default_factory=list)


class Pipeline:
    """Base class: the methods the business tier calls, with no-op defaults."""

    #: Boxes shorter than this fraction of the frame height are dropped before
    #: tracking. A camera with several pipelines uses the smallest value.
    min_box_h_frac: float = 0.0

    def __init__(self, spec, binding):
        self.spec = spec
        self.binding = binding
        self.metrics: dict[str, Any] = {}

    def on_frame(self, tracked: TrackedFrame) -> PipelineOutput:
        raise NotImplementedError

    def on_face_answer(self, track_id: int, decision) -> None:
        pass

    def finalize(self) -> int:
        return 0
