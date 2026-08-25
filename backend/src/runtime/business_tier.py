"""CUDA-free processes: turn perception into tracks, alerts, clips.

One worker here can hold several cameras, each running the SAME pipeline
class (CounterCashTheftPipeline / StorageIntrusionPipeline /
TrackingBaselinePipeline) the current architecture already uses -- the
split does not reimplement tracking, FSM, zone logic or alerting, it only
changes where the boxes/features/till-detections those classes consume come
from. Each camera keeps its own tracker, till registry, clip buffer and
SQLite connection, exactly as camera_runtime.py already does; nothing here
changes that.

This tier is the last to touch a frame's pixels, and the one that releases
the ring slot back to the free list -- after tracking and after any crop
needed for the clip buffer, never before. Releasing early is a real tearing
bug (frame_ring.py caught it once already in a test); releasing here means a
slot only recycles once nobody has any further use for it.

Face identity is fire-and-forget from here. A camera's thread sends a
FaceRequest (a small cropped patch, not a slot reference -- see
inference_server.crop_face_patch) and moves on; the answer lands later on
this same per-camera queue as a FaceAnswer, dispatched to
pipeline.handle_face_answer() whenever it arrives, which is exactly what
EntryFaceWindow.consider() was built to tolerate. Because of that, a
camera's queue carries two message types and this tier discriminates by
isinstance rather than needing two queues per camera.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from backend.src.runtime.frame_ring import FrameRing
from backend.src.runtime.inference_server import FaceAnswer, FaceRequest, PerceptionResult, crop_face_patch


def _build_pipeline(spec, binding, alert_store, media_store, run_id, gallery_db_path):
    """Same mapping camera_runtime.py uses, so a camera behaves identically
    regardless of which architecture is running it."""
    from backend.src.pipelines.counter_cash_theft import CounterCashTheftPipeline
    from backend.src.pipelines.storage_intrusion import StorageIntrusionPipeline
    from backend.src.pipelines.tracking_baseline import TrackingBaselinePipeline

    if binding.pipeline_id == "counter_cash_theft_v1":
        return CounterCashTheftPipeline(
            spec, binding, alert_store, media_store, run_id,
            cpu_only=False, shared_inference=None)
    if binding.pipeline_id in {"storage_intrusion_v1", "cold_storage_intrusion_v1"}:
        return StorageIntrusionPipeline(
            spec, binding, alert_store, media_store, run_id, gallery_db_path,
            cpu_only=False, shared_inference=None)
    return TrackingBaselinePipeline(spec, binding, cpu_only=False, shared_inference=None)


@dataclass
class BusinessStats:
    camera_id: str
    camera_type: str
    frames_processed: int
    alerts_emitted: int
    events_emitted: int
    processing_started_at: float
    processing_ended_at: float
    pipeline_metrics: dict = field(default_factory=dict)


class _CameraWorker:
    """One camera's state, matching camera_runtime.CameraRuntime's role but
    fed from PerceptionResult messages instead of calling det/emb itself."""

    def __init__(self, spec, alert_store, media_store, gallery_db_path, face_q):
        self.spec = spec
        self.alert_store = alert_store
        self.media_store = media_store
        self.face_q = face_q
        self.run_id = alert_store.start_run(
            spec.camera_id, source_ref=spec.source_uri,
            metrics={"camera_type": spec.camera_type})
        self.handlers = [
            _build_pipeline(spec, binding, alert_store, media_store, self.run_id, gallery_db_path)
            for binding in spec.pipeline_bindings if binding.enabled
        ]
        self.frames_processed = 0
        self.alerts_emitted = 0
        self.events_emitted = 0
        # track_id is only unique within one camera's tracker, so pending
        # face state is keyed by track_id alone -- fine, since each
        # _CameraWorker instance already belongs to exactly one camera.
        self.pending_face_count: dict[int, int] = {}

    def handle_perception(self, result: PerceptionResult, frame_bgr) -> None:
        frame = result.frame_meta.to_envelope(frame_bgr)
        for handler in self.handlers:
            if handler.binding.pipeline_id == "counter_cash_theft_v1":
                out = handler.handle_perceived(frame, result.boxes, result.kpts, result.feats,
                                               result.till_detections)
            elif handler.binding.pipeline_id in {"storage_intrusion_v1", "cold_storage_intrusion_v1"}:
                out, face_targets = handler.handle_perceived(frame, result.boxes, result.kpts, result.feats)
                for track_id, ltrb, store_id, company_id in face_targets:
                    self._send_face_request(handler, track_id, ltrb, frame_bgr, store_id, company_id)
            else:
                out = handler.handle_perceived(frame, result.boxes, result.kpts, result.feats)
            self.alerts_emitted += int(out.get("alerts_emitted", 0))
            self.events_emitted += int(out.get("events_emitted", 0))
        self.frames_processed += 1

    def _send_face_request(self, handler, track_id, ltrb, frame_bgr, store_id, company_id) -> None:
        if self.face_q is None:
            # The pipeline only produces face targets when its manifest turns
            # face identity on, so reaching here means the caller wired up a
            # face-enabled camera without a face tier to answer it. Said
            # plainly rather than as an AttributeError on None, because the
            # difference matters: results from such a run are not comparable
            # to a real one, they are a run with identity silently missing.
            raise RuntimeError(
                f"{self.spec.camera_id} has face identity enabled and produced a face "
                f"target, but no face queue was provided; this run would silently drop "
                f"every identification")
        patch, box_in_patch = crop_face_patch(frame_bgr, ltrb)
        self.face_q.put(FaceRequest(
            camera_id=self.spec.camera_id, track_id=int(track_id), patch_bgr=patch,
            person_box_xyxy=box_in_patch, store_id=store_id, company_id=company_id))
        self.pending_face_count[track_id] = self.pending_face_count.get(track_id, 0) + 1

    def handle_face_answer(self, answer: FaceAnswer) -> None:
        for handler in self.handlers:
            if hasattr(handler, "handle_face_answer"):
                handler.handle_face_answer(answer.track_id, answer.decision)
        remaining = self.pending_face_count.get(answer.track_id, 1) - 1
        if remaining <= 0:
            self.pending_face_count.pop(answer.track_id, None)
        else:
            self.pending_face_count[answer.track_id] = remaining

    @property
    def outstanding_face_requests(self) -> int:
        return sum(self.pending_face_count.values())

    def finish(self, started_at: float) -> BusinessStats:
        pipeline_metrics = {}
        for handler in self.handlers:
            finalize = getattr(handler, "finalize", None)
            if callable(finalize):
                # finalize can emit alerts of its own -- a zone entry in the
                # last second of a stream has an open face window when the
                # frames run out. Those land after the last handle_perceived,
                # so they have to be added here or the run's summary
                # undercounts what is actually in the alert store.
                self.alerts_emitted += int(finalize() or 0)
            pipeline_metrics[handler.binding.pipeline_id] = getattr(handler, "metrics", {})
        ended_at = time.time()
        self.alert_store.finish_run(self.run_id, status="completed", metrics={
            "status": "completed", "camera_id": self.spec.camera_id,
            "camera_type": self.spec.camera_type, "frames_processed": self.frames_processed,
            "alerts_emitted": self.alerts_emitted, "events_emitted": self.events_emitted,
            "processing_started_at": started_at, "processing_ended_at": ended_at,
            "pipeline_metrics": pipeline_metrics,
        })
        return BusinessStats(
            camera_id=self.spec.camera_id, camera_type=self.spec.camera_type,
            frames_processed=self.frames_processed, alerts_emitted=self.alerts_emitted,
            events_emitted=self.events_emitted, processing_started_at=started_at,
            processing_ended_at=ended_at, pipeline_metrics=pipeline_metrics)


def run_business_process(shard: list, ring_specs: dict, free_qs: dict, business_qs: dict,
                         db_path: str, media_root: str, gallery_db_path: str, face_q,
                         ready_q, go_evt, stats_q) -> None:
    """Entry point for one business process: several cameras, each on its own thread.

    Threaded so one camera's queue.get() blocking while it waits for the
    next perceived frame does not stall the others -- there is no GPU call
    in this tier to overlap, unlike the inference tier's reason for threads.
    """
    from concurrent.futures import ThreadPoolExecutor

    from backend.src.infra.local_media_store import LocalMediaStore
    from backend.src.infra.sqlite_alert_store import SqliteAlertStore
    from backend.src.runtime.perf import cap_native_threads
    from backend.src.runtime.tier_status import tier_guard

    with tier_guard("business", ready_q, stats_q):
        _run_business_process(shard, ring_specs, free_qs, business_qs, db_path, media_root,
                              gallery_db_path, face_q, ready_q, go_evt, stats_q,
                              cap_native_threads, ThreadPoolExecutor,
                              LocalMediaStore, SqliteAlertStore)


def _run_business_process(shard, ring_specs, free_qs, business_qs, db_path, media_root,
                          gallery_db_path, face_q, ready_q, go_evt, stats_q,
                          cap_native_threads, ThreadPoolExecutor,
                          LocalMediaStore, SqliteAlertStore) -> None:
    cap_native_threads(1)
    rings = {spec.camera_id: FrameRing(ring_specs[spec.camera_id]) for spec in shard}

    ready_q.put(("business", 1))
    go_evt.wait()

    def run_one(spec) -> BusinessStats:
        # Each camera's own SqliteAlertStore: sqlite3 connections are
        # thread-affine, and each camera already lives on its own thread
        # here, matching the barrier-based preload's reason for pinning one
        # camera to one thread throughout in the current architecture.
        alert_store = SqliteAlertStore(db_path)
        media_store = LocalMediaStore(media_root)
        worker = _CameraWorker(spec, alert_store, media_store, gallery_db_path, face_q)
        ring = rings[spec.camera_id]
        free_q = free_qs[spec.camera_id]
        business_q = business_qs[spec.camera_id]
        started_at = time.time()

        perception_done = False
        while True:
            item = business_q.get()
            if item is None:
                perception_done = True
                if worker.outstanding_face_requests == 0:
                    break
                continue
            if isinstance(item, FaceAnswer):
                worker.handle_face_answer(item)
                if perception_done and worker.outstanding_face_requests == 0:
                    break
                continue
            result: PerceptionResult = item
            frame_bgr = ring.frame_slot(result.slot, result.frame_shape)
            try:
                worker.handle_perception(result, frame_bgr)
            finally:
                # The slot goes back even if the pipeline raised. It is not
                # about salvaging this frame -- that frame is lost either way
                # -- it is that a slot never returned is a slot permanently
                # out of circulation, and enough of them starve the decoder
                # into blocking on the free list forever. A pipeline bug
                # would then present as the whole camera silently stopping.
                free_q.put(result.slot)

        try:
            return worker.finish(started_at)
        finally:
            alert_store.close()

    try:
        with ThreadPoolExecutor(max_workers=len(shard)) as pool:
            stats = list(pool.map(run_one, shard))
        stats_q.put(stats)
    finally:
        for ring in rings.values():
            ring.close()
