"""CUDA-free processes: turn perception into tracks, alerts, clips.

One worker here can hold several cameras, each on its own tracker with its
own state -- the tracker, the till registry, the clip buffer and the SQLite
connection are all camera-scoped in the current pipelines, and nothing here
changes that. What changes is where the frame came from: instead of calling
det.detect() and emb() directly, a camera's loop reads PerceptionResult
messages that already carry boxes, keypoints, ReID features and (on the
frames it was sampled) till detections.

This tier is the last to touch a frame, and it is the one that releases the
slot back to the free list -- after tracking, after any crop needed for the
clip buffer or a face request, never before. Releasing early is a real
tearing bug (frame_ring.py caught it once already in a test); releasing here
means a slot is only recycled once nobody has any further use for it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from backend.src.runtime.frame_ring import FrameRing
from backend.src.runtime.inference_server import PerceptionResult


@dataclass
class CameraTrackState:
    tracker: object
    frames: int = 0
    tracks_created_last: int = 0


@dataclass
class BusinessStats:
    camera_id: str
    frames: int
    tracks_created: int
    track_frames_total: int
    started: float
    ended: float


def _new_tracker(h: int, w: int, fps: float):
    from uae_tracker import Tracker

    return Tracker(img_h=h, img_w=w, fps=fps)


def track_one_result(state: CameraTrackState, result: PerceptionResult) -> tuple[int, int]:
    """Feed one PerceptionResult through the real tracker. Returns (live, tracks_created)."""
    from uae_tracker import Detection

    dets = [
        Detection(result.boxes[i, :4], result.boxes[i, 4], result.feats[i], result.kpts[i])
        for i in range(len(result.boxes))
    ]
    live, _removed = state.tracker.update(dets, result.frame_idx)
    state.frames += 1
    return len(live), int(getattr(state.tracker, "_next_id", 0))


def run_business_process(shard: list, ring_specs: dict, free_qs: dict, business_qs: dict,
                         ready_q, go_evt, stats_q) -> None:
    """Entry point for one business process: several cameras, each on its own thread.

    Threaded, not because CPU work here needs to overlap a GPU call -- there
    is no GPU call in this tier -- but so one camera's queue.get() blocking
    while it waits for the next perceived frame does not stall the others.
    """
    from concurrent.futures import ThreadPoolExecutor

    from backend.src.runtime.perf import cap_native_threads

    cap_native_threads(1)
    rings = {spec.camera_id: FrameRing(ring_specs[spec.camera_id]) for spec in shard}

    ready_q.put(("business", 1))
    go_evt.wait()

    def run_one(spec) -> BusinessStats:
        business_q = business_qs[spec.camera_id]
        free_q = free_qs[spec.camera_id]
        ring = rings[spec.camera_id]
        state: CameraTrackState | None = None
        frames = 0
        track_frames_total = 0
        tracks_created = 0
        started = time.time()
        while True:
            item = business_q.get()
            if item is None:
                break
            result: PerceptionResult = item
            if state is None:
                h, w = result.frame_shape[:2]
                state = CameraTrackState(tracker=_new_tracker(h, w, spec.target_fps))
            live, tracks_created = track_one_result(state, result)
            track_frames_total += live
            frames += 1
            # Nothing else in this proof-of-concept needs the pixels, so the
            # slot is released the moment tracking is done with them. Once
            # clip buffering and face cropping join this tier, they read the
            # frame before this release, not after.
            free_q.put(result.slot)
        return BusinessStats(camera_id=spec.camera_id, frames=frames,
                             tracks_created=tracks_created,
                             track_frames_total=track_frames_total,
                             started=started, ended=time.time())

    with ThreadPoolExecutor(max_workers=len(shard)) as pool:
        stats = list(pool.map(run_one, shard))
    stats_q.put(stats)
    for ring in rings.values():
        ring.close()
