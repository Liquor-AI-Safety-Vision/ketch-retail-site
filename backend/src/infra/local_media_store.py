from __future__ import annotations

from pathlib import Path

import cv2

from backend.src.domain.models import FrameEnvelope
from backend.src.pipelines.alert_clip_buffer import FinalizedAlertClip


class LocalMediaStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save_snapshot(self, alert_id: str, frame: FrameEnvelope) -> str:
        out_dir = self.root_dir / alert_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "snapshot.jpg"
        cv2.imwrite(str(out_path), frame.image_bgr)
        return str(out_path)

    def save_clip(self, alert_id: str, clip: FinalizedAlertClip, fps: float) -> str:
        if not clip.frames:
            raise ValueError("clip has no frames")
        out_dir = self.root_dir / alert_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "evidence.mp4"
        h, w = clip.frames[0].image_bgr.shape[:2]
        writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        for item in clip.frames:
            writer.write(item.image_bgr)
        writer.release()
        return str(out_path)
