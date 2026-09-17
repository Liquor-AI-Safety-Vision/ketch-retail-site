from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from backend.src.domain.models import FrameEnvelope


@dataclass
class BufferedFrame:
    frame_idx: int
    ts_utc: datetime
    image_bgr: np.ndarray


@dataclass
class FinalizedAlertClip:
    alert_id: str
    frames: list[BufferedFrame]
    started_at: datetime | None
    ended_at: datetime | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingAlertClip:
    alert_id: str
    event_frame_idx: int
    end_frame_idx: int
    frames: list[BufferedFrame]
    metadata: dict[str, Any]


class AlertClipBuffer:
    """Decides a clip's time range: pre_seconds before the event to post_seconds after.

    keep_images=False keeps timestamps only. That is the mode for a camera whose
    stream is being recorded (see infra/recorded_clips.py): the clip is cut from
    the recording at native fps, so holding 15 s of full-resolution frames in
    RAM per camera -- hundreds of MB -- would buy nothing.
    """

    def __init__(self, fps: float, pre_seconds: float = 15.0, post_seconds: float = 15.0,
                 keep_images: bool = True):
        self.fps = float(fps)
        self.keep_images = keep_images
        self.pre_frames = max(1, int(round(pre_seconds * self.fps)))
        self.post_frames = max(0, int(round(post_seconds * self.fps)))
        self.history: deque[BufferedFrame] = deque(maxlen=self.pre_frames + 1)
        self.pending: list[PendingAlertClip] = []

    def push(self, frame: FrameEnvelope):
        self.history.append(
            BufferedFrame(
                frame_idx=frame.frame_idx,
                ts_utc=frame.ts_utc,
                image_bgr=frame.image_bgr.copy() if self.keep_images else None,
            )
        )

    def open_clip(self, alert_id: str, event_frame_idx: int, metadata: dict[str, Any] | None = None):
        seed_frames = [
            BufferedFrame(
                frame_idx=item.frame_idx,
                ts_utc=item.ts_utc,
                image_bgr=item.image_bgr.copy() if item.image_bgr is not None else None,
            )
            for item in self.history
        ]
        collector = PendingAlertClip(
            alert_id=alert_id,
            event_frame_idx=event_frame_idx,
            end_frame_idx=event_frame_idx + self.post_frames,
            frames=seed_frames,
            metadata=dict(metadata or {}),
        )
        self.pending.append(collector)

    def collect_ready(self, current_frame_idx: int) -> list[FinalizedAlertClip]:
        ready: list[FinalizedAlertClip] = []
        still_pending: list[PendingAlertClip] = []
        for clip in self.pending:
            if current_frame_idx > clip.frames[-1].frame_idx and self.history:
                newest = self.history[-1]
                if newest.frame_idx > clip.frames[-1].frame_idx:
                    clip.frames.append(
                        BufferedFrame(
                            frame_idx=newest.frame_idx,
                            ts_utc=newest.ts_utc,
                            image_bgr=(newest.image_bgr.copy()
                                       if newest.image_bgr is not None else None),
                        )
                    )
            if current_frame_idx >= clip.end_frame_idx:
                ready.append(self._finalize_one(clip))
            else:
                still_pending.append(clip)
        self.pending = still_pending
        return ready

    def flush_all(self) -> list[FinalizedAlertClip]:
        ready = [self._finalize_one(item) for item in self.pending]
        self.pending = []
        return ready

    @staticmethod
    def _finalize_one(clip: PendingAlertClip) -> FinalizedAlertClip:
        started_at = clip.frames[0].ts_utc if clip.frames else None
        ended_at = clip.frames[-1].ts_utc if clip.frames else None
        return FinalizedAlertClip(
            alert_id=clip.alert_id,
            frames=clip.frames,
            started_at=started_at,
            ended_at=ended_at,
            metadata=clip.metadata,
        )


def persist_clip(media_store, alert_store, clip: FinalizedAlertClip, fps: float) -> None:
    """Write a finished clip and record it against its alert.

    A media store that can produce clips asynchronously (submit_clip, i.e. one
    cutting them from a recording) gets the job and records the media row when
    the file exists; any other writes the buffered frames now. One helper so the
    three pipelines that raise alerts with clips cannot diverge on it.
    """
    def record(path: str) -> None:
        alert_store.add_alert_media(
            alert_id=clip.alert_id, kind="clip", path=path, mime_type="video/mp4",
            started_at=clip.started_at.isoformat() if clip.started_at else None,
            ended_at=clip.ended_at.isoformat() if clip.ended_at else None,
            metadata=clip.metadata)

    submit = getattr(media_store, "submit_clip", None)
    if submit is not None:
        submit(clip, record)
    else:
        record(media_store.save_clip(clip.alert_id, clip, fps))
