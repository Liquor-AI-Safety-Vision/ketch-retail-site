from __future__ import annotations

from dataclasses import dataclass
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
