from __future__ import annotations

import sys
import threading
from pathlib import Path

from backend.src.common.timing import TimingCollector


from backend.src.perception import stream_track as S  # noqa: E402
from backend.src.perception.uae_tracker import Detection, Tracker  # noqa: E402

from backend.src.domain.models import CameraPipelineBindingSpec, CameraStreamSpec, FrameEnvelope
from backend.src.pipelines.track_quality import record_track_quality
from backend.src.runtime.shared_inference import SharedInferenceRegistry
from backend.src.common.paths import model_path


BOOTSTRAP_LOCK = threading.Lock()


class TrackingBaselinePipeline:
    """Lightweight production-shape baseline for cameras without full business rules yet."""

    def __init__(
        self,
        spec: CameraStreamSpec,
        binding: CameraPipelineBindingSpec,
        *,
        cpu_only: bool = True,
        shared_inference: SharedInferenceRegistry | None = None,
    ):
        self.spec = spec
        self.binding = binding
        self.cpu_only = cpu_only
        self.shared_inference = shared_inference
        self.det = None
        self.emb = None
        self.tracker = None
        self._business_ready = False
        self.metrics = {
            "frames_processed": 0,
            "detections_total": 0,
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

    def _bootstrap_business(self, frame: FrameEnvelope):
        """The tracker, and nothing that needs a model session.

        Split out for the split-architecture business tier, which never
        loads det/emb -- those live in the inference tier -- so it can build
        just the tracker.
        """
        if self._business_ready:
            return
        h, w = frame.image_bgr.shape[:2]
        self.tracker = Tracker(img_h=h, img_w=w, fps=self.spec.target_fps)
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
        self._bootstrap_business(frame)
        self.metrics["providers"] = {
            "pose_detector": getattr(self.det, "provider", "unknown"),
            "reid_embedder": getattr(self.emb, "provider", "unknown"),
        }

    def warmup(self, frame: FrameEnvelope):
        if self.det is not None:
            return
        with self.timing.track("bootstrap"):
            self._bootstrap(frame)
        self.metrics["bootstrap_ms"] = self.timing.stats["bootstrap"].total_s * 1000.0

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
        return self.handle_perceived(frame, boxes, kpts, feats)

    def handle_perceived(self, frame: FrameEnvelope, boxes, kpts, feats):
        """Tracking for one frame's perception, wherever it ran."""
        if not self._business_ready:
            self._bootstrap_business(frame)
        with self.timing.track("tracker_prepare_detections"):
            dets = [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i]) for i in range(len(boxes))]
        with self.timing.track("tracker_update"):
            live, removed = self.tracker.update(dets, frame.frame_idx)
        self.metrics["frames_processed"] += 1
        self.metrics["detections_total"] += int(len(boxes))
        self.metrics["max_live_tracks"] = max(self.metrics["max_live_tracks"], len(live))
        record_track_quality(self.metrics, self.tracker, live)
        self.metrics["timing"] = self.timing.as_dict()
        return {
            "alerts_emitted": 0,
            "events_emitted": 0,
            "live_tracks": len(live),
            "removed_tracks": len(removed),
        }
