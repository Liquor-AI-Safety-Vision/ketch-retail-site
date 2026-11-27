from __future__ import annotations

import importlib.util
import itertools
import threading
from pathlib import Path
from typing import Any

import numpy as np

from ai.src.common.timing import TimingCollector

# problem2_fsm is deliberately NOT imported here. Every use goes through
# self.F, the per-camera copy from load_private_fsm(); a shared module-level
# alias is exactly what one camera would retune out from under another.
from ai.src.perception import stream_track as S  # noqa: E402
from ai.src.perception import zones  # noqa: E402
from ai.src.perception.run_counter import Till, TillRegistry  # noqa: E402
from ai.src.perception.uae_tracker import Detection, Tracker, camera_config  # noqa: E402

from ai.src.domain.models import CameraPipelineBindingSpec, CameraStreamSpec, FrameEnvelope
from ai.src.infra.local_media_store import LocalMediaStore
from ai.src.infra.sqlite_alert_store import SqliteAlertStore
from ai.src.pipelines import fsm_tuning
from ai.src.pipelines.alert_clip_buffer import AlertClipBuffer, FinalizedAlertClip, persist_clip
from ai.src.pipelines.till_schedule import compute_till_schedule
from ai.src.pipelines.track_quality import record_track_quality
from ai.src.runtime.shared_inference import SharedInferenceRegistry
from ai.src.common.paths import model_path, zones_dir


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


BOOTSTRAP_LOCK = threading.Lock()

_FSM_INSTANCE_COUNTER = itertools.count()


def load_private_fsm():
    """A problem2_fsm module object private to one pipeline instance.

    The FSM keeps its tuning in module-level globals -- FPS, the confirm
    counts, ANY_HAND, WRIST_CONF_TH -- and reads them from inside step(),
    which decides whether a concealment becomes a theft alert. One camera per
    process, that is fine; it is how the FSM was originally driven. The split
    business tier runs several cameras as threads in one process, so a shared
    module means the second camera to bootstrap retunes the first one's
    thresholds and every alert after that is judged against another camera's
    settings, with nothing in the log to say so.

    Giving each pipeline its own module object makes those globals
    per-camera, which is what they always meant to be.

    Chosen over threading a tuning object through the FSM: that is 21
    constants across 703 lines of the code that raises theft alerts, and this
    reaches the same isolation without editing any of it. If the FSM ever
    does take its tuning per call, this can go away.

    The copies are deliberately NOT registered in sys.modules. The canonical
    `import problem2_fsm` stays a single shared module for every other caller,
    and nothing here can be reached by name from outside.
    """
    source = Path(__file__).resolve().parents[1] / "perception" / "problem2_fsm.py"
    name = f"problem2_fsm__private{next(_FSM_INSTANCE_COUNTER)}"
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load a private copy of {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        # Private FSM module: its tuning globals belong to this camera alone.
        self.F = load_private_fsm()

        alert_clip_cfg = dict(self.params.get("alert_clip", {}))
        self.clip_buffer = AlertClipBuffer(
            spec.target_fps,
            pre_seconds=float(alert_clip_cfg.get("pre_s", 15.0)),
            post_seconds=float(alert_clip_cfg.get("post_s", 15.0)),
            keep_images=getattr(media_store, "needs_clip_frames", True),
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
        fsm = dict(self.params.get("fsm", {}))
        fps = self.spec.target_fps
        # The default arithmetic is left exactly as it was -- frames_10fps *
        # fps / 10 -- so every recorded benchmark still describes this code.
        # An override takes the seconds path instead; at the default seconds
        # the two agree, but they are not written as the same expression and
        # nothing here relies on them rounding identically.
        scale = lambda frames_10fps: max(1, int(round(frames_10fps * fps / 10.0)))

        values = {"FPS": float(fps)}
        for key, const in fsm_tuning.FLAGS.items():
            values[const] = bool(fsm.get(key, getattr(self.F, const)))
        for key, (const, frames_10fps) in fsm_tuning.TIMING_FRAMES_AT_10FPS.items():
            values[const] = (
                max(1, int(round(float(fsm[key]) * fps))) if key in fsm
                else scale(frames_10fps)
            )
        for key, const in fsm_tuning.GEOMETRY.items():
            if key in fsm:
                values[const] = float(fsm[key])

        # DRAWER_IN/DRAWER_OUT are a hysteresis pair: entry at or under
        # DRAWER_IN, exit at or over DRAWER_OUT, and the gap between them is
        # what stops a wrist resting on the boundary from chattering between
        # states forever. Inverted or equal, the FSM does not fail -- it
        # oscillates, emitting a stream of entries and exits that look like a
        # very busy employee.
        drawer_in = values.get("DRAWER_IN", self.F.DRAWER_IN)
        drawer_out = values.get("DRAWER_OUT", self.F.DRAWER_OUT)
        if drawer_in >= drawer_out:
            raise ValueError(
                f"{self.spec.camera_id}: fsm.drawer_in ({drawer_in}) must be below "
                f"fsm.drawer_out ({drawer_out}). They are a hysteresis pair -- with "
                f"no gap the drawer state chatters on every frame a wrist hovers at "
                f"the boundary, and every chatter is an event.")

        # Writes into this camera's own module copy, so no lock and no owner
        # check: there is nobody else to collide with. Cameras that need
        # different tuning now simply get it, instead of the second one being
        # refused (or, before that guard existed, silently retuning the first).
        for name, value in values.items():
            setattr(self.F, name, value)

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
            self.tracker = Tracker(
            img_h=h, img_w=w, fps=self.spec.target_fps,
            cfg=camera_config(_tracker_overrides(self.spec), camera_id=self.spec.camera_id))

        # The default used to point into outputs/, the dev artefact directory,
        # so a backend unpacked anywhere else came up with no zones at all --
        # and failed at camera bootstrap rather than at import, where nothing
        # was looking. Zones are input, not output: they ship with the config.
        zones_path = self.params.get(
            "zones_path",
            str(zones_dir() / "top_side_view_zones.json"),
        )
        with self.timing.track("bootstrap::zones"):
            divider, _name = zones.load_divider(zones_path, size=(w, h))
            self.voter = zones.RoleVoter(divider)
            self.checkout = self.F.load_checkout_zones(zones_path, (w, h))
            self._configure_fsm_globals()
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

        # Fixed, not per camera. What varies between counters is the zone, the
        # confidence threshold and the sampling rate -- till_conf and till_fps
        # are per camera for exactly that reason. Which drawer model to run,
        # and at what input size, is a property of the deployment and of the
        # exported graph, not of where a camera is mounted.
        #
        # imgsz especially: this ONNX takes a dynamic input shape, so a camera
        # asking for 512 would run cleanly and simply detect worse than the
        # 640 it was trained at, with nothing to say so. --till-model and
        # --till-imgsz on run_split_pipeline remain, for deliberately
        # swapping the model across a whole run.
        till_model_path = model_path("till_seg_v4_640.onnx")
        till_imgsz = 640
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
                persist_clip(self.media_store, self.alert_store, clip, self.spec.target_fps)

    def finalize(self):
        self._persist_ready_clips(self.clip_buffer.flush_all())

    def _customer_served_recently(self, till_c: tuple[float, float] | None, frame_idx: int):
        zone_name = self.F.zone_for_till(till_c, self.checkout)
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
                    if self.F.point_in_poly(pt, poly):
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
                torso = self.F.torso_len(k)
                hands = self.hands_by_track.setdefault(track.track_id, self.F.HandHistory())
                for side_name, wrist_idx in (("L", self.F.WRI_L), ("R", self.F.WRI_R)):
                    hands.push(side_name, self.F.Sample(frame.frame_idx, k[wrist_idx, 0], k[wrist_idx, 1], k[wrist_idx, 2]))

                event = self.events_by_track.get(track.track_id)
                if event is None or event.state == self.F.ENDED:
                    event = self.F.ConcealmentEvent(track.track_id, frame.frame_idx)
                    self.events_by_track[track.track_id] = event
                before = event.state
                self.F.step(event, frame.frame_idx, k, torso, self.last_till_box, self.last_till_open, hands, [])
                if before != event.state:
                    self.metrics["state_transitions"] += 1
                if event.state == self.F.CONCEALED and not event.alerted:
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
