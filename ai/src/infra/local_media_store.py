from __future__ import annotations

from pathlib import Path

import cv2

from ai.src.domain.models import FrameEnvelope
from ai.src.pipelines.alert_clip_buffer import FinalizedAlertClip


class LocalMediaStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save_snapshot(self, alert_id: str, frame: FrameEnvelope) -> str:
        out_dir = self.root_dir / alert_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "snapshot.jpg"
        # cv2.imwrite returns False rather than raising -- an unwritable
        # directory, a full disk, an unsupported extension. Returning the
        # path regardless records evidence in the alert store that is not on
        # disk, and the alert then looks complete right up until someone
        # opens it.
        if not cv2.imwrite(str(out_path), frame.image_bgr):
            raise OSError(f"cv2.imwrite could not write the snapshot to {out_path}")
        return str(out_path)

    def save_image(self, alert_id: str, name: str, image_bgr) -> str:
        """Any still image that belongs to an alert, e.g. a face chip."""
        out_dir = self.root_dir / alert_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / name
        if not cv2.imwrite(str(out_path), image_bgr):
            raise OSError(f"cv2.imwrite could not write {out_path}")
        return str(out_path)

    def save_clip(self, alert_id: str, clip: FinalizedAlertClip, fps: float) -> str:
        if not clip.frames:
            raise ValueError("clip has no frames")
        out_dir = self.root_dir / alert_id
        out_dir.mkdir(parents=True, exist_ok=True)
        # Written as mp4v first (the only encoder OpenCV wheels reliably have),
        # then re-encoded to H.264: mp4v does not play in a browser.
        raw_path = out_dir / "evidence_mp4v.mp4"
        out_path = out_dir / "evidence.mp4"
        if clip.frames[0].image_bgr is None:
            raise ValueError("clip was buffered without frames; it has to be cut from a recording")
        h, w = clip.frames[0].image_bgr.shape[:2]
        writer = cv2.VideoWriter(str(raw_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        # A VideoWriter that failed to open still accepts write() calls and
        # still releases cleanly; it just produces nothing. Checking is the
        # only way to tell a written clip from a silently discarded one.
        if not writer.isOpened():
            writer.release()
            raise OSError(
                f"cv2.VideoWriter could not open {raw_path} for {w}x{h} at {fps} fps; "
                f"the mp4v codec may be unavailable in this OpenCV build")
        try:
            for item in clip.frames:
                writer.write(item.image_bgr)
        finally:
            writer.release()
        from ai.src.infra.recorded_clips import to_h264

        try:
            to_h264(raw_path, out_path)
        finally:
            raw_path.unlink(missing_ok=True)
        return str(out_path)
