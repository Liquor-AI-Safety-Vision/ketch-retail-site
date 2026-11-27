"""One person tracker per camera, shared by every pipeline bound to it.

Pipelines used to build their own tracker each, so a camera with two
pipelines would have tracked everybody twice and given the same person two
different ids. Here the business tier tracks once per camera and hands the
same tracks to every pipeline (pipelines/base.py).
"""
from __future__ import annotations

from typing import Any

import numpy as np

from ai.src.perception.person_tracker import Detection, Tracker, camera_config


def tracker_overrides(spec) -> dict:
    """Per-camera tracker tuning out of the manifest's perception.person_tracker.

    `device` and `reid_model_path` live in the same block but are not tracker
    settings, so they are dropped rather than reaching camera_config(), which
    refuses names it does not recognise.
    """
    block = dict(spec.perception_profile.get("person_tracker", {}))
    for not_a_threshold in ("device", "reid_model_path"):
        block.pop(not_a_threshold, None)
    return block


class CameraTracker:
    """Filters a frame's detections and runs the camera's tracker over them.

    min_box_h_frac drops boxes shorter than that fraction of the frame height
    before tracking (people far away, or false boxes on shelving). Built
    lazily on the first frame, which gives the frame size.
    """

    def __init__(self, spec, *, min_box_h_frac: float = 0.0):
        self.spec = spec
        self.min_box_h_frac = float(min_box_h_frac)
        self.tracker: Tracker | None = None
        self.metrics: dict[str, Any] = {
            "frames_processed": 0,
            "detections_total": 0,
            "max_live_tracks": 0,
            "boxes_before_min_h": 0,
            "boxes_dropped_min_h": 0,
            "tracks_created": 0,
            "track_frames_total": 0,
            "track_lifetime_frames_avg": 0.0,
        }

    def update(self, frame_idx: int, frame_shape, boxes: np.ndarray, kpts: list, feats):
        """Returns (live, removed) from Tracker.update."""
        h, w = frame_shape[:2]
        if self.tracker is None:
            self.tracker = Tracker(
                img_h=h, img_w=w, fps=self.spec.target_fps,
                cfg=camera_config(tracker_overrides(self.spec), camera_id=self.spec.camera_id))

        min_h = self.min_box_h_frac * h
        if len(boxes) and min_h > 0:
            keep = (boxes[:, 3] - boxes[:, 1]) >= min_h
            self.metrics["boxes_before_min_h"] += int(len(boxes))
            self.metrics["boxes_dropped_min_h"] += int(len(boxes) - int(keep.sum()))
            boxes = boxes[keep]
            kpts = [k for k, keep_one in zip(kpts, keep) if keep_one]
            feats = feats[keep] if len(feats) else feats

        dets = [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i]) for i in range(len(boxes))]
        live, removed = self.tracker.update(dets, frame_idx)
        self._record(len(boxes), live)
        return live, removed

    def _record(self, n_detections: int, live: list) -> None:
        """Frame counts plus the two numbers that expose identity fragmentation.

        When re-identification starts failing, the tracker mints fresh ids for
        the same people: tracks_created climbs while the average lifetime
        drops, even though alert counts and frame rate still look fine.
        """
        m = self.metrics
        m["frames_processed"] += 1
        m["detections_total"] += int(n_detections)
        m["max_live_tracks"] = max(m["max_live_tracks"], len(live))
        created = int(getattr(self.tracker, "_next_id", 0))
        m["tracks_created"] = created
        m["track_frames_total"] += len(live)
        m["track_lifetime_frames_avg"] = (m["track_frames_total"] / created) if created else 0.0
