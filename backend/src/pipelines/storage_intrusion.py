from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.src.common.paths import repo_root
from backend.src.common.timing import TimingCollector

ROOT = repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import stream_track as S  # noqa: E402
import warehouse  # noqa: E402
from face_identity import MatchDecision  # noqa: E402
from uae_tracker import Detection, Tracker  # noqa: E402

from backend.src.domain.models import CameraPipelineBindingSpec, CameraStreamSpec, FrameEnvelope
from backend.src.infra.local_media_store import LocalMediaStore
from backend.src.infra.sqlite_alert_store import SqliteAlertStore
from backend.src.pipelines.alert_clip_buffer import AlertClipBuffer, FinalizedAlertClip
from backend.src.runtime.shared_inference import SharedFaceIdentifier, SharedInferenceRegistry


DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
BOOTSTRAP_LOCK = threading.Lock()


def _parse_hhmm(value: str) -> int:
    hh, mm = value.split(":")
    return int(hh) * 60 + int(mm)


def _in_allowed_window(local_ts: datetime | None, access_schedule: dict[str, Any]) -> bool:
    if local_ts is None:
        return False
    windows = access_schedule.get("allowed_windows", [])
    if not windows:
        return False
    day_name = DAY_NAMES[local_ts.weekday()]
    minute_of_day = local_ts.hour * 60 + local_ts.minute
    for window in windows:
        if day_name not in window.get("days", DAY_NAMES):
            continue
        start_min = _parse_hhmm(window["start"])
        end_min = _parse_hhmm(window["end"])
        if start_min <= minute_of_day <= end_min:
            return True
    return False


@dataclass
class PendingEntry:
    track_id: int
    event_frame_idx: int
    event_ts_utc: datetime
    event_local_ts: datetime | None
    snapshot_frame: FrameEnvelope
    resolved: bool = False


class EntryFaceWindow:
    def __init__(self, start_frame: int):
        self.start_frame = start_frame
        self.end_frame: int | None = None
        self.best: tuple[tuple[float, float], MatchDecision] | None = None

    def is_collecting(self, frame_idx: int) -> bool:
        return self.end_frame is None or frame_idx <= self.end_frame

    def consider(self, decision: MatchDecision):
        if decision.observation is None:
            return
        key = (
            float(decision.top1.similarity if decision.top1 is not None else 0.0),
            float(decision.observation.detector_score),
        )
        if self.best is None or key > self.best[0]:
            self.best = (key, decision)

    def resolved_decision(self) -> MatchDecision | None:
        return None if self.best is None else self.best[1]


class StorageIntrusionPipeline:
    def __init__(
        self,
        spec: CameraStreamSpec,
        binding: CameraPipelineBindingSpec,
        alert_store: SqliteAlertStore,
        media_store: LocalMediaStore,
        run_id: str,
        gallery_db_path: str,
        cpu_only: bool = True,
        shared_inference: SharedInferenceRegistry | None = None,
    ):
        self.spec = spec
        self.binding = binding
        self.alert_store = alert_store
        self.media_store = media_store
        self.run_id = run_id
        self.gallery_db_path = gallery_db_path
        self.cpu_only = cpu_only
        self.shared_inference = shared_inference

        self.params = binding.params
        alert_clip_cfg = dict(self.params.get("alert_clip", {}))
        self.clip_buffer = AlertClipBuffer(
            spec.target_fps,
            pre_seconds=float(alert_clip_cfg.get("pre_s", 15.0)),
            post_seconds=float(alert_clip_cfg.get("post_s", 15.0)),
        )
        self.det = None
        self.emb = None
        self.tracker = None
        self.monitor = None
        self.face_identifier = None
        self.entry_windows: dict[int, EntryFaceWindow] = {}
        self.pending_entries: dict[int, PendingEntry] = {}
        self.metrics = {
            "frames_processed": 0,
            "detections_total": 0,
            "alerts_emitted": 0,
            "entries_seen": 0,
            "unknown_alerts": 0,
            "staff_outside_schedule_alerts": 0,
            "pose_batch_size_last": 1,
            "pose_batch_size_sum": 0,
            "pose_batch_size_max": 1,
            "pose_batch_size_avg": 0.0,
            "pose_queue_wait_ms_last": 0.0,
            "pose_batch_wait_ms_last": 0.0,
            "bootstrap_ms": 0.0,
            "providers": {},
        }
        self.timing = TimingCollector()

        zone_config_path = self.params["zone_config_path"]
        zone_asset_key = self.params["zone_asset_key"]
        zone_data = json.loads(Path(zone_config_path).read_text())
        self.zone_cfg = zone_data[zone_asset_key]
        self.access_schedule = dict(self.params.get("access_schedule", {}))
        self.post_entry_frames = max(
            1,
            round(
                float(self.params.get("face_capture_window", {}).get("end_after_inside_s", 1.0))
                * self.spec.target_fps
            ),
        )

    def _bootstrap(self, frame: FrameEnvelope):
        pose_model = self.spec.perception_profile.get("person_detector", {}).get(
            "model_path",
            str(ROOT / "models/yolo26m-pose.onnx"),
        )
        reid_model = self.spec.perception_profile.get("person_tracker", {}).get(
            "reid_model_path",
            str(ROOT / "models/osnet_x0_25_msmt17.onnx"),
        )
        conf = float(
            self.spec.perception_profile.get("person_detector", {}).get("conf_thres", 0.45)
        )
        S.REID_MODEL = reid_model
        S.ORT_PROVIDERS = ["CPUExecutionProvider"] if self.cpu_only else None
        if self.shared_inference is not None:
            self.det = self.shared_inference.get_pose(pose_model)
        with BOOTSTRAP_LOCK:
            if self.det is None:
                S.MODEL = pose_model
                S.CONF = conf
                self.det = S.Detector()
            self.emb = S.Embedder()

        cfg = warehouse.Config(fps=self.spec.target_fps)
        cfg.person_conf = conf
        if "entry_confirm_seconds" in self.params:
            cfg.entry_confirmation_s = float(self.params["entry_confirm_seconds"])
            cfg.__post_init__()
        if "min_box_h_frac" in self.zone_cfg:
            cfg.min_box_h_frac = float(self.zone_cfg["min_box_h_frac"])

        h, w = frame.image_bgr.shape[:2]
        self.tracker = Tracker(img_h=h, img_w=w, fps=self.spec.target_fps)
        self.monitor = warehouse.ZoneMonitor(self.zone_cfg["zone_norm"], w, h, cfg)

        face_cfg = self.spec.perception_profile.get("face_identity", {})
        if face_cfg.get("enabled", False):
            if self.shared_inference is not None:
                self.face_identifier = SharedFaceIdentifier(
                    face_detector=self.shared_inference.get_face_detector(
                        str(ROOT / "models/yolov6s_face.onnx")
                    ),
                    arcface=self.shared_inference.get_arcface(
                        str(ROOT / "models/ms1mv3_arcface_r18.onnx")
                    ),
                    gallery_db_path=self.gallery_db_path,
                    face_conf=float(face_cfg.get("face_conf", 0.20)),
                    crop_expand=float(face_cfg.get("crop_expand", 0.12)),
                    iou=float(face_cfg.get("iou_thres", 0.45)),
                    match_threshold=float(face_cfg.get("match_threshold", 0.30)),
                    match_margin=float(face_cfg.get("match_margin", 0.10)),
                )
            else:
                from face_identity import FaceIdentifier  # noqa: E402

                providers = ["CPUExecutionProvider"] if self.cpu_only else None
                self.face_identifier = FaceIdentifier(
                    str(ROOT / "models/yolov6s_face.onnx"),
                    str(ROOT / "models/ms1mv3_arcface_r18.onnx"),
                    self.gallery_db_path,
                    providers=providers,
                    prefer_coreml=not self.cpu_only,
                    match_threshold=float(face_cfg.get("match_threshold", 0.30)),
                    match_margin=float(face_cfg.get("match_margin", 0.10)),
                )
        face_detector_provider = None
        arcface_provider = None
        if self.face_identifier is not None:
            if self.shared_inference is not None:
                face_detector_provider = self.face_identifier.face_detector.provider
                arcface_provider = self.face_identifier.arcface.provider
            else:
                face_detector_provider = self.face_identifier.extractor.detector.sess.get_providers()[0]
                arcface_provider = self.face_identifier.embedder.sess.get_providers()[0]
        self.metrics["providers"] = {
            "pose_detector": getattr(self.det, "provider", "unknown"),
            "reid_embedder": getattr(self.emb, "provider", "unknown"),
            "face_detector": face_detector_provider,
            "arcface_embedder": arcface_provider,
        }

    def warmup(self, frame: FrameEnvelope):
        if self.det is not None:
            return
        with self.timing.track("bootstrap"):
            self._bootstrap(frame)
        self.metrics["bootstrap_ms"] = self.timing.stats["bootstrap"].total_s * 1000.0

    def _persist_ready_clips(self, clips: list[FinalizedAlertClip]):
        with self.timing.track("clip_persist_io"):
            for clip in clips:
                clip_path = self.media_store.save_clip(clip.alert_id, clip, self.spec.target_fps)
                self.alert_store.add_alert_media(
                    alert_id=clip.alert_id,
                    kind="clip",
                    path=clip_path,
                    mime_type="video/mp4",
                    started_at=clip.started_at.isoformat() if clip.started_at else None,
                    ended_at=clip.ended_at.isoformat() if clip.ended_at else None,
                    metadata=clip.metadata,
                )

    def finalize(self):
        self._persist_ready_clips(self.clip_buffer.flush_all())
        if self.shared_inference is not None and self.face_identifier is not None:
            self.face_identifier.close()

    def _finalize_entry(self, track_id: int, decision: MatchDecision | None):
        pending = self.pending_entries.get(track_id)
        if pending is None or pending.resolved:
            return 0
        pending.resolved = True

        is_unknown = True
        is_staff = False
        person_id = None
        label = "Unknown"
        similarity = None
        reason = "no_identity_decision"
        top1 = None
        top2 = None
        if decision is not None:
            is_unknown = decision.is_unknown
            person_id = decision.identity
            label = decision.display_name
            reason = decision.reason
            top1 = decision.top1.similarity if decision.top1 is not None else None
            top2 = decision.top2.similarity if decision.top2 is not None else None
            if decision.person is not None:
                is_staff = bool(decision.person.is_staff)
                similarity = float(decision.person.similarity)

        event_type = None
        reason_codes = [reason]
        if is_unknown:
            event_type = "unknown_entry"
            reason_codes.append("UNKNOWN_PERSON_ENTERED_STORAGE")
            self.metrics["unknown_alerts"] += 1
        elif is_staff and not _in_allowed_window(pending.event_local_ts, self.access_schedule):
            event_type = "staff_outside_schedule"
            reason_codes.append("STAFF_ENTERED_OUTSIDE_ALLOWED_WINDOW")
            self.metrics["staff_outside_schedule_alerts"] += 1

        if event_type is None:
            self.pending_entries.pop(track_id, None)
            self.entry_windows.pop(track_id, None)
            return 0

        metadata = {
            "track_id": track_id,
            "camera_type": self.spec.camera_type,
            "event_frame_idx": pending.event_frame_idx,
            "event_source_time_s": round(
                pending.snapshot_frame.source_time_s,
                3,
            ),
            "identity_reason": reason,
            "top1_similarity": top1,
            "top2_similarity": top2,
            "company_id": self.spec.company_id,
            "store_id": self.spec.store_id,
        }
        with self.timing.track("alert_io"):
            alert_id = self.alert_store.create_alert(
                store_id=self.spec.store_id,
                camera_id=self.spec.camera_id,
                run_id=self.run_id,
                pipeline_type=self.binding.pipeline_id,
                event_type=event_type,
                severity="warning",
                opened_at=pending.event_ts_utc.isoformat(),
                identity_person_id=person_id,
                identity_label=label,
                identity_score=similarity,
                reason_codes=reason_codes,
                metadata=metadata,
            )
            snapshot_path = self.media_store.save_snapshot(alert_id, pending.snapshot_frame)
            self.alert_store.add_alert_media(
                alert_id=alert_id,
                kind="snapshot",
                path=snapshot_path,
                mime_type="image/jpeg",
                started_at=pending.event_ts_utc.isoformat(),
                metadata={"track_id": track_id},
            )
            self.clip_buffer.open_clip(
                alert_id,
                event_frame_idx=pending.event_frame_idx,
                metadata={"camera_id": self.spec.camera_id, "event_type": event_type, "track_id": track_id},
            )
        self.metrics["alerts_emitted"] += 1
        self.pending_entries.pop(track_id, None)
        self.entry_windows.pop(track_id, None)
        return 1

    def process_frame(self, frame: FrameEnvelope):
        if self.det is None:
            self.warmup(frame)
        with self.timing.track("clip_buffer_push"):
            self.clip_buffer.push(frame)

        with self.timing.track("detect_pose"):
            if self.shared_inference is not None:
                pose_result = self.det.detect(frame.image_bgr, conf=float(
                    self.spec.perception_profile.get("person_detector", {}).get("conf_thres", 0.45)
                ))
                boxes, kpts = pose_result.boxes, pose_result.kpts
                pose_profile = pose_result.profile
            else:
                boxes, kpts = self.det(frame.image_bgr)
                pose_profile = self.det.last_profile
        self.timing.merge_dict(
            {
                "detect_pose::letterbox": float(pose_profile.get("letterbox_s", 0.0)),
                "detect_pose::preprocess": float(pose_profile.get("preprocess_s", 0.0)),
                "detect_pose::infer": float(pose_profile.get("infer_s", 0.0)),
                "detect_pose::decode": float(pose_profile.get("decode_s", 0.0)),
                "detect_pose::queue_wait": float(pose_profile.get("queue_wait_s", 0.0)),
                "detect_pose::batch_wait": float(pose_profile.get("batch_wait_s", 0.0)),
            }
        )
        self.metrics["pose_batch_size_last"] = int(pose_profile.get("batch_size", 1))
        self.metrics["pose_batch_size_sum"] += self.metrics["pose_batch_size_last"]
        self.metrics["pose_batch_size_max"] = max(
            int(self.metrics["pose_batch_size_max"]),
            self.metrics["pose_batch_size_last"],
        )
        self.metrics["pose_batch_size_avg"] = round(
            self.metrics["pose_batch_size_sum"] / max(1, self.metrics["frames_processed"] + 1),
            3,
        )
        self.metrics["pose_queue_wait_ms_last"] = round(float(pose_profile.get("queue_wait_s", 0.0)) * 1000.0, 3)
        self.metrics["pose_batch_wait_ms_last"] = round(float(pose_profile.get("batch_wait_s", 0.0)) * 1000.0, 3)
        min_h = self.monitor.cfg.min_box_h_frac * frame.image_bgr.shape[0]
        if len(boxes) and min_h > 0:
            keep = (boxes[:, 3] - boxes[:, 1]) >= min_h
            boxes = boxes[keep]
            kpts = [k for k, keep_one in zip(kpts, keep) if keep_one]

        with self.timing.track("embed_reid"):
            feats = self.emb(frame.image_bgr, boxes)
        self.timing.merge_dict(
            {
                "embed_reid::crop_resize": float(self.emb.last_profile.get("crop_resize_s", 0.0)),
                "embed_reid::pack_norm": float(self.emb.last_profile.get("pack_norm_s", 0.0)),
                "embed_reid::infer": float(self.emb.last_profile.get("infer_s", 0.0)),
                "embed_reid::post_concat": float(self.emb.last_profile.get("post_concat_s", 0.0)),
                "embed_reid::l2norm": float(self.emb.last_profile.get("l2norm_s", 0.0)),
            }
        )
        with self.timing.track("tracker_prepare_detections"):
            dets = [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i]) for i in range(len(boxes))]
        with self.timing.track("tracker_update"):
            live, removed = self.tracker.update(dets, frame.frame_idx)
        with self.timing.track("zone_monitor_drop"):
            self.monitor.drop(removed, frame.frame_idx)

        alerts_from_finalize = 0
        with self.timing.track("finalize_removed_tracks"):
            for tid in removed:
                window = self.entry_windows.get(tid)
                decision = window.resolved_decision() if window is not None else None
                alerts_from_finalize += self._finalize_entry(tid, decision)

        events_before = len(self.monitor.events)
        with self.timing.track("zone_monitor_update"):
            items = self.monitor.update(
                [(t.track_id, t.ltrb, t.kpts, source == "detected") for t, source in live],
                frame.frame_idx,
            )
        state_of = {tid: st for tid, _ltrb, _kp, st, _pt, _streak in items}

        with self.timing.track("entry_window_state_update"):
            for tid, st in state_of.items():
                if self.face_identifier is None:
                    continue
                if st == warehouse.PENDING_IN:
                    self.entry_windows.setdefault(tid, EntryFaceWindow(frame.frame_idx))
                elif st == warehouse.OUTSIDE and tid in self.pending_entries:
                    window = self.entry_windows.get(tid)
                    decision = window.resolved_decision() if window is not None else None
                    alerts_from_finalize += self._finalize_entry(tid, decision)

        if self.face_identifier is not None:
            with self.timing.track("face_identity"):
                identify_calls = 0
                for tid, ltrb, _kp, st, _pt, _streak in items:
                    window = self.entry_windows.get(tid)
                    if window is None or not window.is_collecting(frame.frame_idx):
                        continue
                    if st not in (warehouse.PENDING_IN, warehouse.INSIDE):
                        continue
                    identify_calls += 1
                    decision = self.face_identifier.identify(
                        frame.image_bgr,
                        ltrb,
                        store_id=self.spec.store_id,
                        company_id=self.spec.company_id,
                    )
                    window.consider(decision)
                self.timing.merge_dict({"face_identity::calls": float(identify_calls)})

        new_events = self.monitor.events[events_before:]
        with self.timing.track("process_zone_events"):
            for event in new_events:
                if event["event"] != "ZONE_ENTRY":
                    continue
                tid = int(event["track_id"])
                self.metrics["entries_seen"] += 1
                self.pending_entries[tid] = PendingEntry(
                    track_id=tid,
                    event_frame_idx=frame.frame_idx,
                    event_ts_utc=frame.ts_utc,
                    event_local_ts=frame.simulated_local_ts,
                    snapshot_frame=frame,
                )
                if self.face_identifier is None:
                    alerts_from_finalize += self._finalize_entry(tid, None)
                    continue
                window = self.entry_windows.setdefault(tid, EntryFaceWindow(frame.frame_idx))
                window.end_frame = frame.frame_idx + self.post_entry_frames

        if self.face_identifier is not None:
            with self.timing.track("finalize_expired_entry_windows"):
                for tid, window in list(self.entry_windows.items()):
                    st = state_of.get(tid)
                    if window.end_frame is None:
                        continue
                    if st in (warehouse.PENDING_IN, warehouse.INSIDE) and frame.frame_idx <= window.end_frame:
                        continue
                    alerts_from_finalize += self._finalize_entry(tid, window.resolved_decision())

        self.metrics["frames_processed"] += 1
        self.metrics["detections_total"] += int(len(boxes))
        with self.timing.track("clip_buffer_collect"):
            ready_clips = self.clip_buffer.collect_ready(frame.frame_idx)
        self._persist_ready_clips(ready_clips)
        self.metrics["timing"] = self.timing.as_dict()
        return {
            "alerts_emitted": alerts_from_finalize,
            "events_emitted": len(new_events),
            "live_tracks": len(items),
            "removed_tracks": len(removed),
        }
