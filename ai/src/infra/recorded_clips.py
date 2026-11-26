"""Evidence clips cut from the camera's own recording, at its native frame rate.

The pipelines only ever see the frames they sample (3-5 fps), so a clip built
from those frames plays like a slideshow. The recording server that the
cameras stream through (MediaMTX) already writes each stream to disk unchanged,
native fps and all, and serves any time range of it over HTTP:

    GET {playback_url}/get?path={path}&start={RFC3339}&duration={s}&format=mp4

So a clip is a time range, not a pile of frames: the pipeline's
AlertClipBuffer still decides *when* (15 s before the event to 15 s after), but
with this store it keeps timestamps only, no pixels, and the clip is fetched
from the recording once the range has been written, then re-encoded to H.264
so a browser can play it.

That work runs on one background thread per business process, never on the
camera thread: a clip cannot be fetched until 15 s after the event anyway, and
a transcode is seconds of CPU that would otherwise stall that camera's frames.
When the clip is ready its add_alert_media is queued behind the alert row the
pipeline already queued, so the FK order AlertOutbox relies on still holds.

This is the same shape as a network video recorder, and it is what a real
deployment needs as well: RTSP cannot seek, so "the 15 s before" only exists if
something was recording it.
"""
from __future__ import annotations

import logging
import queue
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

#: How long after the end of a range to wait before asking for it: the recorder
#: flushes a part every recordPartDuration (1 s) and the tail must be on disk.
SETTLE_S = 3.0
#: How long to keep retrying a range the recorder does not have yet.
FETCH_RETRY_S = 30.0


def to_h264(src: Path, dst: Path) -> None:
    """Re-encode any clip to H.264/yuv420p mp4 with the index at the front.

    MPEG-4 Part 2 (OpenCV's mp4v) and HEVC do not play in a browser <video>;
    H.264 in yuv420p does everywhere. faststart puts the moov atom first so
    playback can begin before the whole file has downloaded from R2.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found on PATH; cannot produce a browser-playable clip")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
           "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-threads", "2", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg could not transcode {src}: {proc.stderr.strip()[-500:]}")


def fetch_recording(playback_url: str, path: str, start: datetime, duration_s: float,
                    dst: Path, timeout_s: float = 60.0) -> None:
    query = urllib.parse.urlencode({
        "path": path, "start": start.isoformat(), "duration": f"{duration_s:.3f}",
        "format": "mp4"})
    url = f"{playback_url.rstrip('/')}/get?{query}"
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:
        dst.write_bytes(resp.read())
    if dst.stat().st_size == 0:
        raise RuntimeError(f"recording server returned an empty clip for {path} @ {start}")


@dataclass
class _ClipJob:
    alert_id: str
    started_at: datetime
    ended_at: datetime
    on_saved: Callable[[str], None]
    fetch: Callable[[Path], None]
    out_dir: Path


class ClipWorker:
    """One background thread that turns clip ranges into H.264 files."""

    def __init__(self, *, settle_s: float = SETTLE_S, retry_s: float = FETCH_RETRY_S,
                 transcode: Callable[[Path, Path], None] = to_h264):
        self.settle_s = settle_s
        self.retry_s = retry_s
        self.transcode = transcode
        self._queue: "queue.Queue[_ClipJob | None]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="clip-worker")
        self._thread.start()
        self.saved = 0
        self.failed = 0

    def submit(self, job: _ClipJob) -> None:
        self._queue.put(job)

    def close(self, timeout_s: float = 180.0) -> None:
        """Finish every submitted clip (bounded), then stop."""
        self._queue.put(None)
        self._thread.join(timeout=timeout_s)
        if self._thread.is_alive():
            logger.error("clip worker: still busy after %.0fs at shutdown; remaining clips "
                         "are lost", timeout_s)

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                self._do(job)
                self.saved += 1
            except Exception as exc:  # noqa: BLE001 - one clip must not stop the rest
                self.failed += 1
                logger.error("clip for alert %s could not be produced: %r", job.alert_id, exc)

    def _do(self, job: _ClipJob) -> None:
        wait = job.ended_at.timestamp() + self.settle_s - time.time()
        if wait > 0:
            time.sleep(wait)
        job.out_dir.mkdir(parents=True, exist_ok=True)
        raw = job.out_dir / "evidence_raw.mp4"
        fetch_started = time.time()
        deadline = time.monotonic() + self.retry_s
        while True:
            try:
                job.fetch(raw)
                break
            except (urllib.error.URLError, RuntimeError, OSError) as exc:
                if time.monotonic() >= deadline:
                    raise
                logger.warning("clip for alert %s not fetchable yet, retrying: %r",
                               job.alert_id, exc)
                time.sleep(2.0)
        fetched_at = time.time()
        out = job.out_dir / "evidence.mp4"
        try:
            self.transcode(raw, out)
        finally:
            raw.unlink(missing_ok=True)
        done_at = time.time()
        logger.info(
            "clip for alert %s: %.1fs range, fetch %.2fs, h264 encode %.2fs, ready %.1fs "
            "after the range ended", job.alert_id,
            (job.ended_at - job.started_at).total_seconds(), fetched_at - fetch_started,
            done_at - fetched_at, done_at - job.ended_at.timestamp())
        job.on_saved(str(out))


class RecordingClipMediaStore:
    """A camera's media store when a recording of its stream exists.

    Snapshots and stills still come from the pipeline's frames; only clips
    come from the recording.
    """

    #: Tells AlertClipBuffer it need not keep pixels, only timestamps.
    needs_clip_frames = False

    def __init__(self, local, worker: ClipWorker, *, playback_url: str, path: str):
        self.local = local
        self.worker = worker
        self.playback_url = playback_url
        self.path = path

    def save_snapshot(self, alert_id, frame):
        return self.local.save_snapshot(alert_id, frame)

    def save_image(self, alert_id, name, image_bgr):
        return self.local.save_image(alert_id, name, image_bgr)

    def submit_clip(self, clip, on_saved: Callable[[str], None]) -> None:
        if clip.started_at is None or clip.ended_at is None:
            logger.error("clip for alert %s has no time range; skipped", clip.alert_id)
            return
        duration = max(1.0, (clip.ended_at - clip.started_at).total_seconds())
        self.worker.submit(_ClipJob(
            alert_id=clip.alert_id, started_at=clip.started_at, ended_at=clip.ended_at,
            on_saved=on_saved, out_dir=Path(self.local.root_dir) / clip.alert_id,
            fetch=lambda dst: fetch_recording(
                self.playback_url, self.path, clip.started_at, duration, dst)))
