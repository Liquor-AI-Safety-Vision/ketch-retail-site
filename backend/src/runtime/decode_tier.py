"""CUDA-free processes: read a stream, letterbox it, hand it to inference.

This tier owns the frame's whole life until inference takes over: decode,
write straight into a ring slot, letterbox and normalise into that same
slot's blob, forward a small message. Nothing here touches a GPU, which is
the entire point -- this is the tier that is meant to scale with CPU core
count without adding CUDA context contention, measured elsewhere at 19ms per
call at two contexts, 45ms at four, 153ms at eight.

Letterboxing happens here rather than in the inference tier for the same
reason the frame itself does not travel by value: CPU work done in the
CUDA-holding process is CPU work that does not scale by adding decoders. The
arithmetic is the exact same call SharedPoseService.detect() makes
internally (preprocess()), so a frame letterboxed here and one letterboxed
in-process produce identical blobs -- verified in
test_decode_business_equivalence.py.

A decoder that cannot get a free slot blocks on the free list. That is
ordinary backpressure, not a round trip: it means the inference tier is
behind, and the right response is for this decoder to wait, not to allocate
around the pool the way the reference implementation's per-frame allocation
does (measured at over a full core's worth of time at this frame rate).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from backend.src.runtime.frame_ring import FrameRing
from backend.src.runtime.inference_server import DecodeMessage


@dataclass
class DecoderStats:
    camera_id: str
    frames: int
    started: float
    ended: float


def _run_camera(spec, ring: FrameRing, free_q, decode_q, pose_imgsz: int,
                pose_dtype, conf: float, till_conf: float | None,
                till_every_n: int) -> DecoderStats:
    """Pull one camera's frames to completion. Runs on a thread inside the decoder process."""
    from backend.src.runtime.shared_inference import _letterbox
    from backend.src.runtime.virtual_video_stream import VirtualVideoStreamSource

    import cv2

    source = VirtualVideoStreamSource(spec)
    frames = 0
    started = time.time()
    for frame in source.iter_frames(pace_realtime=False):
        slot = free_q.get()
        dst = ring.frame_slot(slot, frame.image_bgr.shape)
        np.copyto(dst, frame.image_bgr)

        tl0 = time.perf_counter()
        lb, ratio, dx, dy = _letterbox(frame.image_bgr, pose_imgsz)
        tl1 = time.perf_counter()
        blob_dst = ring.blob_slot(slot)
        rgb = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
        np.copyto(blob_dst, np.ascontiguousarray(rgb, pose_dtype) / pose_dtype(255.0))
        tl2 = time.perf_counter()

        run_till = till_conf is not None and (frame.frame_idx % till_every_n == 0)
        decode_q.put(DecodeMessage(
            camera_id=spec.camera_id, slot=slot, frame_shape=frame.image_bgr.shape,
            frame_idx=frame.frame_idx, ratio=ratio, dx=dx, dy=dy,
            letterbox_s=tl1 - tl0, preprocess_s=tl2 - tl1, conf=conf,
            till_conf=till_conf if run_till else None,
        ))
        frames += 1
    return DecoderStats(camera_id=spec.camera_id, frames=frames, started=started, ended=time.time())


def run_decoder_process(shard: list, ring_specs: dict, free_qs: dict, decode_qs: dict, *,
                        pose_imgsz: int, pose_dtype_name: str, conf: float,
                        till_conf: float | None, till_every_n: int,
                        ready_q, go_evt, stats_q) -> None:
    """Entry point for one decoder process: some cameras, each on its own thread.

    Threaded rather than one-camera-per-process because decode itself is I/O
    and light CPU work -- the expensive CPU work (pose, ReID) already left
    this tier -- so many cameras share a process cheaply the way they always
    have in the current architecture's camera threads.
    """
    from concurrent.futures import ThreadPoolExecutor

    from backend.src.runtime.frame_ring import FrameRing
    from backend.src.runtime.perf import cap_native_threads

    cap_native_threads(1)
    pose_dtype = np.float16 if pose_dtype_name == "float16" else np.float32
    rings = {spec.camera_id: FrameRing(ring_specs[spec.camera_id]) for spec in shard}

    ready_q.put(("decode", 1))
    go_evt.wait()

    def run_one(spec):
        return _run_camera(
            spec, rings[spec.camera_id], free_qs[spec.camera_id],
            decode_qs[spec.camera_id], pose_imgsz, pose_dtype, conf,
            till_conf, till_every_n)

    with ThreadPoolExecutor(max_workers=len(shard)) as pool:
        stats = list(pool.map(run_one, shard))
    stats_q.put(stats)
    for ring in rings.values():
        ring.close()
