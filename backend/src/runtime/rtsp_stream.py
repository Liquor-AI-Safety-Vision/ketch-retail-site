"""Read a live RTSP camera, with the same surface as VirtualVideoStreamSource.

The two differ in what they can assume, which is why this is a separate class
rather than a branch inside the file source:

  * There is nothing to seek. start_s and the trim/loop strategies describe
    replaying a recording; a camera has no past to replay, so they are
    rejected rather than silently ignored.
  * The stream ends only by failing. A file's EOF is normal and means "loop or
    stop"; a camera's read failure means the link dropped, and the answer is
    to reconnect, not to finish.
  * The source, not the consumer, sets the pace. A file can be read as fast as
    the CPU allows; a camera emits at its own frame rate and cannot be hurried.

Measurements behind the design are in docs/RTSP_MIGRATION.md.
"""
from __future__ import annotations

import logging
import time
from datetime import timezone

import cv2

from backend.src.common.stream_uri import redact_stream_credentials
from backend.src.domain.models import CameraStreamSpec, FrameEnvelope, StreamSourceInfo
from backend.src.runtime.virtual_video_stream import (
    StreamOpenError,
    VirtualStreamPlan,
    _simulated_local_time,
    utc_now,
)

# Fallback when the camera does not report a frame rate. Deliberately a
# constant and not a measurement: measuring the rate at startup measures the
# buffer draining, not the camera. On the simulator a freshly opened stream
# delivered 21 fps for its first second against a true 15 fps, because ~1.5s
# of frames had queued while the connection was being negotiated. A source
# step derived from that number would be wrong for the whole run.
DEFAULT_NATIVE_FPS = 15.0

# Bound how long a dead camera can hold a decode lane. Without these the
# FFMPEG backend can block indefinitely on a host that accepts the TCP
# connection and then says nothing, which is exactly how a hung NVR behaves.
DEFAULT_OPEN_TIMEOUT_MS = 8000
DEFAULT_READ_TIMEOUT_MS = 8000

logger = logging.getLogger(__name__)


class RtspStreamSource:
    """Live RTSP source. Same public surface as VirtualVideoStreamSource."""

    def __init__(self, spec: CameraStreamSpec):
        if spec.source_kind != "rtsp":
            raise ValueError(
                f"RtspStreamSource requires source_kind=rtsp, got {spec.source_kind!r}")
        self.spec = spec

        options = dict((spec.raw_config or {}).get("stream", {}).get("rtsp", {}))
        self.transport = str(options.get("transport", "tcp")).lower()
        if self.transport not in {"tcp", "udp"}:
            raise ValueError(f"unsupported rtsp transport: {self.transport!r}")
        self.open_timeout_ms = int(options.get("open_timeout_ms", DEFAULT_OPEN_TIMEOUT_MS))
        self.read_timeout_ms = int(options.get("read_timeout_ms", DEFAULT_READ_TIMEOUT_MS))
        self.native_fps_hint = float(options.get("native_fps", 0.0))
        # 0 means keep retrying forever, which is what a permanently deployed
        # camera wants; a benchmark sets a bound so a broken run ends.
        self.max_reconnects = int(options.get("max_reconnects", 0))
        self.reconnect_backoff_s = float(options.get("reconnect_backoff_s", 0.5))
        self.reconnect_backoff_max_s = float(options.get("reconnect_backoff_max_s", 10.0))

        self.metrics = {
            "open_attempts": 0,
            "open_successes": 0,
            "open_failures": 0,
            "open_elapsed_s": 0.0,
            "reopen_count": 0,
            "grab_failures": 0,
            "retrieve_failures": 0,
            "eof_count": 0,
            "last_open_error": None,
            # Live-stream specific: a file source cannot produce these.
            "reconnect_count": 0,
            "reconnect_elapsed_s": 0.0,
            "max_frame_gap_s": 0.0,
        }
        self.plan = VirtualStreamPlan(
            target_duration_s=spec.target_duration_s,
            target_fps=spec.target_fps,
            start_s=spec.start_s,
            strategy=spec.strategy,
        )
        self._validate()
        self.info = self._probe()
        self._validate_frame_rate()

    @property
    def safe_uri(self) -> str:
        """The URI with credentials removed, for logs and exceptions."""
        return redact_stream_credentials(self.spec.source_uri) or ""

    def _validate(self) -> None:
        if self.plan.target_fps <= 0:
            raise ValueError("target_fps must be > 0")
        if self.plan.target_duration_s <= 0:
            raise ValueError("target_duration_s must be > 0")
        # Rejected rather than ignored: a manifest asking a camera to start 30s
        # in, or to loop, has a misunderstanding in it that should surface at
        # startup instead of quietly producing a stream that does neither.
        if self.plan.start_s:
            raise ValueError(
                f"camera={self.spec.camera_id} start_s={self.plan.start_s} is meaningless "
                "for a live camera; there is nothing to seek to")
        if self.plan.strategy == "loop_to_duration":
            raise ValueError(
                f"camera={self.spec.camera_id} strategy=loop_to_duration is meaningless "
                "for a live camera; there is no clip to replay")

    def _validate_frame_rate(self) -> None:
        """Refuse target_fps above the camera's rate -- but only if it is known.

        The failure being guarded is silent: source_step = native/target falls
        below 1, every grabbed frame passes the decimation test, and the lane
        emits native_fps while the tracker, the FSM and every confirm window
        have already been sized for target_fps. Each window then covers more
        wall-clock time than it was meant to, and no metric anywhere disagrees.

        Split by where native_fps came from, because the consequence of being
        wrong differs. Reported by the camera: refuse, the manifest is asking
        for frames that do not exist. Assumed: warn, because refusing on a
        guess would take a working camera offline over a header field OpenCV
        could not read -- and the assumption may well be the wrong one.
        """
        if self.plan.target_fps <= self.info.native_fps:
            return
        detail = (
            f"camera={self.spec.camera_id} target_fps={self.plan.target_fps:g} exceeds the "
            f"camera's {self.info.native_fps:g} fps ({self.native_fps_source}). The lane "
            f"would emit {self.info.native_fps:g} fps while the tracker, the FSM and every "
            f"confirm window are sized for {self.plan.target_fps:g}, so each covers more "
            f"wall-clock time than intended.")
        if self.native_fps_source == "reported":
            raise ValueError(detail + " Lower target_fps.")
        logger.warning(
            "%s Not refused because %s is an assumption, not a measurement -- set "
            "stream.rtsp.native_fps if it is wrong.", detail, self.info.native_fps)

    def _open_capture(self, *, for_probe: bool = False):
        started = time.perf_counter()
        self.metrics["open_attempts"] += 1
        # Transport goes through the private option string rather than the
        # OPENCV_FFMPEG_CAPTURE_OPTIONS environment variable, which is
        # process-global: one camera configured for UDP would otherwise change
        # the transport of every other camera sharing the decode process.
        cap = cv2.VideoCapture(self.spec.source_uri, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self.open_timeout_ms,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, self.read_timeout_ms,
        ])
        elapsed_s = time.perf_counter() - started
        self.metrics["open_elapsed_s"] += elapsed_s
        if not cap.isOpened():
            self.metrics["open_failures"] += 1
            detail = (
                f"{'probe' if for_probe else 'stream_open'} failed: VideoCapture could not "
                f"open the stream within {self.open_timeout_ms}ms. Typical causes are a "
                "wrong password (the server answers 401 and OpenCV reports only a generic "
                "failure), an unreachable host, or RTSP disabled on the device."
            )
            self.metrics["last_open_error"] = detail
            raise StreamOpenError(
                source_path=self.safe_uri,
                camera_id=self.spec.camera_id,
                detail=detail,
                # No file to look for; reported so the shared error type keeps
                # one meaning for the field rather than inventing a second.
                file_exists=False,
                open_attempts=int(self.metrics["open_attempts"]),
            )
        self.metrics["open_successes"] += 1
        return cap

    def _probe(self) -> StreamSourceInfo:
        cap = self._open_capture(for_probe=True)
        reported_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        cap.release()

        native_fps = reported_fps
        # Where the number came from decides how much can be concluded from
        # it. A camera that reports its own rate is authoritative; a fallback
        # is a guess, and refusing a run on a guess would take down a working
        # camera because OpenCV could not read a header field.
        self.native_fps_source = "reported"
        if native_fps <= 0:
            if self.native_fps_hint:
                native_fps = self.native_fps_hint
                self.native_fps_source = "manifest hint"
            else:
                native_fps = DEFAULT_NATIVE_FPS
                self.native_fps_source = "built-in default"
            logger.warning(
                "%s: the camera did not report a frame rate; assuming %g fps (%s). "
                "The decimation ratio native_fps/target_fps is derived from this, so a "
                "wrong assumption means the pipeline does not run at target_fps=%g. "
                "Set stream.rtsp.native_fps in the manifest to the camera's real rate.",
                self.spec.camera_id, native_fps, self.native_fps_source,
                self.plan.target_fps)
        return StreamSourceInfo(
            path=self.safe_uri,
            native_fps=native_fps,
            # A live stream has neither: it has not ended, so there is no
            # count, and no duration to divide it by.
            frame_count=0,
            duration_s=0.0,
            width=width,
            height=height,
        )

    def snapshot_metrics(self) -> dict[str, float | int | str | None]:
        open_attempts = max(1, int(self.metrics["open_attempts"]))
        open_successes = max(1, int(self.metrics["open_successes"]))
        return {
            "open_attempts": int(self.metrics["open_attempts"]),
            "open_successes": int(self.metrics["open_successes"]),
            "open_failures": int(self.metrics["open_failures"]),
            "open_avg_ms": round(self.metrics["open_elapsed_s"] * 1000.0 / open_attempts, 3),
            "open_success_avg_ms": round(
                self.metrics["open_elapsed_s"] * 1000.0 / open_successes, 3),
            "reopen_count": int(self.metrics["reopen_count"]),
            "grab_failures": int(self.metrics["grab_failures"]),
            "retrieve_failures": int(self.metrics["retrieve_failures"]),
            "eof_count": int(self.metrics["eof_count"]),
            "last_open_error": self.metrics["last_open_error"],
            "reconnect_count": int(self.metrics["reconnect_count"]),
            "reconnect_elapsed_s": round(self.metrics["reconnect_elapsed_s"], 3),
            "max_frame_gap_s": round(self.metrics["max_frame_gap_s"], 3),
        }

    def probe_first_frame(self) -> FrameEnvelope:
        probe = next(self.iter_timed_frames(pace_realtime=False), None)
        if probe is None:
            raise RuntimeError(f"no frame available from stream: {self.safe_uri}")
        frame, _timing = probe
        return frame

    def iter_frames(self, pace_realtime: bool = True):
        for frame, _timing in self.iter_timed_frames(pace_realtime=pace_realtime):
            yield frame

    def _reconnect(self, cap):
        """Release and reopen, backing off between attempts.

        Returns the new capture. Raises once max_reconnects is exhausted.
        """
        if cap is not None:
            cap.release()
        attempt = 0
        backoff = self.reconnect_backoff_s
        started = time.perf_counter()
        while True:
            attempt += 1
            self.metrics["reconnect_count"] += 1
            try:
                cap = self._open_capture()
                self.metrics["reopen_count"] += 1
                self.metrics["reconnect_elapsed_s"] += time.perf_counter() - started
                return cap
            except StreamOpenError:
                if self.max_reconnects and attempt >= self.max_reconnects:
                    self.metrics["reconnect_elapsed_s"] += time.perf_counter() - started
                    raise
                time.sleep(backoff)
                backoff = min(backoff * 2.0, self.reconnect_backoff_max_s)

    def iter_timed_frames(self, pace_realtime: bool = True):
        """Yield (FrameEnvelope, timing) pairs.

        `pace_realtime` is accepted for interface compatibility and
        intentionally ignored. A camera already emits at its own rate, so
        there is nothing to slow down -- and sleeping would be actively
        harmful: after a reconnect, or after the ~1.5s of frames that queue
        during connection setup, the loop has to run free to drain the backlog.
        Throttling it to target_fps would keep the source permanently behind
        live, which is the failure this class exists to avoid.
        """
        emitted = 0
        target_frames = int(round(self.plan.target_duration_s * self.plan.target_fps))
        source_step = self.info.native_fps / self.plan.target_fps
        source_acc = 0.0
        source_frame_idx = 0
        cap = self._open_capture()
        last_frame_at = time.perf_counter()

        try:
            while emitted < target_frames:
                grab_s_total = 0.0
                skipped_source_frames = 0
                retrieve_s = 0.0
                while True:
                    # Every source frame is grabbed and only the wanted ones
                    # are retrieved. This is the whole ballgame for a live
                    # stream: consuming at target_fps (3-5) from a camera
                    # sending 15 leaves frames piling up in the capture
                    # buffer, and every read then returns an older frame.
                    # Measured on the simulator: reading at 5 fps for 12s left
                    # 125 frames queued and ~8s of latency, still growing.
                    grab_started = time.perf_counter()
                    grabbed = cap.grab()
                    grab_s_total += time.perf_counter() - grab_started
                    if not grabbed:
                        # Not EOF -- a camera has no end. The link dropped.
                        self.metrics["grab_failures"] += 1
                        cap = self._reconnect(cap)
                        source_acc = 0.0
                        continue

                    now = time.perf_counter()
                    gap = now - last_frame_at
                    if gap > self.metrics["max_frame_gap_s"]:
                        self.metrics["max_frame_gap_s"] = gap
                    last_frame_at = now

                    source_acc += 1.0
                    if source_acc < source_step:
                        source_frame_idx += 1
                        skipped_source_frames += 1
                        continue
                    source_acc -= source_step
                    retrieve_started = time.perf_counter()
                    ok, frame = cap.retrieve()
                    retrieve_s += time.perf_counter() - retrieve_started
                    if not ok:
                        self.metrics["retrieve_failures"] += 1
                        source_frame_idx += 1
                        continue
                    break

                local_ts = _simulated_local_time(self.spec, emitted)
                ts_utc = local_ts.astimezone(timezone.utc) if local_ts else utc_now()

                yield FrameEnvelope(
                    company_id=self.spec.company_id,
                    store_id=self.spec.store_id,
                    camera_id=self.spec.camera_id,
                    camera_type=self.spec.camera_type,
                    frame_idx=emitted,
                    ts_utc=ts_utc,
                    simulated_local_ts=local_ts,
                    source_kind=self.spec.source_kind,
                    source_uri=self.spec.source_uri,
                    source_path=self.spec.source_path,
                    image_bgr=frame,
                    source_frame_idx=source_frame_idx,
                    source_time_s=source_frame_idx / self.info.native_fps,
                    # Nothing loops; kept at 0 so consumers that group by it
                    # see one continuous stream rather than a fake boundary
                    # every time the camera reconnects.
                    loop_index=0,
                ), {
                    "source_grab_s": grab_s_total,
                    "source_retrieve_s": retrieve_s,
                    # Always 0: the stream paces itself, so there is nothing
                    # to sleep for. Reported so the field means the same
                    # thing in both sources rather than going missing.
                    "source_sleep_s": 0.0,
                    "source_skipped_source_frames": skipped_source_frames,
                }
                emitted += 1
                source_frame_idx += 1
        finally:
            cap.release()


def open_stream_source(spec: CameraStreamSpec):
    """Build the source class that matches spec.source_kind.

    Exists so callers stop naming VirtualVideoStreamSource directly. Every
    call site that hardcodes the file source is a place where switching a
    manifest to real cameras raises NotImplementedError instead of working.
    """
    if spec.source_kind == "rtsp":
        return RtspStreamSource(spec)
    from backend.src.runtime.virtual_video_stream import VirtualVideoStreamSource
    return VirtualVideoStreamSource(spec)
