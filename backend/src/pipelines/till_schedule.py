from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TillSchedule:
    every_n: int
    confirm_samples: int
    confirm_window_s: float


def compute_till_schedule(camera_fps: float, till_confirm_frames: int, till_fps: float | None) -> TillSchedule:
    """How often to sample the drawer, and how many samples confirm a change.

    `till_confirm_frames` is a debounce expressed in samples, tuned when the
    drawer was sampled on every frame. Sampling it less often has to keep the
    same debounce *duration*, or a 5-sample streak at a lower rate would take
    longer than intended to confirm a drawer that opened.

    This has to be the one place this arithmetic exists. In the split
    architecture the decoder tier needs `every_n` before any pipeline object
    is built -- it decides whether to run till on a given frame -- while the
    business tier needs `confirm_samples` to build its TillRegistry. Two
    copies of this formula would drift the moment one changed.
    """
    camera_fps = float(camera_fps) or 1.0
    confirm_window_s = till_confirm_frames / camera_fps
    every_n = max(1, int(round(camera_fps / float(till_fps or camera_fps))))
    sample_fps = camera_fps / every_n
    confirm_samples = max(1, int(round(confirm_window_s * sample_fps)))
    return TillSchedule(
        every_n=every_n,
        confirm_samples=confirm_samples,
        confirm_window_s=round(confirm_samples / sample_fps, 3),
    )
