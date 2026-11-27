"""Raising an alert: the row, its snapshot, any extra images and its evidence clip.

Every pipeline that alerts does the same four things in the same order, so
they live here once:

    emitter.push(frame)                    # every frame, for the clip's pre-roll
    alert_id = emitter.emit(...)           # alert row + snapshot (+ images) + clip opened
    emitter.persist_ready(frame_idx)       # every frame: clips whose post-roll is complete
    emitter.flush()                        # end of stream: every clip still open
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ai.src.domain.models import FrameEnvelope
from ai.src.pipelines.alert_clip_buffer import AlertClipBuffer, persist_clip


class AlertEmitter:
    def __init__(self, spec, binding, alert_store, media_store, run_id: str):
        self.spec = spec
        self.binding = binding
        self.alert_store = alert_store
        self.media_store = media_store
        self.run_id = run_id
        clip_cfg = dict(binding.params.get("alert_clip", {}))
        self.clip_buffer = AlertClipBuffer(
            spec.target_fps,
            pre_seconds=float(clip_cfg.get("pre_s", 15.0)),
            post_seconds=float(clip_cfg.get("post_s", 15.0)),
            # A recorded camera has its clips cut from the recording, so the
            # buffer keeps timestamps only (see infra/recorded_clips.py).
            keep_images=getattr(media_store, "needs_clip_frames", True),
        )

    def push(self, frame: FrameEnvelope) -> None:
        self.clip_buffer.push(frame)

    def emit(
        self,
        *,
        event_type: str,
        severity: str,
        snapshot_frame: FrameEnvelope,
        opened_at: datetime,
        event_frame_idx: int,
        reason_codes: list[str],
        metadata: dict[str, Any],
        media_metadata: dict[str, Any],
        identity_person_id: str | None = None,
        identity_label: str | None = None,
        identity_score: float | None = None,
        images: list[tuple[str, str, Any, dict[str, Any]]] = (),
    ) -> str:
        """Write the alert and its snapshot, then start its clip.

        images: extra stills as (kind, file name, image_bgr, metadata), e.g.
        the matched face. media_metadata goes on the snapshot and the clip.
        """
        opened = opened_at.isoformat()
        alert_id = self.alert_store.create_alert(
            store_id=self.spec.store_id,
            camera_id=self.spec.camera_id,
            run_id=self.run_id,
            pipeline_type=self.binding.pipeline_id,
            event_type=event_type,
            severity=severity,
            opened_at=opened,
            identity_person_id=identity_person_id,
            identity_label=identity_label,
            identity_score=identity_score,
            reason_codes=reason_codes,
            metadata=metadata,
        )
        snapshot_path = self.media_store.save_snapshot(alert_id, snapshot_frame)
        self.alert_store.add_alert_media(
            alert_id=alert_id, kind="snapshot", path=snapshot_path, mime_type="image/jpeg",
            started_at=opened, metadata=dict(media_metadata))
        for kind, name, image_bgr, image_metadata in images:
            path = self.media_store.save_image(alert_id, name, image_bgr)
            self.alert_store.add_alert_media(
                alert_id=alert_id, kind=kind, path=path, mime_type="image/jpeg",
                started_at=opened, metadata=image_metadata)
        self.clip_buffer.open_clip(
            alert_id,
            event_frame_idx=event_frame_idx,
            metadata={"camera_id": self.spec.camera_id, "event_type": event_type, **media_metadata},
        )
        return alert_id

    def persist_ready(self, frame_idx: int) -> None:
        for clip in self.clip_buffer.collect_ready(frame_idx):
            persist_clip(self.media_store, self.alert_store, clip, self.spec.target_fps)

    def flush(self) -> None:
        for clip in self.clip_buffer.flush_all():
            persist_clip(self.media_store, self.alert_store, clip, self.spec.target_fps)
