"""Entrance: alert when someone walking IN matches the store's watchlist.

Production port of ai/demo/demo_entrance_face.py, with the same perception and
the same decisions, moved onto the split architecture:

    person detect -> track -> face request for every inbound track, every frame
                       |                        |
                 pelvis anchor         best face kept per track
                       |               (face tier answers async)
                  zone + confirm                |
                       |                        |
                   ZONE_ENTRY  -----------------+--> watchlist match?  -> alert
                                                                  no match -> counted only

What each rule is for, all measured in the demo on real entrance footage:

  * Faces are collected from the moment a track appears OUTSIDE, not once it
    is inside. The best view of someone entering happens before the zone
    confirms them; once inside they have turned away.
  * A track never seen OUTSIDE is walking out, not in, and gets no face work.
  * A face counts only if it is at least `min_face_h` px tall (junk boxes came
    back 10-29 px at scores up to 0.49, real faces 52-102 px) and sits within
    the top `face_top_frac` of the person box (confidence could not separate
    real faces, 0.71-0.80, from fabric and shelving, 0.74).
  * Entry confirmation is rounded UP to whole frames: 0.5 s at 5 fps must be
    3 frames, not round()'s 2.

The match itself happens in the face tier, against the non-staff persons of
this store (the watchlist; see match_is_staff in run_split_pipeline), with the
camera's own face_identity.match_threshold. Only answers whose face passes the
quality rules above are trusted. A visit writes to the alert store only when
one of them matched: an alert with the snapshot at entry, the matched face and
an evidence clip. A visit with no match is counted and logged with its closest
similarity, and nothing else -- the dashboard is for incidents, not footfall.

Caveat that has to travel with this pipeline: measured 2026-08-29
(ai/docs/FUTURE_BUSINESS_CASES.md), ArcFace embeddings from THIS camera
angle do not separate identities -- same-person and different-person
similarities overlap completely. The threshold therefore trades missed
shoplifters against strangers flagged as one, and there is no value that
avoids both. The per-visit log line exists to choose it from data.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ai.src.common.timing import TimingCollector
from ai.src.domain.models import CameraPipelineBindingSpec, CameraStreamSpec, FrameEnvelope
from ai.src.perception import warehouse
from ai.src.perception.uae_tracker import Detection, Tracker, camera_config
from ai.src.pipelines.alert_clip_buffer import AlertClipBuffer, persist_clip
from ai.src.pipelines.track_quality import record_track_quality

logger = logging.getLogger(__name__)


def _tracker_overrides(spec) -> dict:
    block = dict(spec.perception_profile.get("person_tracker", {}))
    for not_a_threshold in ("device", "reid_model_path"):
        block.pop(not_a_threshold, None)
    return block


class BestFace:
    """What one inbound track's face answers amounted to so far."""

    def __init__(self, min_face_h: float, face_top_frac: float):
        self.min_face_h = min_face_h
        self.face_top_frac = face_top_frac
        self.best: tuple[float, Any] | None = None    # (detector score, FaceObservation)
        self.match: tuple[float, Any, Any] | None = None  # (similarity, PersonMatch, obs)
        self.closest = None                           # highest top1 similarity, matched or not
        self.seen = 0
        self.rejected_small = 0
        self.rejected_position = 0

    def consider(self, decision) -> None:
        observation = getattr(decision, "observation", None)
        if observation is None:
            return
        self.seen += 1
        fx1, fy1, fx2, fy2 = (float(v) for v in observation.face_box_xyxy)
        px1, py1, px2, py2 = (float(v) for v in observation.person_box_xyxy)
        # Both boxes are in the coordinates of the patch the request shipped,
        # at original pixel scale, so the ratio and the height are comparable
        # to what the demo measured on full frames.
        rel = ((fy1 + fy2) / 2.0 - py1) / max(1e-6, py2 - py1)
        if rel > self.face_top_frac:
            self.rejected_position += 1
            return
        if fy2 - fy1 < self.min_face_h:
            self.rejected_small += 1
            return
        score = float(observation.detector_score)
        if self.best is None or score > self.best[0]:
            self.best = (score, observation)
        top1 = getattr(decision, "top1", None)
        if top1 is not None and (self.closest is None or top1.similarity > self.closest):
            self.closest = float(top1.similarity)
        person = getattr(decision, "person", None)
        # person is set only when the face tier's threshold and margin both
        # passed; is_staff is re-checked so a gallery searched without the
        # watchlist scope can never turn a staff member into a shoplifter.
        if person is not None and not person.is_staff:
            if self.match is None or person.similarity > self.match[0]:
                self.match = (float(person.similarity), person, observation)


@dataclass
class PendingVisit:
    track_id: int
    event_frame_idx: int
    event_ts_utc: Any
    snapshot_frame: FrameEnvelope
    end_frame: int


class EntranceFaceCapturePipeline:
    def __init__(
        self,
        spec: CameraStreamSpec,
        binding: CameraPipelineBindingSpec,
        alert_store,
        media_store,
        run_id: str,
    ):
        self.spec = spec
        self.binding = binding
        self.alert_store = alert_store
        self.media_store = media_store
        self.run_id = run_id
        self.params = binding.params
        self.timing = TimingCollector()

        zone_data = json.loads(Path(self.params["zone_config_path"]).read_text())
        self.zone_cfg = zone_data[self.params.get("zone_asset_key", "entrance")]
        self.entry_confirm_s = float(self.params.get("entry_confirm_seconds", 0.5))
        self.min_face_h = float(self.params.get("min_face_h", 32))
        self.face_top_frac = float(self.params.get("face_top_frac", 0.35))
        # How long after ZONE_ENTRY a visit waits for face answers still in
        # flight. Face requests are fire-and-forget, so the last frames before
        # entry -- often the frontal ones -- can come back after the event.
        self.post_entry_frames = max(1, math.ceil(
            float(self.params.get("face_capture_window", {}).get("end_after_inside_s", 1.0))
            * spec.target_fps))
        clip_cfg = dict(self.params.get("alert_clip", {}))
        self.clip_buffer = AlertClipBuffer(
            spec.target_fps, pre_seconds=float(clip_cfg.get("pre_s", 15.0)),
            post_seconds=float(clip_cfg.get("post_s", 15.0)),
            keep_images=getattr(media_store, "needs_clip_frames", True))

        self.tracker = None
        self.monitor = None
        self._business_ready = False
        self.came_from_outside: set[int] = set()
        self.born_inside: set[int] = set()
        self.faces: dict[int, BestFace] = {}
        self.pending: dict[int, PendingVisit] = {}
        self.visited: set[int] = set()
        self.metrics = {
            "frames_processed": 0,
            "detections_total": 0,
            "alerts_emitted": 0,
            "visits_confirmed": 0,
            "visits_with_face": 0,
            "watchlist_alerts": 0,
            "entries_skipped_outbound": 0,
            "face_requests": 0,
            "face_answers": 0,
            "face_identification_failures": 0,
        }

    def _bootstrap_business(self, frame: FrameEnvelope) -> None:
        cfg = warehouse.Config(fps=self.spec.target_fps)
        cfg.person_conf = float(
            self.spec.perception_profile.get("person_detector", {}).get("conf_thres", 0.45))
        cfg.entry_confirmation_s = self.entry_confirm_s
        cfg.__post_init__()
        # Up, not round(): see the module docstring.
        cfg.entry_frames = max(1, math.ceil(self.entry_confirm_s * cfg.fps))
        # Off unless the zone file says otherwise. Config's 0.14 was measured
        # on storage_1; this doorway renders people at a different size.
        cfg.min_box_h_frac = float(self.zone_cfg.get("min_box_h_frac", 0.0))
        h, w = frame.image_bgr.shape[:2]
        self.tracker = Tracker(
            img_h=h, img_w=w, fps=self.spec.target_fps,
            cfg=camera_config(_tracker_overrides(self.spec), camera_id=self.spec.camera_id))
        self.monitor = warehouse.ZoneMonitor(self.zone_cfg["zone_norm"], w, h, cfg)
        self._business_ready = True

    def handle_perceived(self, frame: FrameEnvelope, boxes, kpts, feats):
        """Returns (result, face_targets), the intrusion pipelines' contract:
        face_targets are (track_id, ltrb, store_id, company_id) for the
        business tier to send to the face tier."""
        if not self._business_ready:
            self._bootstrap_business(frame)
        self.clip_buffer.push(frame)

        min_h = self.monitor.cfg.min_box_h_frac * frame.image_bgr.shape[0]
        if len(boxes) and min_h > 0:
            keep = (boxes[:, 3] - boxes[:, 1]) >= min_h
            boxes = boxes[keep]
            kpts = [k for k, m in zip(kpts, keep) if m]
            feats = feats[keep] if len(feats) else feats

        dets = [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i]) for i in range(len(boxes))]
        live, removed = self.tracker.update(dets, frame.frame_idx)
        record_track_quality(self.metrics, self.tracker, live)
        self.monitor.drop(removed, frame.frame_idx)

        emitted = 0
        # A track that ends while its visit waits for late answers is
        # recorded with what it has; nothing more can arrive for it.
        for tid in removed:
            emitted += self._finish_visit(tid)
            self.faces.pop(tid, None)
            self.came_from_outside.discard(tid)
            self.born_inside.discard(tid)
            self.visited.discard(tid)

        events_before = len(self.monitor.events)
        items = self.monitor.update(
            [(t.track_id, t.ltrb, t.kpts, source == "detected") for t, source in live],
            frame.frame_idx)

        face_targets = []
        for tid, ltrb, _k, st, _pt, _streak in items:
            if st == warehouse.OUTSIDE:
                self.came_from_outside.add(tid)
            elif tid not in self.came_from_outside:
                self.born_inside.add(tid)
                continue
            if tid in self.visited:
                continue   # recorded already; no reason to keep asking
            self.faces.setdefault(tid, BestFace(self.min_face_h, self.face_top_frac))
            face_targets.append((tid, ltrb, self.spec.store_id, self.spec.company_id))
        self.metrics["face_requests"] += len(face_targets)

        for event in self.monitor.events[events_before:]:
            if event["event"] != "ZONE_ENTRY":
                continue
            tid = int(event["track_id"])
            if tid not in self.came_from_outside:
                self.metrics["entries_skipped_outbound"] += 1
                continue
            self.pending[tid] = PendingVisit(
                track_id=tid,
                event_frame_idx=frame.frame_idx,
                event_ts_utc=frame.ts_utc,
                # A copy: the frame is a view into a ring slot the decoder will
                # overwrite before the visit window closes.
                snapshot_frame=dataclasses.replace(frame, image_bgr=frame.image_bgr.copy()),
                end_frame=frame.frame_idx + self.post_entry_frames,
            )
            # No new requests after entry; answers already in flight are still
            # taken until end_frame.
            self.visited.add(tid)

        for tid, visit in list(self.pending.items()):
            if frame.frame_idx >= visit.end_frame:
                emitted += self._finish_visit(tid)

        self._persist_ready_clips(self.clip_buffer.collect_ready(frame.frame_idx))
        self.metrics["frames_processed"] += 1
        self.metrics["detections_total"] += int(len(boxes))
        return {
            "alerts_emitted": emitted,
            "events_emitted": len(self.monitor.events) - events_before,
            "live_tracks": len(items),
            "removed_tracks": len(removed),
        }, face_targets

    def handle_face_answer(self, track_id: int, decision) -> None:
        self.metrics["face_answers"] += 1
        if decision is None:
            self.metrics["face_identification_failures"] += 1
            return
        window = self.faces.get(int(track_id))
        if window is not None:
            window.consider(decision)

    def _persist_ready_clips(self, clips) -> None:
        for clip in clips:
            persist_clip(self.media_store, self.alert_store, clip, self.spec.target_fps)

    def _finish_visit(self, track_id: int) -> int:
        visit = self.pending.pop(track_id, None)
        if visit is None:
            return 0
        window = self.faces.get(track_id)
        self.metrics["visits_confirmed"] += 1
        if window is not None and window.best is not None:
            self.metrics["visits_with_face"] += 1
        logger.info(
            "%s visit track=%d at %.1fs: faces=%d usable_face=%s closest_watchlist=%s matched=%s",
            self.spec.camera_id, track_id, visit.snapshot_frame.source_time_s,
            window.seen if window else 0,
            None if window is None or window.best is None else round(window.best[0], 3),
            None if window is None or window.closest is None else round(window.closest, 3),
            None if window is None or window.match is None else window.match[1].person_id)
        if window is None or window.match is None:
            return 0

        similarity, person, observation = window.match
        metadata = {
            "track_id": track_id,
            "camera_type": self.spec.camera_type,
            "event_frame_idx": visit.event_frame_idx,
            "event_source_path": visit.snapshot_frame.source_path,
            "event_source_time_s": round(visit.snapshot_frame.source_time_s, 3),
            "face_detector_score": round(float(observation.detector_score), 3),
            "faces_seen": window.seen,
            "faces_rejected_small": window.rejected_small,
            "faces_rejected_position": window.rejected_position,
            "store_id": self.spec.store_id,
            "company_id": self.spec.company_id,
        }
        alert_id = self.alert_store.create_alert(
            store_id=self.spec.store_id,
            camera_id=self.spec.camera_id,
            run_id=self.run_id,
            pipeline_type=self.binding.pipeline_id,
            event_type="watchlist_match",
            severity="warning",
            opened_at=visit.event_ts_utc.isoformat(),
            identity_person_id=person.person_id,
            identity_label=person.display_name,
            identity_score=similarity,
            reason_codes=["ZONE_ENTRY_CONFIRMED", "WATCHLIST_FACE_MATCHED"],
            metadata=metadata,
        )
        snapshot_path = self.media_store.save_snapshot(alert_id, visit.snapshot_frame)
        self.alert_store.add_alert_media(
            alert_id=alert_id, kind="snapshot", path=snapshot_path, mime_type="image/jpeg",
            started_at=visit.event_ts_utc.isoformat(), metadata={"track_id": track_id})
        face_path = self.media_store.save_image(alert_id, "face.jpg", observation.chip_bgr)
        self.alert_store.add_alert_media(
            alert_id=alert_id, kind="face", path=face_path, mime_type="image/jpeg",
            started_at=visit.event_ts_utc.isoformat(),
            metadata={"track_id": track_id, "similarity": round(similarity, 3)})
        self.clip_buffer.open_clip(
            alert_id, event_frame_idx=visit.event_frame_idx,
            metadata={"camera_id": self.spec.camera_id, "event_type": "watchlist_match",
                      "track_id": track_id})
        self.metrics["watchlist_alerts"] += 1
        self.metrics["alerts_emitted"] += 1
        return 1

    def finalize(self) -> int:
        emitted = sum(self._finish_visit(tid) for tid in list(self.pending))
        self._persist_ready_clips(self.clip_buffer.flush_all())
        return emitted
