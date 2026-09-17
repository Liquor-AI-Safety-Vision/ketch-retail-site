from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import numpy as np


@dataclass
class FrameEnvelope:
    company_id: Optional[str]
    store_id: str
    camera_id: str
    camera_type: str
    frame_idx: int
    ts_utc: datetime
    simulated_local_ts: Optional[datetime]
    source_kind: str
    source_uri: str
    source_path: str
    image_bgr: np.ndarray
    source_frame_idx: int
    source_time_s: float
    loop_index: int


@dataclass
class FrameMeta:
    """Everything in FrameEnvelope except the pixels.

    A FrameEnvelope's image_bgr is the one field too large to put on a
    multiprocessing queue by value (pickling a 1080p array costs 35.6ms
    against 0.85ms for a slot index, measured in diag_ipc.py). The split
    architecture's decode tier keeps the pixels in a FrameRing and forwards
    this instead; the business tier reconstructs a full FrameEnvelope by
    pairing it with the frame it reads back out of the ring.
    """

    company_id: Optional[str]
    store_id: str
    camera_id: str
    camera_type: str
    frame_idx: int
    ts_utc: datetime
    simulated_local_ts: Optional[datetime]
    source_kind: str
    source_uri: str
    source_path: str
    source_frame_idx: int
    source_time_s: float
    loop_index: int

    @classmethod
    def from_envelope(cls, frame: "FrameEnvelope") -> "FrameMeta":
        return cls(
            company_id=frame.company_id, store_id=frame.store_id,
            camera_id=frame.camera_id, camera_type=frame.camera_type,
            frame_idx=frame.frame_idx, ts_utc=frame.ts_utc,
            simulated_local_ts=frame.simulated_local_ts,
            source_kind=frame.source_kind, source_uri=frame.source_uri,
            source_path=frame.source_path, source_frame_idx=frame.source_frame_idx,
            source_time_s=frame.source_time_s, loop_index=frame.loop_index,
        )

    def to_envelope(self, image_bgr: np.ndarray) -> "FrameEnvelope":
        return FrameEnvelope(
            company_id=self.company_id, store_id=self.store_id,
            camera_id=self.camera_id, camera_type=self.camera_type,
            frame_idx=self.frame_idx, ts_utc=self.ts_utc,
            simulated_local_ts=self.simulated_local_ts,
            source_kind=self.source_kind, source_uri=self.source_uri,
            source_path=self.source_path, image_bgr=image_bgr,
            source_frame_idx=self.source_frame_idx, source_time_s=self.source_time_s,
            loop_index=self.loop_index,
        )


@dataclass
class StreamSourceInfo:
    path: str
    native_fps: float
    frame_count: int
    duration_s: float
    width: int
    height: int


@dataclass
class CameraPipelineBindingSpec:
    pipeline_id: str
    enabled: bool
    params: dict[str, Any]


@dataclass
class CameraStreamSpec:
    company_id: Optional[str]
    store_id: str
    #: The store's human-readable name, for stores.display_name. Carried on
    #: every camera spec rather than passed apart because seed_store_topology
    #: only ever receives the merged spec list -- it has no manifest to look
    #: back at. Falls back to store_id, which is what the column held for
    #: every row written before this field existed.
    store_display_name: str
    store_timezone: str
    camera_id: str
    display_name: str
    camera_type: str
    source_kind: str
    source_uri: str
    source_path: str
    strategy: str
    start_s: float
    target_duration_s: float
    target_fps: float
    runtime_profile: dict[str, Any]
    perception_profile: dict[str, Any]
    simulation_profile: dict[str, Any]
    pipeline_bindings: list[CameraPipelineBindingSpec]
    raw_config: dict[str, Any]
    #: Files a video_file camera cycles through instead of source_path alone:
    #: [{"path": resolved path, "repeat": n}, ...]. Empty for a single file.
    #: See VirtualVideoStreamSource for how a pass through it is played.
    playlist: list[dict[str, Any]] = field(default_factory=list)

    @property
    def pipeline_ids(self) -> list[str]:
        return [binding.pipeline_id for binding in self.pipeline_bindings if binding.enabled]


@dataclass
class StaffSeedEntry:
    person_id: str
    display_name: str
    is_staff: bool
    company_id: Optional[str]
    store_id: Optional[str]
    video_path: str
    timestamp_s: float
    search_window_s: float
    crop_box_xyxy: tuple[int, int, int, int]
    metadata: dict[str, Any]
