from __future__ import annotations

import logging
import dataclasses
import json
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ai.src.common.timing import TimingCollector


from ai.src.perception import stream_track as S  # noqa: E402
from ai.src.perception import warehouse  # noqa: E402
from ai.src.perception.face_identity import MatchDecision  # noqa: E402
from ai.src.perception.uae_tracker import Detection, Tracker, camera_config  # noqa: E402

from ai.src.domain.models import CameraPipelineBindingSpec, CameraStreamSpec, FrameEnvelope
from ai.src.infra.local_media_store import LocalMediaStore
from ai.src.infra.sqlite_alert_store import SqliteAlertStore
from ai.src.pipelines.alert_clip_buffer import AlertClipBuffer, FinalizedAlertClip, persist_clip
from ai.src.pipelines.track_quality import record_track_quality
from ai.src.runtime.shared_inference import SharedFaceIdentifier, SharedInferenceRegistry
from ai.src.common.paths import model_path


def _tracker_overrides(spec) -> dict:
    """Per-camera tracker tuning out of the manifest.

    `device` and `reid_model_path` live in the same block but are not tracker
    settings -- one selects a provider, the other names a file -- so they are
    dropped here rather than reaching camera_config(), which refuses names it
    does not recognise.
    """
    block = dict(spec.perception_profile.get("person_tracker", {}))
    for not_a_threshold in ("device", "reid_model_path"):
        block.pop(not_a_threshold, None)
    return block

logger = logging.getLogger(__name__)


DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
BOOTSTRAP_LOCK = threading.Lock()


def _store_local_time(frame: FrameEnvelope, store_timezone: str) -> datetime:
    """The wall-clock time at the store when this frame was taken.

    The simulated clock when the manifest enables one, the real clock
    otherwise. This used to read simulated_local_ts alone, which is None
    whenever the simulation clock is off -- and _in_allowed_window(None) is
    False, so on a real clock every staff entry was "outside the allowed
    window" at every hour of the day.
    """
    if frame.simulated_local_ts is not None:
        return frame.simulated_local_ts
    from zoneinfo import ZoneInfo

    return frame.ts_utc.astimezone(ZoneInfo(store_timezone))


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
    # A window that crosses midnight is listed under the day it *starts* on,
    # which is how a shift roster reads: "Friday 22:00-06:00" is Friday
    # night, and its small hours fall on Saturday. So a Saturday 02:00 sample
    # has to be tested against Friday's windows, not Saturday's -- which
    # means the day of the sample is not enough to pick which windows apply.
    #
    # Getting this wrong is not a near miss. Checking `day_name in days`
    # first, before the wrap is considered, accepts the evening half and
    # rejects the morning half of the very same shift: staff on shift at
    # 02:00 read as intruders. An earlier version of this function did
    # exactly that while carrying a comment claiming otherwise.
    prev_day_name = DAY_NAMES[(local_ts.weekday() - 1) % 7]
    minute_of_day = local_ts.hour * 60 + local_ts.minute
    for window in windows:
        days = window.get("days", DAY_NAMES)
        start_min = _parse_hhmm(window["start"])
        end_min = _parse_hhmm(window["end"])
        if start_min <= end_min:
            if day_name in days and start_min <= minute_of_day <= end_min:
                return True
            continue
        # Crosses midnight: two halves, listed under two different calendar
        # days but belonging to one shift.
        if day_name in days and minute_of_day >= start_min:
            return True                      # tonight's evening half
        if prev_day_name in days and minute_of_day <= end_min:
            return True                      # last night's morning half
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

    def consider(self, decision: MatchDecision | None):
        # None means no identification happened at all -- the async face tier
        # answering a request that raised, so the business worker's count can
        # come down. Guarded here as well as at the call site: the call site
        # guard is what the current path relies on, but a method that
        # dereferences its argument unconditionally is one new caller away
        # from the same crash.
        if decision is None or decision.observation is None:
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
            keep_images=getattr(media_store, "needs_clip_frames", True),
        )
        self.det = None
        self.emb = None
        self.tracker = None
        self.monitor = None
        self.face_identifier = None
        self._business_ready = False
        # Whether identity checks apply to this camera at all is pure config,
        # independent of whether a face_identifier object has been built.
        # The split business tier never builds one -- that model lives in its
        # own process -- but still needs to know to collect identification
        # targets and route them there instead of skipping face work
        # entirely.
        self.face_enabled = bool(self.spec.perception_profile.get("face_identity", {}).get("enabled", False))
        self.entry_windows: dict[int, EntryFaceWindow] = {}
        self.pending_entries: dict[int, PendingEntry] = {}
        # Tracks whose face request came back as a failure rather than as a
        # verdict. Without this, "the face tier is broken" and "this person
        # never turned toward the camera" both arrive as no decision at all,
        # and both raise an identical unknown-person alert -- so a dead face
        # model reads as a night of intruders, and nothing in the alert says
        # otherwise. Alerting either way is deliberate (a real stranger
        # during an outage must not be missed); telling them apart is what
        # lets an operator see an outage for what it is.
        self.face_failed_tracks: set[int] = set()
        self.metrics = {
            "frames_processed": 0,
            "detections_total": 0,
            "alerts_emitted": 0,
            "entries_seen": 0,
            # Boxes the inference tier embedded with ReID and this filter then
            # discards. ReID is ~48% of GPU capacity at the current frame rate,
            # so whether that waste is worth moving the filter upstream is a
            # ratio, not an opinion -- and nobody had measured the ratio.
            "boxes_before_min_h": 0,
            "boxes_dropped_min_h": 0,
            "unknown_alerts": 0,
            "face_identification_failures": 0,
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

    def _bootstrap_business(self, frame: FrameEnvelope):
        """Everything process_frame needs that is not a model session.

        Split out so the split-architecture business tier -- which never
        loads det/emb/face itself, since those live in the inference and
        face tiers -- can build the tracker and zone monitor without
        touching a GPU. Called from both `_bootstrap` (the local/
        current-architecture path) and directly by callers that only need
        this half.
        """
        if self._business_ready:
            return
        conf = float(self.spec.perception_profile.get("person_detector", {}).get("conf_thres", 0.45))
        cfg = warehouse.Config(fps=self.spec.target_fps)
        cfg.person_conf = conf
        # Entry was the only one of these a manifest could reach. Exit was
        # fixed at 4.0 s -- a number warehouse.Config's own comment sources
        # from one clip, where a person in a doorway crossed the boundary
        # three times in 15 s -- and exit is the side that decides whether a
        # person leaving is seen to leave at all. A doorway at the frame edge,
        # a busier aisle, or a camera further back all move it, and none of
        # them are properties of this system.
        if "entry_confirm_seconds" in self.params:
            cfg.entry_confirmation_s = float(self.params["entry_confirm_seconds"])
        if "exit_confirm_seconds" in self.params:
            cfg.exit_confirmation_s = float(self.params["exit_confirm_seconds"])
        if "presence_confirm_frames" in self.params:
            cfg.presence_confirmation_frames = int(self.params["presence_confirm_frames"])
        if "exit_on_loss_min_frames" in self.params:
            cfg.exit_on_loss_min_frames = int(self.params["exit_on_loss_min_frames"])
        if "anchor" in self.params:
            anchor = str(self.params["anchor"])
            # Checked here rather than trusted: ZoneMonitor compares
            # cfg.anchor against "bottom_center" and takes the keypoint path
            # for anything else, so a typo does not fail -- it silently
            # selects the other anchor and moves every zone decision on this
            # camera with nothing to say it happened.
            if anchor not in warehouse.ANCHOR_MODES:
                raise ValueError(
                    f"{self.spec.camera_id}: anchor={anchor!r} is not one of "
                    f"{sorted(warehouse.ANCHOR_MODES)}. An unrecognised value would "
                    f"quietly select the keypoint anchor and shift every zone "
                    f"crossing on this camera.")
            cfg.anchor = anchor
        # Once, after both confirmation windows are settled: __post_init__ is
        # what turns them into entry_frames/exit_frames.
        cfg.__post_init__()
        if "min_box_h_frac" in self.zone_cfg:
            cfg.min_box_h_frac = float(self.zone_cfg["min_box_h_frac"])

        h, w = frame.image_bgr.shape[:2]
        with self.timing.track("bootstrap::tracker"):
            self.tracker = Tracker(
            img_h=h, img_w=w, fps=self.spec.target_fps,
            cfg=camera_config(_tracker_overrides(self.spec), camera_id=self.spec.camera_id))
            self.monitor = warehouse.ZoneMonitor(self.zone_cfg["zone_norm"], w, h, cfg)
        self._business_ready = True

    def _bootstrap(self, frame: FrameEnvelope):
        pose_model = self.spec.perception_profile.get("person_detector", {}).get(
            "model_path",
            model_path("yolo26m-pose.onnx"),
        )
        reid_model = self.spec.perception_profile.get("person_tracker", {}).get(
            "reid_model_path",
            model_path("osnet_x0_25_msmt17.onnx"),
        )
        conf = float(
            self.spec.perception_profile.get("person_detector", {}).get("conf_thres", 0.45)
        )
        S.REID_MODEL = reid_model
        S.ORT_PROVIDERS = ["CPUExecutionProvider"] if self.cpu_only else None
        if self.shared_inference is not None:
            self.det = self.shared_inference.get_pose(pose_model)
        with self.timing.track("bootstrap::detector"):
            with BOOTSTRAP_LOCK:
                if self.det is None:
                    S.MODEL = pose_model
                    S.CONF = conf
                    self.det = S.Detector()
        with self.timing.track("bootstrap::embedder"):
            def _build_embedder():
                # S.Embedder reads the REID_MODEL global, so pin it under the lock.
                with BOOTSTRAP_LOCK:
                    S.REID_MODEL = reid_model
                    return S.Embedder()

            if self.shared_inference is not None:
                self.emb = self.shared_inference.get_reid_embedder(
                    reid_model, build=_build_embedder
                )
            else:
                self.emb = _build_embedder()

        self._bootstrap_business(frame)

        face_cfg = self.spec.perception_profile.get("face_identity", {})
        if face_cfg.get("enabled", False):
            # Read once, so the two construction paths below cannot disagree
            # about a threshold. They did: the direct path passed only
            # match_threshold and match_margin, so face_conf, crop_expand and
            # iou_thres were honoured under the shared registry and silently
            # dropped without it -- the same manifest, two behaviours.
            face_model = face_cfg.get("model_path", model_path("yolov6s_face.onnx"))
            arcface_model = face_cfg.get(
                "arcface_model_path", model_path("ms1mv3_arcface_r18.onnx"))
            face_conf = float(face_cfg.get("face_conf", 0.20))
            crop_expand = float(face_cfg.get("crop_expand", 0.12))
            iou_thres = float(face_cfg.get("iou_thres", 0.45))
            match_threshold = float(face_cfg.get("match_threshold", 0.30))
            match_margin = float(face_cfg.get("match_margin", 0.10))

            if self.shared_inference is not None:
                with self.timing.track("bootstrap::face"):
                    self.face_identifier = SharedFaceIdentifier(
                        face_detector=self.shared_inference.get_face_detector(face_model),
                        arcface=self.shared_inference.get_arcface(arcface_model),
                        gallery_db_path=self.gallery_db_path,
                        face_conf=face_conf,
                        crop_expand=crop_expand,
                        iou=iou_thres,
                        match_threshold=match_threshold,
                        match_margin=match_margin,
                    )
            else:
                from ai.src.perception.face_identity import FaceIdentifier  # noqa: E402

                providers = ["CPUExecutionProvider"] if self.cpu_only else None
                self.face_identifier = FaceIdentifier(
                    face_model,
                    arcface_model,
                    self.gallery_db_path,
                    providers=providers,
                    prefer_coreml=not self.cpu_only,
                    face_conf=face_conf,
                    crop_expand=crop_expand,
                    iou=iou_thres,
                    match_threshold=match_threshold,
                    match_margin=match_margin,
                )
        if self.face_identifier is not None:
            self._check_gallery_scope()
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

    def _check_gallery_scope(self) -> None:
        """Say so at startup when the identity gallery resolves to nothing.

        The store_id on the manifest has to match the one the staff were
        registered under. When it does not, every match returns
        "gallery_empty" and the run still looks healthy: alerts are emitted,
        just with nobody ever recognised. That cost a full 16-camera
        benchmark before it was noticed.
        """
        summary = self.face_identifier.gallery.summarize(
            store_id=self.spec.store_id,
            company_id=self.spec.company_id,
        )
        self.metrics["gallery"] = summary
        if summary["embeddings"] > 0:
            return
        logger.warning(
            "%s: face identity is enabled but the gallery is empty for store_id=%r "
            "company_id=%r. Every identification will report gallery_empty. "
            "Registered store ids: %s",
            self.spec.camera_id, self.spec.store_id, self.spec.company_id,
            summary["known_store_ids"],
        )

    def warmup(self, frame: FrameEnvelope):
        if self.det is not None:
            return
        with self.timing.track("bootstrap"):
            self._bootstrap(frame)
        self.metrics["bootstrap_ms"] = self.timing.stats["bootstrap"].total_s * 1000.0

    def _persist_ready_clips(self, clips: list[FinalizedAlertClip]):
        with self.timing.track("clip_persist_io"):
            for clip in clips:
                persist_clip(self.media_store, self.alert_store, clip, self.spec.target_fps)

    def finalize(self) -> int:
        """Close out anything still open. Returns alerts emitted while doing so.

        The count is returned rather than only counted in self.metrics
        because both runtimes accumulate their alert totals from what
        process_frame/handle_perceived returned, and finalize happens after
        the last of those -- so an alert raised here would otherwise be
        written to the alert store while missing from the run's own summary.
        """
        # Entries still waiting on a face window are resolved here rather
        # than abandoned. A window normally closes post_entry_frames after
        # the entry, and that is around a second of footage -- so anyone who
        # walks into the zone in the last second of a stream, or of a trimmed
        # benchmark clip, has an open window when the frames run out. Without
        # this the run reports "completed" with the alert never emitted, and
        # the loss is invisible: no error, just a smaller number. It is also
        # exactly the kind of entry worth catching, since a trim boundary is
        # arbitrary and a real stream simply ends when it ends.
        #
        # Uses whatever the window resolved to, the same value the expiry
        # path passes -- an entry closed early by the stream ending is not a
        # different kind of entry, it just ran out of frames to improve on.
        emitted = 0
        for track_id in list(self.pending_entries):
            window = self.entry_windows.get(track_id)
            emitted += self._finalize_entry(
                track_id, window.resolved_decision() if window else None)

        # Flushed after, not before: an alert raised just above opens a clip,
        # and flushing first would leave that clip unwritten.
        self._persist_ready_clips(self.clip_buffer.flush_all())
        if self.shared_inference is not None and self.face_identifier is not None:
            self.face_identifier.close()
        return emitted

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

        # A failed identification still alerts -- an outage is not a reason to
        # stop watching the door -- but it says so, instead of blaming the
        # person for a broken model.
        face_failed = decision is None and track_id in self.face_failed_tracks
        if face_failed:
            reason = "face_identification_failed"

        event_type = None
        reason_codes = [reason]
        if is_unknown:
            event_type = "unknown_entry"
            reason_codes.append(
                "FACE_IDENTIFICATION_FAILED" if face_failed else "UNKNOWN_PERSON_ENTERED_STORAGE")
            self.metrics["unknown_alerts"] += 1
        elif is_staff and not _in_allowed_window(pending.event_local_ts, self.access_schedule):
            event_type = "staff_outside_schedule"
            reason_codes.append("STAFF_ENTERED_OUTSIDE_ALLOWED_WINDOW")
            self.metrics["staff_outside_schedule_alerts"] += 1

        if event_type is None:
            self.pending_entries.pop(track_id, None)
            self.entry_windows.pop(track_id, None)
            self.face_failed_tracks.discard(track_id)
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
        self.face_failed_tracks.discard(track_id)
        return 1

    def process_frame(self, frame: FrameEnvelope):
        if self.det is None:
            self.warmup(frame)
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
        result, face_targets = self.handle_perceived(frame, boxes, kpts, feats)
        # In local/current-architecture mode self.face_identifier is a real
        # object (built in _bootstrap whenever face is enabled), so
        # handle_perceived already resolved every identification
        # synchronously and face_targets is always empty here.
        assert not face_targets, "local mode produced async face targets with no async path to send them"
        return result

    def handle_perceived(self, frame: FrameEnvelope, boxes, kpts, feats):
        """Tracking, zone logic, entry alerts and clip handling for one frame's perception.

        Takes boxes/kpts/feats already computed -- by process_frame's own
        det/emb calls in local mode, or by the inference tier's
        PerceptionResult in the split architecture -- so this is the one
        place the business logic lives regardless of where perception ran.

        The min_box_h filter runs here rather than where boxes first arrive,
        which costs a little wasted ReID compute in split mode (the
        inference tier embeds boxes this filter then discards, since it has
        no reason to know a zone-monitor threshold). The alternative --
        threading min_box_h_frac through to the inference tier -- would
        make a business-only config reach into perception. Filtering here
        keeps tracker.update() fed the exact same detections either way,
        which is the property that actually needs to hold.

        Returns (result_dict, face_targets). face_targets is empty except in
        split mode: entries needing identification that this pipeline has no
        model to resolve itself, for the caller to route to the face tier.
        """
        if not self._business_ready:
            self._bootstrap_business(frame)
        with self.timing.track("clip_buffer_push"):
            self.clip_buffer.push(frame)

        min_h = self.monitor.cfg.min_box_h_frac * frame.image_bgr.shape[0]
        if len(boxes) and min_h > 0:
            keep = (boxes[:, 3] - boxes[:, 1]) >= min_h
            self.metrics["boxes_before_min_h"] += int(len(boxes))
            self.metrics["boxes_dropped_min_h"] += int(len(boxes) - int(keep.sum()))
            boxes = boxes[keep]
            kpts = [k for k, keep_one in zip(kpts, keep) if keep_one]
            feats = feats[keep] if len(feats) else feats

        with self.timing.track("tracker_prepare_detections"):
            dets = [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i]) for i in range(len(boxes))]
        with self.timing.track("tracker_update"):
            live, removed = self.tracker.update(dets, frame.frame_idx)
        record_track_quality(self.metrics, self.tracker, live)
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
                if not self.face_enabled:
                    continue
                if st == warehouse.PENDING_IN:
                    self.entry_windows.setdefault(tid, EntryFaceWindow(frame.frame_idx))
                elif st == warehouse.OUTSIDE and tid in self.pending_entries:
                    window = self.entry_windows.get(tid)
                    decision = window.resolved_decision() if window is not None else None
                    alerts_from_finalize += self._finalize_entry(tid, decision)

        # Targets this pipeline cannot resolve itself in split mode
        # (self.face_identifier is None there): the caller sends these to
        # the face tier and later reports back through handle_face_answer.
        face_targets: list[tuple[int, Any, str | None, str | None]] = []
        if self.face_enabled:
            with self.timing.track("face_identity"):
                identify_calls = 0
                for tid, ltrb, _kp, st, _pt, _streak in items:
                    window = self.entry_windows.get(tid)
                    if window is None or not window.is_collecting(frame.frame_idx):
                        continue
                    if st not in (warehouse.PENDING_IN, warehouse.INSIDE):
                        continue
                    identify_calls += 1
                    if self.face_identifier is not None:
                        decision = self.face_identifier.identify(
                            frame.image_bgr,
                            ltrb,
                            store_id=self.spec.store_id,
                            company_id=self.spec.company_id,
                        )
                        window.consider(decision)
                    else:
                        face_targets.append((tid, ltrb, self.spec.store_id, self.spec.company_id))
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
                    event_local_ts=_store_local_time(frame, self.spec.store_timezone),
                    # A copy, not the frame itself. This gets read back out
                    # for a snapshot whenever the entry window closes, up to
                    # post_entry_frames later -- and in the split
                    # architecture frame.image_bgr is a view into a ring
                    # slot the decoder will have overwritten several times
                    # over by then. Cheap to always copy here since a zone
                    # entry is rare, not once per frame.
                    snapshot_frame=dataclasses.replace(frame, image_bgr=frame.image_bgr.copy()),
                )
                if not self.face_enabled:
                    alerts_from_finalize += self._finalize_entry(tid, None)
                    continue
                window = self.entry_windows.setdefault(tid, EntryFaceWindow(frame.frame_idx))
                window.end_frame = frame.frame_idx + self.post_entry_frames

        if self.face_enabled:
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
        }, face_targets

    def handle_face_answer(self, track_id: int, decision) -> None:
        """Feed an async identification result in whenever it arrives.

        Called from the split business tier only; the local/current path
        never has anything to feed here since it resolves identity inline.
        A window that has already been finalized or was never opened (the
        track left before its request came back) is silently ignored --
        exactly the same case handle_perceived already tolerates when
        resolved_decision() finds nothing.

        decision=None is the face tier saying it could not identify at all.
        It has to answer something for every request or the business worker
        never finishes counting them down, so None is the answer it sends
        when identify() raised. Ignoring it here is the same outcome as the
        request never having been made, which is what an exception in the
        local path would also have produced -- but consider() dereferences
        decision.observation, so it has to be filtered before that.
        """
        if decision is None:
            # Remembered, not just ignored: the alert this track eventually
            # raises has to be able to say the identification failed rather
            # than that the person was unrecognised.
            self.face_failed_tracks.add(int(track_id))
            self.metrics["face_identification_failures"] += 1
            return
        window = self.entry_windows.get(track_id)
        if window is not None:
            window.consider(decision)
