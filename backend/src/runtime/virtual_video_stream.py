from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2

from backend.src.domain.models import CameraStreamSpec, FrameEnvelope, StreamSourceInfo


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _simulated_local_time(spec: CameraStreamSpec, frame_idx: int) -> datetime | None:
    profile = spec.simulation_profile or {}
    if not profile.get("enabled", False):
        return None
    started_at_local = profile.get("started_at_local")
    if not started_at_local:
        return None
    base = datetime.fromisoformat(started_at_local)
    if base.tzinfo is None:
        base = base.replace(tzinfo=ZoneInfo(spec.store_timezone))
    speed = float(profile.get("playback_speed", 1.0) or 1.0)
    elapsed_s = frame_idx / spec.target_fps
    return base + timedelta(seconds=elapsed_s / speed)


@dataclass
class VirtualStreamPlan:
    target_duration_s: float
    target_fps: float
    start_s: float
    strategy: str


class StreamOpenError(RuntimeError):
    def __init__(
        self,
        *,
        source_path: str,
        camera_id: str,
        detail: str,
        file_exists: bool,
        open_attempts: int,
    ):
        self.source_path = source_path
        self.camera_id = camera_id
        self.detail = detail
        self.file_exists = file_exists
        self.open_attempts = open_attempts
        super().__init__(
            f"camera={camera_id} could not open source={source_path} "
            f"(file_exists={file_exists}, attempts={open_attempts}): {detail}"
        )


class VirtualVideoStreamSource:
    """Read a file progressively as if it were a live stream.

    The source never preloads the full video. It opens the file, seeks to the
    configured start, emits frames paced to wall-clock at target_fps, and loops
    only when the stream plan says the clip is shorter than the target runtime.
    """

    def __init__(self, spec: CameraStreamSpec):
        if spec.source_kind == "rtsp":
            # Live cameras live in RtspStreamSource: it reconnects instead of
            # treating a read failure as EOF, and rejects start_s/loop rather
            # than pretending a camera can be seeked. Reach both through
            # rtsp_stream.open_stream_source() rather than naming either class.
            raise ValueError(
                f"camera={spec.camera_id} source_kind=rtsp needs RtspStreamSource; "
                "use rtsp_stream.open_stream_source(spec) to pick the source by kind")
        if spec.source_kind != "video_file":
            raise ValueError(f"unsupported source kind: {spec.source_kind}")
        self.spec = spec
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
        }
        self.plan = VirtualStreamPlan(
            target_duration_s=spec.target_duration_s,
            target_fps=spec.target_fps,
            start_s=spec.start_s,
            strategy=spec.strategy,
        )
        self.info = self._probe()
        self._validate()

    def _probe(self) -> StreamSourceInfo:
        cap = self._open_capture(for_probe=True)
        native_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        cap.release()
        duration_s = frame_count / native_fps if native_fps else 0.0
        return StreamSourceInfo(
            path=self.spec.source_path,
            native_fps=native_fps,
            frame_count=frame_count,
            duration_s=duration_s,
            width=width,
            height=height,
        )

    def _validate(self):
        if self.plan.target_fps <= 0:
            raise ValueError("target_fps must be > 0")
        if self.plan.target_duration_s <= 0:
            raise ValueError("target_duration_s must be > 0")
        if self.info.native_fps <= 0:
            raise ValueError("source video has invalid fps")
        if self.plan.strategy not in {"trim", "loop_to_duration"}:
            raise ValueError(f"unsupported strategy: {self.plan.strategy}")
        if self.plan.start_s < 0:
            raise ValueError("start_s must be >= 0")

    def _open_capture(self, *, for_probe: bool = False):
        started = time.perf_counter()
        self.metrics["open_attempts"] += 1
        cap = cv2.VideoCapture(self.spec.source_path)
        elapsed_s = time.perf_counter() - started
        self.metrics["open_elapsed_s"] += elapsed_s
        if not cap.isOpened():
            self.metrics["open_failures"] += 1
            detail = "VideoCapture.isOpened() returned false"
            try:
                file_exists = Path(self.spec.source_path).exists()
            except Exception:
                file_exists = False
            self.metrics["last_open_error"] = detail
            if not file_exists:
                raise FileNotFoundError(self.spec.source_path)
            phase = "probe" if for_probe else "stream_open"
            raise StreamOpenError(
                source_path=self.spec.source_path,
                camera_id=self.spec.camera_id,
                detail=(
                    f"{phase} failed because VideoCapture could not initialize the source. "
                    "This often means codec/ffmpeg/resource exhaustion, not a missing file."
                ),
                file_exists=file_exists,
                open_attempts=int(self.metrics["open_attempts"]),
            )
        self.metrics["open_successes"] += 1
        if self.plan.start_s > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, self.plan.start_s * 1000.0)
        return cap

    def snapshot_metrics(self) -> dict[str, float | int | str | None]:
        open_attempts = max(1, int(self.metrics["open_attempts"]))
        open_successes = max(1, int(self.metrics["open_successes"]))
        return {
            "open_attempts": int(self.metrics["open_attempts"]),
            "open_successes": int(self.metrics["open_successes"]),
            "open_failures": int(self.metrics["open_failures"]),
            "open_avg_ms": round(self.metrics["open_elapsed_s"] * 1000.0 / open_attempts, 3),
            "open_success_avg_ms": round(
                self.metrics["open_elapsed_s"] * 1000.0 / open_successes, 3
            ),
            "reopen_count": int(self.metrics["reopen_count"]),
            "grab_failures": int(self.metrics["grab_failures"]),
            "retrieve_failures": int(self.metrics["retrieve_failures"]),
            "eof_count": int(self.metrics["eof_count"]),
            "last_open_error": self.metrics["last_open_error"],
        }

    def probe_first_frame(self) -> FrameEnvelope:
        probe = next(self.iter_timed_frames(pace_realtime=False), None)
        if probe is None:
            raise RuntimeError(f"no frame available from source: {self.spec.source_path}")
        frame, _timing = probe
        return frame

    def iter_frames(self, pace_realtime: bool = True):
        for frame, _timing in self.iter_timed_frames(pace_realtime=pace_realtime):
            yield frame

    def iter_timed_frames(self, pace_realtime: bool = True):
        emitted = 0
        target_frames = int(round(self.plan.target_duration_s * self.plan.target_fps))
        source_step = self.info.native_fps / self.plan.target_fps
        source_acc = 0.0
        loop_index = 0
        source_frame_idx = 0
        cap = self._open_capture()
        started = time.perf_counter()

        while emitted < target_frames:
            grab_s_total = 0.0
            skipped_source_frames = 0
            retrieve_s = 0.0
            sleep_s = 0.0
            while True:
                grab_started = time.perf_counter()
                grabbed = cap.grab()
                grab_s_total += time.perf_counter() - grab_started
                if not grabbed:
                    self.metrics["grab_failures"] += 1
                    self.metrics["eof_count"] += 1
                    if self.plan.strategy != "loop_to_duration":
                        cap.release()
                        return
                    cap.release()
                    cap = self._open_capture()
                    self.metrics["reopen_count"] += 1
                    loop_index += 1
                    source_frame_idx = 0
                    source_acc = 0.0
                    continue

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

            if pace_realtime:
                due = started + emitted / self.plan.target_fps
                slack = due - time.perf_counter()
                if slack > 0:
                    sleep_started = time.perf_counter()
                    time.sleep(slack)
                    sleep_s += time.perf_counter() - sleep_started

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
                loop_index=loop_index,
            ), {
                "source_grab_s": grab_s_total,
                "source_retrieve_s": retrieve_s,
                "source_sleep_s": sleep_s,
                "source_skipped_source_frames": skipped_source_frames,
            }
            emitted += 1
            source_frame_idx += 1

        cap.release()
