"""Writing a video file that players will actually open.

Extracted from run_warehouse.py, which stayed behind in ai/dev/ as an offline
runner. till_state.py needs this one function and nothing else from it, so
keeping the whole runner in backend just to reach to_h264 would have dragged
a CLI, its argument parser and its repo-relative default paths along with it.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)


def to_h264(path: str) -> None:
    """OpenCV can only write mp4v here, which most players refuse. Re-encode in
    place so the file that lands on disk is one you can actually open."""
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not found; leaving %s as mp4v (may not play)", path)
        return
    tmp = path.replace(".mp4", ".h264.mp4")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                        "-pix_fmt", "yuv420p", tmp], capture_output=True)
    if r.returncode != 0:
        logger.error("re-encode failed: %s", r.stderr.decode()[:200])
        return
    os.replace(tmp, path)
