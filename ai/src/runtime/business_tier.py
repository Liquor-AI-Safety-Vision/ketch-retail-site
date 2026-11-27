"""CUDA-free processes: turn perception into tracks, alerts, clips.

One worker here can hold several cameras, each running the SAME pipeline
class (CounterCashTheftPipeline / StorageIntrusionPipeline /
TrackingBaselinePipeline) the current architecture already uses -- the
split does not reimplement tracking, FSM, zone logic or alerting, it only
changes where the boxes/features/till-detections those classes consume come
from. Each camera keeps its own tracker, till registry, clip buffer and
SQLite connection; nothing here changes that.

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

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ai.src.pipelines.registry import COUNTER_CASH_THEFT, FACE_TARGET_IDS, build_pipeline
from ai.src.runtime.frame_ring import FrameRing
from ai.src.runtime.inference_server import FaceAnswer, FaceRequest, PerceptionResult, crop_face_patch

logger = logging.getLogger(__name__)

#: How often each camera logs its own fps and latency while the run continues.
HEARTBEAT_S = 60.0
# How often a camera's cameras.last_frame_at is refreshed while frames flow.
# The dashboard calls a camera offline after a few missed refreshes.
CAMERA_SEEN_S = 30.0


def _build_pipeline(spec, binding, alert_store, media_store, run_id, gallery_db_path):
    """The one mapping, via the registry, so an unknown pipeline_id fails at
    bootstrap instead of falling through to the tracking pipeline."""
    return build_pipeline(
        spec, binding, alert_store=alert_store, media_store=media_store,
        run_id=run_id, gallery_db_path=gallery_db_path,
        cpu_only=False, shared_inference=None)


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
    target_fps: float = 0.0
    #: Decode -> business-done per frame, in ms: mean/p50/p95 over the most
    #: recent frames, max over the whole run.
    latency_ms: dict = field(default_factory=dict)


class _CameraWorker:
    """One camera's state, fed from PerceptionResult messages rather than
    calling det/emb itself -- those live in the inference tier."""

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
        # (completed_at, latency_s) per frame. Bounded: a forever run must not
        # grow a list per frame; 6000 frames is 20 min at 5 fps.
        self._samples: deque[tuple[float, float]] = deque(maxlen=6000)
        self._latency_max = 0.0
        self._last_heartbeat = time.time()
        self._last_seen_write = 0.0  # first processed frame marks the camera online at once

    def _record_latency(self, frame_meta) -> None:
        now = time.time()
        latency_s = now - frame_meta.ts_utc.timestamp()
        self._samples.append((now, latency_s))
        self._latency_max = max(self._latency_max, latency_s)
        if now - self._last_heartbeat >= HEARTBEAT_S:
            self._last_heartbeat = now
            self._log_heartbeat()
        if now - self._last_seen_write >= CAMERA_SEEN_S:
            self._last_seen_write = now
            touch = getattr(self.alert_store, "touch_camera", None)
            if touch is not None:
                touch(self.spec.camera_id,
                      datetime.fromtimestamp(now, timezone.utc).isoformat())

    def _log_heartbeat(self) -> None:
        """The same numbers the end-of-run table prints, while the run is still
        going. A forever run has no end, so without this a camera that has
        fallen behind its target fps is invisible until the process is stopped.
        The window is _samples' own (20 min at 5 fps), so this is recent
        behaviour, not an average since start-up."""
        s = self.latency_summary()
        if not s:
            return
        logger.info(
            "%s: %.2f fps (target %d) latency p50 %.0f p95 %.0f p99 %.0f max %.0f ms, "
            "%d/%d frames over the %.0f ms budget, max gap %.0f ms, %d alerts so far",
            self.spec.camera_id, s["fps"], self.spec.target_fps, s["p50"], s["p95"],
            s["p99"], s["max"], s["late"], s["frames"], s["interval_ms"],
            s["max_gap_ms"], self.alerts_emitted)

    def latency_summary(self) -> dict:
        """Per-frame timing from the first processed frame to the last.

        fps is measured between processed frames, so connection set-up before
        the first frame is not counted against it. "late" counts frames whose
        decode-to-done time exceeded one frame interval (1/target_fps) -- the
        budget a frame has before the next one is due. max_gap is the longest
        wait between two consecutive processed frames.
        """
        import numpy as np

        if len(self._samples) < 2:
            return {}
        done = np.asarray([s[0] for s in self._samples])
        lat = np.asarray([s[1] for s in self._samples]) * 1000.0
        interval_ms = 1000.0 / max(1e-9, float(self.spec.target_fps))
        return {"fps": round(float((len(done) - 1) / max(1e-9, done[-1] - done[0])), 3),
                "p50": round(float(np.percentile(lat, 50)), 1),
                "p95": round(float(np.percentile(lat, 95)), 1),
                "p99": round(float(np.percentile(lat, 99)), 1),
                "max": round(self._latency_max * 1000.0, 1),
                "late": int((lat > interval_ms).sum()),
                "interval_ms": round(interval_ms, 1),
                "max_gap_ms": round(float(np.diff(done).max()) * 1000.0, 1),
                "frames": int(len(done))}

    def handle_perception(self, result: PerceptionResult, frame_bgr) -> None:
        frame = result.frame_meta.to_envelope(frame_bgr)
        for handler in self.handlers:
            # Dispatching on the id here picks a call signature, not a class --
            # counter takes till detections, storage returns face targets. The
            # ids come from registry so this cannot drift from _build_pipeline,
            # and an unregistered one has already failed at bootstrap.
            if handler.binding.pipeline_id == COUNTER_CASH_THEFT:
                out = handler.handle_perceived(frame, result.boxes, result.kpts, result.feats,
                                               result.till_detections)
            elif handler.binding.pipeline_id in FACE_TARGET_IDS:
                out, face_targets = handler.handle_perceived(frame, result.boxes, result.kpts, result.feats)
                for track_id, ltrb, store_id, company_id in face_targets:
                    self._send_face_request(handler, track_id, ltrb, frame_bgr, store_id, company_id)
            else:
                out = handler.handle_perceived(frame, result.boxes, result.kpts, result.feats)
            self.alerts_emitted += int(out.get("alerts_emitted", 0))
            self.events_emitted += int(out.get("events_emitted", 0))
        self.frames_processed += 1
        self._record_latency(result.frame_meta)

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
            processing_ended_at=ended_at, pipeline_metrics=pipeline_metrics,
            target_fps=float(self.spec.target_fps), latency_ms=self.latency_summary())


def _build_sink_factory(storage_mode: str, db_path: str):
    """The one place that decides what AlertOutbox's writer thread talks to.

    local-db: SqliteAlertStore(db_path), unchanged from before AlertOutbox
    existed. cloud-db: CloudAlertSink, reading NeonDB/R2 credentials from the
    environment -- the parent process loads .env into os.environ before
    spawning tier processes, and spawn inherits the OS environment, so a
    business process needs nothing passed to it beyond the mode string
    itself. Failing on a missing env var here, inside the writer thread,
    surfaces through AlertOutbox.close() re-raising it -- not as a silent
    business process death.
    """
    if storage_mode == "cloud-db":
        import os

        from ai.src.infra.cloud_alert_sink import CloudAlertSink

        dsn = os.environ["DATABASE_URL"]
        r2_endpoint = os.environ["R2_ENDPOINT"]
        r2_bucket = os.environ["R2_BUCKET"]
        r2_key = os.environ["R2_ACCESS_KEY_ID"]
        r2_secret = os.environ["R2_SECRET_ACCESS_KEY"]
        return lambda: CloudAlertSink(dsn, r2_endpoint, r2_bucket, r2_key, r2_secret)

    from ai.src.infra.sqlite_alert_store import SqliteAlertStore
    return lambda: SqliteAlertStore(db_path)


def _recording_of(spec) -> dict | None:
    """The camera's stream.recording block, if its stream is being recorded."""
    stream = (spec.raw_config or {}).get("stream") or {}
    recording = stream.get("recording")
    return recording if isinstance(recording, dict) and recording.get("playback_url") else None


def run_business_process(shard: list, ring_specs: dict, free_qs: dict, business_qs: dict,
                         db_path: str, media_root: str, gallery_db_path: str, face_q,
                         ready_q, go_evt, stats_q, storage_mode: str = "local-db") -> None:
    """Entry point for one business process: several cameras, each on its own thread.

    Threaded so one camera's queue.get() blocking while it waits for the
    next perceived frame does not stall the others -- there is no GPU call
    in this tier to overlap, unlike the inference tier's reason for threads.
    """
    from concurrent.futures import ThreadPoolExecutor

    from ai.src.infra.local_media_store import LocalMediaStore
    from ai.src.runtime.alert_outbox import AlertOutbox
    from ai.src.runtime.perf import cap_native_threads
    from ai.src.runtime.tier_status import tier_guard

    with tier_guard("business", ready_q, stats_q):
        _run_business_process(shard, ring_specs, free_qs, business_qs, db_path, media_root,
                              gallery_db_path, face_q, ready_q, go_evt, stats_q,
                              cap_native_threads, ThreadPoolExecutor,
                              LocalMediaStore, AlertOutbox, storage_mode)


def _run_business_process(shard, ring_specs, free_qs, business_qs, db_path, media_root,
                          gallery_db_path, face_q, ready_q, go_evt, stats_q,
                          cap_native_threads, ThreadPoolExecutor,
                          LocalMediaStore, AlertOutbox, storage_mode="local-db") -> None:
    cap_native_threads(1)
    rings = {spec.camera_id: FrameRing(ring_specs[spec.camera_id]) for spec in shard}

    # One outbox per process, shared by every camera thread below, not one
    # sink per camera: the writer thread it starts is where the sink lives
    # (a sqlite3 connection is thread-affine; a psycopg2/boto3 one has no
    # reason to multiply either), and it is the single seam storage_mode
    # switches without touching anything below this line.
    alert_outbox = AlertOutbox(_build_sink_factory(storage_mode, db_path))

    # Cameras whose stream is recorded get their clips cut from the recording
    # at native fps (infra/recorded_clips.py); one worker thread serves them all.
    clip_worker = None
    if any(_recording_of(spec) for spec in shard):
        from ai.src.infra.recorded_clips import ClipWorker
        clip_worker = ClipWorker()

    # Every camera's pipelines are built before reporting ready -- zone files,
    # the private FSM module, the run row -- so none of it lands on a first
    # frame. The outbox above has already started opening its Neon/R2
    # connections on its writer thread.
    workers = {}
    for spec in shard:
        media_store = LocalMediaStore(media_root)
        recording = _recording_of(spec)
        if recording:
            from ai.src.infra.recorded_clips import RecordingClipMediaStore
            media_store = RecordingClipMediaStore(
                media_store, clip_worker, playback_url=recording["playback_url"],
                path=recording.get("path", spec.camera_id))
        workers[spec.camera_id] = _CameraWorker(spec, alert_outbox, media_store,
                                                gallery_db_path, face_q)

    ready_q.put(("business", 1))
    go_evt.wait()

    def run_one(spec) -> BusinessStats:
        worker = workers[spec.camera_id]
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

        return worker.finish(started_at)

    try:
        with ThreadPoolExecutor(max_workers=len(shard)) as pool:
            stats = list(pool.map(run_one, shard))
        # Closing here (once, after every camera thread is done producing
        # writes) flushes the queue and re-raises the first write failure --
        # closing per-camera would either close a store other cameras still
        # feed, or race the last camera's still-enqueued writes.
        #
        # Before stats_q.put, not after. The parent takes these stats as
        # "business is done" and from there gives every tier SHUTDOWN_GRACE_S
        # before terminating it. Flushing after the put meant the tail of the
        # outbox -- with cloud-db, R2 uploads of 5-15 MB clips -- raced that
        # grace period, and lost to it whenever the backlog was long.
        #
        # Clips first: a clip being cut from the recording queues its media
        # row into the outbox only when the file exists, so the outbox cannot
        # close before the clip worker has finished.
        if clip_worker is not None:
            clip_worker.close()
        alert_outbox.close()
        stats_q.put(stats)
    finally:
        try:
            alert_outbox.close()  # no-op if the line above already ran
        finally:
            for ring in rings.values():
                ring.close()
