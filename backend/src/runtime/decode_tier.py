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

from backend.src.domain.models import FrameMeta
from backend.src.runtime.frame_ring import FrameRing
from backend.src.runtime.inference_server import DecodeMessage


@dataclass
class DecoderStats:
    camera_id: str
    frames: int
    started: float
    ended: float

    # Where a decoder thread's wall time actually goes. Split into work and
    # blocking, because the two mean opposite things: a decoder heavy on
    # grab/retrieve is the bottleneck, while one heavy on free_wait or
    # put_wait is idle behind a slower tier and its "decode FPS" is really a
    # measurement of downstream.
    #
    # grab/retrieve were already measured per frame by the source and thrown
    # away, because the tier called iter_frames() -- which drops the timing
    # dict iter_timed_frames() yields alongside every frame.
    free_wait_s: float = 0.0      # blocked waiting for a free ring slot
    grab_s: float = 0.0           # demux/decode of every source frame
    retrieve_s: float = 0.0       # full decode of the frames actually kept
    frame_copy_s: float = 0.0     # copying the BGR frame into the ring
    letterbox_s: float = 0.0      # resize/pad to the pose input size
    preprocess_s: float = 0.0     # BGR->RGB, CHW, /255 into the blob slot
    put_wait_s: float = 0.0       # blocked pushing to the lane's queue
    # Paced sources only (strategy=forever): how late a frame left the source
    # against its real-time schedule. max is the worst moment; last is where
    # the run ended -- a last that is large means the lag never recovered.
    schedule_lag_max_s: float = 0.0
    schedule_lag_last_s: float = 0.0
    # Every frame the source delivered, including the ones decimation skipped.
    # grab_s / source_frames is the average wait per source frame: for a live
    # stream at N fps it sits near 1000/N ms when the reader keeps up, and
    # collapses toward 0 when frames are piling up in the buffer.
    source_frames: int = 0


@dataclass
class CameraDecodeConfig:
    """Per-camera decode settings. Only till_conf/till_every_n vary by
    pipeline -- counter cameras sample a drawer, storage cameras do not."""

    conf: float
    till_conf: float | None = None
    till_every_n: int = 1


def _run_camera(spec, cfg: CameraDecodeConfig, ring: FrameRing, free_q, decode_q,
                pose_imgsz: int, pose_dtype, stop_evt=None, source=None) -> DecoderStats:
    """Pull one camera's frames until the stream ends or stop_evt is set.

    Runs on a thread inside the decoder process. stop_evt is how a run whose
    streams never end (strategy=forever) finishes: the parent sets it on
    SIGINT/SIGTERM, every camera returns from here after its current frame,
    and the ordinary end-of-stream shutdown takes over -- lanes drain,
    business flushes its alerts -- exactly as if the videos had run out.
    """
    from backend.src.runtime.shared_inference import _letterbox
    from backend.src.runtime.rtsp_stream import open_stream_source

    import cv2

    # By kind, not hardcoded: this is the tier that has to keep working when a
    # manifest switches from recorded files to real cameras.
    if source is None:
        source = open_stream_source(spec)
    # A file replayed forever stands in for a camera, so it is paced like one.
    # Unpaced it would run at whatever the GPU allows -- 1.55x realtime on a
    # 4070 -- which is a benchmark, not a simulation: timestamps drift ahead of
    # the footage and the load is not the load a real store produces. Finite
    # runs stay unpaced, which is what every recorded benchmark measured.
    # (An RTSP source ignores the flag; a camera paces itself.)
    pace_realtime = bool(getattr(source.plan, "endless", False))
    frames = 0
    started = time.time()
    acc = {"free_wait_s": 0.0, "grab_s": 0.0, "retrieve_s": 0.0, "frame_copy_s": 0.0,
           "letterbox_s": 0.0, "preprocess_s": 0.0, "put_wait_s": 0.0}
    # iter_timed_frames, not iter_frames: same frames, but it also yields the
    # grab/retrieve cost the source already measured for each one.
    frame_iter = source.iter_timed_frames(pace_realtime=pace_realtime)
    lag_max = lag_last = 0.0
    source_frames = 0
    try:
        for frame, source_timing in frame_iter:
            if stop_evt is not None and stop_evt.is_set():
                break
            acc["grab_s"] += source_timing["source_grab_s"]
            acc["retrieve_s"] += source_timing["source_retrieve_s"]
            lag_last = float(source_timing.get("source_schedule_lag_s", 0.0))
            lag_max = max(lag_max, lag_last)
            source_frames += 1 + int(source_timing.get("source_skipped_source_frames", 0))
            if frames == 0:
                # Checked once, on the first frame, rather than per frame. A
                # source larger than the ring was sized for would otherwise fail
                # inside np.copyto with a broadcast error, on a pool thread, in a
                # decoder process -- which surfaces as the parent waiting forever
                # on stats it will never receive, with the real cause buried.
                rh, rw = ring.spec.frame_shape[:2]
                fh, fw = frame.image_bgr.shape[:2]
                if fh > rh or fw > rw:
                    raise ValueError(
                        f"{spec.camera_id} decodes {fw}x{fh} frames but the ring was sized "
                        f"for at most {rw}x{rh}; raise MAX_FRAME_SHAPE in the driver, since "
                        f"a slot cannot hold this frame")
            tf0 = time.perf_counter()
            slot = free_q.get()
            tf1 = time.perf_counter()
            acc["free_wait_s"] += tf1 - tf0
            dst = ring.frame_slot(slot, frame.image_bgr.shape)
            np.copyto(dst, frame.image_bgr)
            acc["frame_copy_s"] += time.perf_counter() - tf1

            tl0 = time.perf_counter()
            lb, ratio, dx, dy = _letterbox(frame.image_bgr, pose_imgsz)
            tl1 = time.perf_counter()
            blob_dst = ring.blob_slot(slot)
            rgb = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
            # Normalise in float32 and let copyto do the narrowing, rather than
            # dividing in the blob's own dtype.
            #
            # For a float32 blob this is exactly what it did before. For float16
            # it is the difference between working and not: numpy has no SIMD
            # path for float16 arithmetic on CPU, so a /255 in float16 is
            # emulated element by element. Measured at 16 cameras over 3 minutes,
            # that one division took decode preprocessing from 69.3s to 539.8s --
            # 7.8x -- and made fp16 pose 15% *slower* end to end than fp32 while
            # the GPU sat idle waiting for it.
            np.copyto(blob_dst, np.ascontiguousarray(rgb, np.float32) / np.float32(255.0))
            tl2 = time.perf_counter()

            acc["letterbox_s"] += tl1 - tl0
            acc["preprocess_s"] += tl2 - tl1

            run_till = cfg.till_conf is not None and (frame.frame_idx % cfg.till_every_n == 0)
            tp0 = time.perf_counter()
            decode_q.put(DecodeMessage(
                camera_id=spec.camera_id, slot=slot, frame_shape=frame.image_bgr.shape,
                frame_idx=frame.frame_idx, frame_meta=FrameMeta.from_envelope(frame),
                ratio=ratio, dx=dx, dy=dy,
                letterbox_s=tl1 - tl0, preprocess_s=tl2 - tl1, conf=cfg.conf,
                till_conf=cfg.till_conf if run_till else None,
            ))
            acc["put_wait_s"] += time.perf_counter() - tp0
            frames += 1
    finally:
        # Releases the capture now rather than whenever the generator is
        # collected -- for RTSP that is a live connection to the camera.
        frame_iter.close()
    return DecoderStats(camera_id=spec.camera_id, frames=frames, started=started,
                        ended=time.time(), schedule_lag_max_s=lag_max,
                        schedule_lag_last_s=lag_last, source_frames=source_frames, **acc)


def run_decoder_process(shard: list, ring_specs: dict, free_qs: dict, decode_qs: dict,
                        camera_configs: dict[str, CameraDecodeConfig], *,
                        pose_imgsz: int, pose_dtype_name: str,
                        ready_q, go_evt, stats_q, stop_evt=None) -> None:
    """Entry point for one decoder process: some cameras, each on its own thread.

    Threaded rather than one-camera-per-process because decode itself is I/O
    and light CPU work -- the expensive CPU work (pose, ReID) already left
    this tier -- so many cameras share a process cheaply the way they always
    have in the current architecture's camera threads.
    """
    from concurrent.futures import ThreadPoolExecutor

    from backend.src.runtime.frame_ring import FrameRing
    from backend.src.runtime.perf import cap_native_threads
    from backend.src.runtime.tier_status import tier_guard

    # Everything, including the setup above ready_q.put, sits inside the
    # guard: a failure while opening a video or mapping a ring happens
    # before this process has ever spoken, and the parent is blocked on
    # ready_q at exactly that moment.
    with tier_guard("decode", ready_q, stats_q):
        cap_native_threads(1)
        pose_dtype = np.float16 if pose_dtype_name == "float16" else np.float32
        rings = {spec.camera_id: FrameRing(ring_specs[spec.camera_id]) for spec in shard}
        try:
            # Opened (and probed, and validated) before reporting ready, not on
            # the first frame: a bad URL or a target_fps the source cannot give
            # fails here, and live streams are already connected and draining
            # when the clock starts.
            from backend.src.runtime.rtsp_stream import open_stream_source

            sources = {spec.camera_id: open_stream_source(spec) for spec in shard}
            for source in sources.values():
                if hasattr(source, "keep_warm"):
                    source.keep_warm()
            ready_q.put(("decode", 1))
            go_evt.wait()

            def run_one(spec):
                return _run_camera(
                    spec, camera_configs[spec.camera_id], rings[spec.camera_id],
                    free_qs[spec.camera_id], decode_qs[spec.camera_id], pose_imgsz, pose_dtype,
                    stop_evt, sources[spec.camera_id])

            with ThreadPoolExecutor(max_workers=len(shard)) as pool:
                stats = list(pool.map(run_one, shard))
            stats_q.put(stats)
        finally:
            # Detaching in a finally matters even on the failure path: these
            # are consumer mappings, so close() only detaches, but leaving
            # them mapped keeps the block alive past the owner's unlink.
            for ring in rings.values():
                ring.close()
