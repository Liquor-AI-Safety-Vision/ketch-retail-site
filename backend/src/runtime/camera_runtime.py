from __future__ import annotations

import logging
import time
from typing import Any

from backend.src.common.timing import TimingCollector
from backend.src.domain.models import CameraPipelineBindingSpec, CameraStreamSpec
from backend.src.infra.local_media_store import LocalMediaStore
from backend.src.infra.sqlite_alert_store import SqliteAlertStore
from backend.src.pipelines.registry import build_pipeline
from backend.src.runtime.shared_inference import SharedInferenceRegistry
from backend.src.runtime.rtsp_stream import open_stream_source

logger = logging.getLogger(__name__)


class CameraRuntime:
    def __init__(
        self,
        spec: CameraStreamSpec,
        *,
        db_path: str,
        media_root: str,
        gallery_db_path: str,
        cpu_only: bool = True,
        shared_inference: SharedInferenceRegistry | None = None,
        skip_pipelines: bool = False,
    ):
        self.spec = spec
        self.db_path = db_path
        self.media_root = media_root
        self.gallery_db_path = gallery_db_path
        self.cpu_only = cpu_only
        self.shared_inference = shared_inference
        self.skip_pipelines = skip_pipelines
        self.alert_store = SqliteAlertStore(db_path)
        self.media_store = LocalMediaStore(media_root)
        self.source = open_stream_source(spec)
        self.run_id: str | None = None
        self.pipeline_handlers = []
        self._prepared = False
        self.processing_started_at: float | None = None
        self.metrics = {
            "frames_processed": 0,
            "alerts_emitted": 0,
            "events_emitted": 0,
            "processing_mode": (
                "decode_only"
                if skip_pipelines
                else (
                    "thread_shared_batch_no_drop"
                    if shared_inference is not None
                    else "synchronous_no_queue_no_drop"
                )
            ),
            "source_skipped_source_frames": 0,
        }
        self.timing = TimingCollector()

    def close(self):
        self.alert_store.close()

    def _new_pipeline_handler(self, binding: CameraPipelineBindingSpec):
        return build_pipeline(
            self.spec,
            binding,
            alert_store=self.alert_store,
            media_store=self.media_store,
            run_id=self.run_id,
            gallery_db_path=self.gallery_db_path,
            cpu_only=self.cpu_only,
            shared_inference=self.shared_inference,
        )

    def prepare(self) -> None:
        """Build the pipeline handlers and load their models.

        Split out of run() so a caller can finish loading every camera's models
        before any camera starts pulling frames. Model init used to happen inside
        the timed loop, where it cost up to 20s of a 135s 16-camera run and left
        the first seconds of every stream behind its target_fps. Idempotent, so
        run() can still call it for callers that do not prepare up front.
        """
        if self._prepared:
            return
        self.run_id = self.alert_store.start_run(
            self.spec.camera_id,
            source_ref=self.spec.source_uri,
            metrics={"camera_type": self.spec.camera_type},
        )
        self.pipeline_handlers = [
            self._new_pipeline_handler(binding)
            for binding in self.spec.pipeline_bindings
            if binding.enabled
        ] if not self.skip_pipelines else []
        if self.pipeline_handlers:
            warmup_frame = self.source.probe_first_frame()
            for handler in self.pipeline_handlers:
                warmup = getattr(handler, "warmup", None)
                if callable(warmup):
                    warmup(warmup_frame)
        self._prepared = True

    def run(self, *, pace_realtime: bool = False, limit_frames: int | None = None) -> dict[str, Any]:
        self.prepare()
        # Marks the boundary between loading models and actually serving frames,
        # so throughput can be reported without model init folded into it.
        self.processing_started_at = time.time()
        status = "completed"
        try:
            for frame, source_timing in self.source.iter_timed_frames(pace_realtime=pace_realtime):
                if limit_frames is not None and self.metrics["frames_processed"] >= limit_frames:
                    break
                self.timing.merge_dict(
                    {
                        "source_grab": source_timing["source_grab_s"],
                        "source_retrieve": source_timing["source_retrieve_s"],
                        "source_sleep": source_timing["source_sleep_s"],
                    }
                )
                self.metrics["source_skipped_source_frames"] += int(
                    source_timing.get("source_skipped_source_frames", 0)
                )
                with self.timing.track("frame_total"):
                    for handler in self.pipeline_handlers:
                        with self.timing.track(f"pipeline::{handler.binding.pipeline_id}"):
                            result = handler.process_frame(frame)
                        self.metrics["alerts_emitted"] += int(result.get("alerts_emitted", 0))
                        self.metrics["events_emitted"] += int(result.get("events_emitted", 0))
                self.metrics["frames_processed"] += 1
                if self.metrics["frames_processed"] % 25 == 0:
                    logger.info(
                        "%s frames=%s alerts=%s events=%s avg_frame_ms=%s",
                        self.spec.camera_id, self.metrics["frames_processed"],
                        self.metrics["alerts_emitted"], self.metrics["events_emitted"],
                        self.timing.stats["frame_total"].as_dict()["avg_ms"],
                    )
        except KeyboardInterrupt:
            status = "cancelled"
            raise
        except Exception:
            status = "failed"
            raise
        finally:
            for handler in self.pipeline_handlers:
                finalize = getattr(handler, "finalize", None)
                if callable(finalize):
                    # Alerts raised while closing out (an entry whose face
                    # window was still open when the stream ended) come after
                    # the last process_frame, so they are added here rather
                    # than being written to the store but missing from the
                    # run summary. The split business tier does the same.
                    self.metrics["alerts_emitted"] += int(finalize() or 0)
            # Collected once, after finalize, and both returned and stored.
            # This used to be `return self._collect_metrics(status)` inside
            # the try: a return expression is evaluated before finally runs,
            # so the caller got the pre-finalize count while the database got
            # the post-finalize one. Two different totals for one run, and
            # the wrong one was the one regression.py compares -- which would
            # have read as the split architecture changing behaviour, since
            # the split tier finalizes before it builds its stats and was
            # therefore right.
            metrics = self._collect_metrics(status)
            self.alert_store.finish_run(self.run_id, status=status, metrics=metrics)
            self.close()
        return metrics

    def _collect_metrics(self, status: str) -> dict[str, Any]:
        pipeline_metrics = {}
        for handler in self.pipeline_handlers:
            pipeline_metrics[handler.binding.pipeline_id] = getattr(handler, "metrics", {})
        return {
            "status": status,
            "camera_id": self.spec.camera_id,
            "camera_type": self.spec.camera_type,
            "frames_processed": self.metrics["frames_processed"],
            "alerts_emitted": self.metrics["alerts_emitted"],
            "events_emitted": self.metrics["events_emitted"],
            "processing_mode": self.metrics["processing_mode"],
            "source_skipped_source_frames": self.metrics["source_skipped_source_frames"],
            "source_metrics": self.source.snapshot_metrics(),
            "processing_started_at": self.processing_started_at,
            "processing_ended_at": time.time(),
            "timing": self.timing.as_dict(),
            "pipeline_metrics": pipeline_metrics,
        }
