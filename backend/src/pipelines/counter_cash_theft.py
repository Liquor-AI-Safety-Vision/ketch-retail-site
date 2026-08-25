from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

import numpy as np

from backend.src.common.paths import repo_root
from backend.src.common.timing import TimingCollector

ROOT = repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import problem2_fsm as F  # noqa: E402
import stream_track as S  # noqa: E402
import zones  # noqa: E402
from run_counter import Till, TillRegistry  # noqa: E402
from uae_tracker import Detection, Tracker  # noqa: E402

from backend.src.domain.models import CameraPipelineBindingSpec, CameraStreamSpec, FrameEnvelope
from backend.src.infra.local_media_store import LocalMediaStore
from backend.src.infra.sqlite_alert_store import SqliteAlertStore
from backend.src.pipelines.alert_clip_buffer import AlertClipBuffer, FinalizedAlertClip
from backend.src.pipelines.till_schedule import compute_till_schedule
from backend.src.pipelines.track_quality import record_track_quality
from backend.src.runtime.shared_inference import SharedInferenceRegistry


BOOTSTRAP_LOCK = threading.Lock()

# What the concealment FSM was last configured with, and by which camera.
#
# problem2_fsm keeps its tuning in module-level globals -- FPS, the confirm
# counts, ANY_HAND, WRIST_CONF_TH -- and _configure_fsm_globals writes into
# them. That is fine for one camera per process, which is how the FSM was
# originally driven. It is not fine here: the split business tier runs
# several cameras as threads in one process, so the second camera to
# bootstrap silently retunes the first one's FSM and every alert after that
# is judged against another camera's thresholds.
#
# Today every counter in the manifest shares identical parameters, so all of
# them write the same values and the race is harmless. That is luck, not
# design, and it is the kind of luck that runs out during a deployment rather
# than during a test. Until the FSM takes its tuning as an argument instead
# of reading globals -- 21 constants across 703 lines, threaded through the
# code that decides whether to raise a theft alert, so not a change to make
# casually -- this at least refuses to be silently wrong.
_FSM_GLOBALS_OWNER: dict[str, object] = {}
_FSM_GLOBALS_LOCK = threading.Lock()


class CounterCashTheftPipeline:
    def __init__(
        self,
        spec: CameraStreamSpec,
        binding: CameraPipelineBindingSpec,
        alert_store: SqliteAlertStore,
        media_store: LocalMediaStore,
        run_id: str,
        cpu_only: bool = True,
        shared_inference: SharedInferenceRegistry | None = None,
    ):
        self.spec = spec
        self.binding = binding
        self.alert_store = alert_store
        self.media_store = media_store
        self.run_id = run_id
        self.cpu_only = cpu_only
        self.shared_inference = shared_inference
        self.params = binding.params

        alert_clip_cfg = dict(self.params.get("alert_clip", {}))
        self.clip_buffer = AlertClipBuffer(
            spec.target_fps,
            pre_seconds=float(alert_clip_cfg.get("pre_s", 15.0)),
            post_seconds=float(alert_clip_cfg.get("post_s", 15.0)),
        )
        self.suppress_if_customer_recent = bool(self.params.get("suppress_if_customer_recent", True))
        self.customer_recent_window_frames = max(
            1,
            int(round(float(self.params.get("customer_recent_window_s", 3.0)) * self.spec.target_fps)),
        )

        self.det = None
        self.emb = None
        self.till = None
        self.till_reg = None
        self.tracker = None
        self.voter = None
        self.checkout = {}
        self.last_customer_seen: dict[str, int] = {}
        self.hands_by_track: dict[int, Any] = {}
        self.events_by_track: dict[int, Any] = {}
        self.last_till_box = None
        self.last_till_open = False
        self.till_every_n = 1
        self._business_ready = False
        self.metrics = {
            "frames_processed": 0,
            "detections_total": 0,
            "alerts_emitted": 0,
            "suppressed_events": 0,
            "state_transitions": 0,
            "till_state_changes": 0,
            "till_frames_skipped": 0,
            "till_batch_size_last": 1,
            "till_batch_size_sum": 0,
            "till_batch_size_max": 1,
            "till_batch_size_avg": 0.0,
            "till_queue_wait_ms_last": 0.0,
            "till_batch_wait_ms_last": 0.0,
            "max_live_tracks": 0,
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

    def _configure_fsm_globals(self):
        scale = lambda frames_10fps: max(1, int(round(frames_10fps * self.spec.target_fps / 10.0)))
        values = {
            "FPS": float(self.spec.target_fps),
            "HAND_INSIDE_CONFIRM": scale(3),
            "HAND_EXIT_CONFIRM": scale(3),
            "POSE_GRACE": scale(5),
            "DWELL_CONFIRM": scale(5),
            "STATE_TIMEOUT": scale(50),
            "POST_EVENT": scale(30),
            "TRAJ_WINDOW": scale(25),
            "TRAJ_LOOKBACK": max(1, scale(5)),
            "ANY_HAND": bool(self.params.get("any_hand", False)),
        }
        if "wrist_conf_th" in self.params:
            values["WRIST_CONF_TH"] = float(self.params["wrist_conf_th"])

        # Refuse to be the second camera that retunes a shared FSM to
        # something else. Two counters with the same settings are fine and
        # common -- they write identical values, which is why this has never
        # bitten. Two with different settings would corrupt each other's
        # alert decisions with nothing in the log to show for it.
        with _FSM_GLOBALS_LOCK:
            previous = _FSM_GLOBALS_OWNER.get("values")
            owner = _FSM_GLOBALS_OWNER.get("camera_id")
            if previous is not None and previous != values and owner != self.spec.camera_id:
                differing = {k: (previous.get(k), v) for k, v in values.items()
                             if previous.get(k) != v}
                raise RuntimeError(
                    f"{self.spec.camera_id} needs different concealment-FSM tuning than "
                    f"{owner}, which already configured it in this process: {differing}. "
                    f"problem2_fsm holds its tuning in module globals, so both cameras "
                    f"would share whichever was written last and one of them would be "
                    f"judged against the other's thresholds. Run them in separate "
                    f"processes, or give them matching parameters, until the FSM takes "
                    f"its tuning per instance.")
            _FSM_GLOBALS_OWNER["values"] = values
            _FSM_GLOBALS_OWNER["camera_id"] = self.spec.camera_id
            for name, value in values.items():
                setattr(F, name, value)

    def _bootstrap_business(self, frame: FrameEnvelope):
        """Everything process_frame needs that is not a model session.

        Split out so the split-architecture business tier -- which never
        loads det/emb/till itself, since those live in the inference tier --
        can build the tracker, the till debounce registry, the zone voter and
        the FSM globals without touching a GPU. Called from both
        `_bootstrap` (the local/current-architecture path) and directly by
        callers that only need this half.
        """
        till_confirm_frames = int(self.params.get("till_confirm_frames", 5))
        schedule = compute_till_schedule(
            self.spec.target_fps, till_confirm_frames, self.params.get("till_fps"))
        self.till_every_n = schedule.every_n
        self.till_reg = TillRegistry(schedule.confirm_samples)
        self.metrics["till_every_n_frames"] = schedule.every_n
        self.metrics["till_confirm_samples"] = schedule.confirm_samples
        self.metrics["till_confirm_window_s"] = schedule.confirm_window_s

        h, w = frame.image_bgr.shape[:2]
        with self.timing.track("bootstrap::tracker"):
            self.tracker = Tracker(img_h=h, img_w=w, fps=self.spec.target_fps)

        zones_path = self.params.get(
            "zones_path",
            str(ROOT / "outputs/top-side-view/top-side-view_zones.json"),
        )
        with self.timing.track("bootstrap::zones"):
            divider, _name = zones.load_divider(zones_path, size=(w, h))
            self.voter = zones.RoleVoter(divider)
            self.checkout = F.load_checkout_zones(zones_path, (w, h))
            self._configure_fsm_globals()
        self._business_ready = True

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
            self.spec.perception_profile.get("person_detector", {}).get("conf_thres", 0.35)
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

        till_model_path = self.params.get("till_model_path", str(ROOT / "models/till_seg_v4_640.onnx"))
        till_imgsz = int(self.params.get("till_imgsz", 640))
        till_conf = float(self.params.get("till_conf", 0.70))
        till_providers = (
            ["CPUExecutionProvider"]
            if self.cpu_only
            else ["CUDAExecutionProvider", "CPUExecutionProvider"]
        )
        self._bootstrap_business(frame)
        with self.timing.track("bootstrap::till"):
            if self.shared_inference is not None:
                self.till = self.shared_inference.get_till(
                    till_model_path,
                    imgsz=till_imgsz,
                )
            else:
                # confirm_samples, not the raw till_confirm_frames param: the
                # schedule already rescaled it for till_every_n, and the local
                # Till's own registry must debounce over the same window
                # _bootstrap_business's TillRegistry would have.
                self.till = Till(
                    till_model_path,
                    till_imgsz,
                    till_conf,
                    confirm=self.metrics["till_confirm_samples"],
                    providers=till_providers,
                )
                # The local Till owns its own TillRegistry built with that
                # same count; use it instead of the one _bootstrap_business
                # just built, so there is exactly one registry either way.
                self.till_reg = self.till.reg

        self.metrics["providers"] = {
            "pose_detector": getattr(self.det, "provider", "unknown"),
            "reid_embedder": getattr(self.emb, "provider", "unknown"),
            "till_detector": getattr(self.till, "provider", "unknown"),
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

    def _customer_served_recently(self, till_c: tuple[float, float] | None, frame_idx: int):
        zone_name = F.zone_for_till(till_c, self.checkout)
        served = (
            zone_name is not None
            and zone_name in self.last_customer_seen
            and frame_idx - self.last_customer_seen[zone_name] <= self.customer_recent_window_frames
        )
        return served, zone_name

    def _emit_alert(self, frame: FrameEnvelope, event, zone_name: str | None):
        reason_codes = list(event.reasons)
        metadata = {
            "employee_track_id": int(event.track_id),
            "alert_frame_idx": frame.frame_idx,
            "event_start_frame_idx": int(event.start),
            "event_source_time_s": round(frame.source_time_s, 3),
            "active_hand": "RIGHT" if event.active_hand == "R" else "LEFT",
            "checkout_zone": zone_name,
            "company_id": self.spec.company_id,
            "store_id": self.spec.store_id,
        }
        with self.timing.track("alert_io"):
            alert_id = self.alert_store.create_alert(
                store_id=self.spec.store_id,
                camera_id=self.spec.camera_id,
                run_id=self.run_id,
                pipeline_type=self.binding.pipeline_id,
                event_type="suspected_cash_concealment",
                severity="high",
                opened_at=frame.ts_utc.isoformat(),
                identity_person_id=None,
                identity_label=f"track_{event.track_id}",
                identity_score=None,
                reason_codes=reason_codes,
                metadata=metadata,
            )
            snapshot_path = self.media_store.save_snapshot(alert_id, frame)
            self.alert_store.add_alert_media(
                alert_id=alert_id,
                kind="snapshot",
                path=snapshot_path,
                mime_type="image/jpeg",
                started_at=frame.ts_utc.isoformat(),
                metadata={"employee_track_id": int(event.track_id)},
            )
            self.clip_buffer.open_clip(
                alert_id,
                event_frame_idx=frame.frame_idx,
                metadata={"camera_id": self.spec.camera_id, "event_type": "suspected_cash_concealment"},
            )
        self.metrics["alerts_emitted"] += 1

    def process_frame(self, frame: FrameEnvelope):
        if self.det is None:
            self.warmup(frame)

        with self.timing.track("detect_pose"):
            if self.shared_inference is not None:
                pose_result = self.det.detect(frame.image_bgr, conf=float(
                    self.spec.perception_profile.get("person_detector", {}).get("conf_thres", 0.35)
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
        # Sampling the drawer less often than every frame. The confirm streak is
        # counted in till samples, so it is scaled at bootstrap to keep the
        # debounce window the same length in seconds.
        run_till = (frame.frame_idx % self.till_every_n) == 0
        till_dets = None
        till_profile = {}
        with self.timing.track("till_infer"):
            if run_till:
                if self.shared_inference is not None:
                    till_result = self.till.detect(
                        frame.image_bgr,
                        conf=float(self.params.get("till_conf", 0.70)),
                    )
                    till_dets = till_result.detections
                    till_profile = till_result.profile
                else:
                    till_dets = self.till(frame.image_bgr)
                    till_profile = self.till.last_profile
            else:
                self.metrics["till_frames_skipped"] += 1
        self.timing.merge_dict(
            {
                "till_infer::letterbox": float(till_profile.get("letterbox_s", 0.0)),
                "till_infer::preprocess": float(till_profile.get("preprocess_s", 0.0)),
                "till_infer::infer": float(till_profile.get("infer_s", 0.0)),
                "till_infer::decode_masks": float(till_profile.get("decode_masks_s", 0.0)),
                "till_infer::queue_wait": float(till_profile.get("queue_wait_s", 0.0)),
                "till_infer::batch_wait": float(till_profile.get("batch_wait_s", 0.0)),
            }
        )
        self.metrics["till_batch_size_last"] = int(till_profile.get("batch_size", 1))
        self.metrics["till_batch_size_sum"] += self.metrics["till_batch_size_last"]
        self.metrics["till_batch_size_max"] = max(
            int(self.metrics["till_batch_size_max"]),
            self.metrics["till_batch_size_last"],
        )
        self.metrics["till_batch_size_avg"] = round(
            self.metrics["till_batch_size_sum"] / max(1, self.metrics["frames_processed"] + 1),
            3,
        )
        self.metrics["till_queue_wait_ms_last"] = round(float(till_profile.get("queue_wait_s", 0.0)) * 1000.0, 3)
        self.metrics["till_batch_wait_ms_last"] = round(float(till_profile.get("batch_wait_s", 0.0)) * 1000.0, 3)
        return self.handle_perceived(frame, boxes, kpts, feats, till_dets)

    def handle_perceived(self, frame: FrameEnvelope, boxes, kpts, feats, till_dets):
        """Tracking, till debounce, FSM and clip handling for one frame's perception.

        Takes boxes/kpts/feats/till_dets already computed -- by
        process_frame's own det/emb/till calls in local mode, or by the
        inference tier's PerceptionResult in the split architecture (where
        till was folded into the same message rather than a separate
        crossing, since it has no dependency on people at all) -- so this is
        the one place the business logic lives regardless of where
        perception ran. `till_dets is None` means till was not sampled this
        frame, matching process_frame's own run_till flag exactly.
        """
        if not self._business_ready:
            self._bootstrap_business(frame)
        with self.timing.track("clip_buffer_push"):
            self.clip_buffer.push(frame)

        with self.timing.track("tracker_prepare_detections"):
            dets = [
                Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i])
                for i in range(len(boxes))
            ]
        with self.timing.track("tracker_update"):
            live, removed = self.tracker.update(dets, frame.frame_idx)
        with self.timing.track("voter_drop_removed"):
            for tid in removed:
                self.voter.drop(tid)

        # Only advance the confirm streak on frames that actually observed the
        # drawer. Feeding it a repeated reading would reach `confirm` without any
        # new evidence, which is exactly the false positive the streak guards
        # against. On skipped frames the previous confirmed state stands.
        run_till = till_dets is not None
        changes: list = []  # no new drawer reading on a skipped frame
        if run_till:
            with self.timing.track("till_state_update"):
                items_till, changes = self.till_reg.update(till_dets)
            self.metrics["till_state_changes"] += len(changes)

            open_boxes = []
            self.last_till_open = False
            for till_state, _raw_name, _score, poly in items_till:
                box = [
                    float(poly[:, 0].min()),
                    float(poly[:, 1].min()),
                    float(poly[:, 0].max()),
                    float(poly[:, 1].max()),
                ]
                if till_state.state == "Open":
                    self.last_till_open = True
                    open_boxes.append(box)
            if open_boxes:
                self.last_till_box = open_boxes[0]
        self.metrics["max_live_tracks"] = max(self.metrics["max_live_tracks"], len(live))
        record_track_quality(self.metrics, self.tracker, live)

        with self.timing.track("voter_role_update"):
            live_items = []
            for track, _source in live:
                role, anchor_pt = self.voter.update(track.track_id, track.ltrb, track.kpts)
                live_items.append((track, role, anchor_pt))

        with self.timing.track("customer_zone_update"):
            for zone_name, poly in self.checkout.items():
                for track, role, _anchor_pt in live_items:
                    if role != zones.CUSTOMER:
                        continue
                    pt, _src = zones.anchor(track.ltrb, np.array(track.kpts) if track.kpts is not None else None)
                    if F.point_in_poly(pt, poly):
                        self.last_customer_seen[zone_name] = frame.frame_idx
                        break

        alerts_now = 0
        till_c = None
        if self.last_till_box is not None:
            till_c = (
                (self.last_till_box[0] + self.last_till_box[2]) / 2.0,
                (self.last_till_box[1] + self.last_till_box[3]) / 2.0,
            )

        with self.timing.track("fsm_logic"):
            fsm_tracks_considered = 0
            for track, role, _anchor_pt in live_items:
                if role != zones.STAFF or track.kpts is None:
                    continue
                fsm_tracks_considered += 1
                k = np.array(track.kpts)
                torso = F.torso_len(k)
                hands = self.hands_by_track.setdefault(track.track_id, F.HandHistory())
                for side_name, wrist_idx in (("L", F.WRI_L), ("R", F.WRI_R)):
                    hands.push(side_name, F.Sample(frame.frame_idx, k[wrist_idx, 0], k[wrist_idx, 1], k[wrist_idx, 2]))

                event = self.events_by_track.get(track.track_id)
                if event is None or event.state == F.ENDED:
                    event = F.ConcealmentEvent(track.track_id, frame.frame_idx)
                    self.events_by_track[track.track_id] = event
                before = event.state
                F.step(event, frame.frame_idx, k, torso, self.last_till_box, self.last_till_open, hands, [])
                if before != event.state:
                    self.metrics["state_transitions"] += 1
                if event.state == F.CONCEALED and not event.alerted:
                    served, zone_name = self._customer_served_recently(till_c, frame.frame_idx)
                    if self.suppress_if_customer_recent and served:
                        event.alerted = True
                        self.metrics["suppressed_events"] += 1
                        continue
                    event.alerted = True
                    self._emit_alert(frame, event, zone_name)
                    alerts_now += 1
            self.timing.merge_dict({"fsm_logic::tracks_considered": float(fsm_tracks_considered)})

        with self.timing.track("clip_buffer_collect"):
            ready_clips = self.clip_buffer.collect_ready(frame.frame_idx)
        self._persist_ready_clips(ready_clips)

        self.metrics["frames_processed"] += 1
        self.metrics["detections_total"] += int(len(boxes))
        self.metrics["timing"] = self.timing.as_dict()
        return {
            "alerts_emitted": alerts_now,
            "events_emitted": len(changes),
            "live_tracks": len(live),
            "removed_tracks": len(removed),
        }
