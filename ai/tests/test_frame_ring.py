"""The frame ring has to hold across a process boundary, not just in theory.

These are cheap checks of the properties the split depends on: that a
consumer in another process sees exactly what the producer wrote, that slots
recycle without tearing, and that the free list provides backpressure rather
than letting a fast producer run memory away.

    python -m pytest ai/tests/test_frame_ring.py -q
"""
from __future__ import annotations

import multiprocessing as mp
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.src.runtime.frame_ring import FrameRing, RingSpec  # noqa: E402

FRAME_SHAPE = (48, 64, 3)
BLOB_SHAPE = (3, 16, 16)


def _reader(spec: RingSpec, work_q, free_q, result_q):
    """Attach once, read many, and release each slot when finished with it.

    Releasing is the consumer's job and only the consumer's. A producer that
    returns a slot as soon as it has queued the index is free to refill it
    while this side is still reading, which produces a frame that is half one
    image and half the next.
    """
    ring = FrameRing(spec)
    try:
        seen = []
        while True:
            item = work_q.get()
            if item is None:
                break
            slot, expected_fill, expected_blob = item
            frame = ring.frame_slot(slot)
            blob = ring.blob_slot(slot)
            seen.append((
                int(frame[0, 0, 0]),
                bool((frame == expected_fill).all()),
                bool(np.isclose(blob, expected_blob).all()),
            ))
            free_q.put(slot)
        result_q.put(seen)
    finally:
        ring.close()


def test_consumer_process_sees_what_the_producer_wrote():
    ctx = mp.get_context("spawn")
    ring, free_q = FrameRing.create(ctx, slots=4, frame_shape=FRAME_SHAPE,
                                    blob_shape=BLOB_SHAPE)
    work_q, result_q = ctx.Queue(), ctx.Queue()
    proc = ctx.Process(target=_reader, args=(ring.spec, work_q, free_q, result_q))
    proc.start()
    try:
        # More frames than slots, so every slot is recycled several times and a
        # stale mapping or a missed release would show up.
        for i in range(12):
            slot = free_q.get()
            # Write in place, which is the whole point of handing out the
            # destination rather than accepting a finished array.
            ring.frame_slot(slot)[:] = i + 1
            ring.blob_slot(slot)[:] = np.float32(i) / 10.0
            work_q.put((slot, i + 1, np.float32(i) / 10.0))
        work_q.put(None)
        seen = result_q.get(timeout=30)
    finally:
        proc.join(timeout=30)
        if proc.is_alive():
            proc.terminate()
        ring.close()

    assert len(seen) == 12
    assert all(frame_ok and blob_ok for _, frame_ok, blob_ok in seen)
    assert [first for first, _, _ in seen] == list(range(1, 13))


def test_free_list_bounds_frames_in_flight():
    """A producer that cannot get a slot must block, not allocate."""
    ctx = mp.get_context("spawn")
    ring, free_q = FrameRing.create(ctx, slots=2, frame_shape=FRAME_SHAPE)
    try:
        taken = [free_q.get(), free_q.get()]
        assert sorted(taken) == [0, 1]
        import queue as _queue

        try:
            free_q.get(timeout=0.2)
            raise AssertionError("free list handed out a third slot from a pool of two")
        except _queue.Empty:
            pass
        free_q.put(taken[0])
        assert free_q.get(timeout=1) == taken[0]
    finally:
        ring.close()


def test_frame_slot_crops_to_the_real_frame_size():
    """Slots are sized for the largest frame; smaller ones use a sub-view."""
    ctx = mp.get_context("spawn")
    ring, _free_q = FrameRing.create(ctx, slots=1, frame_shape=(1080, 1920, 3))
    try:
        view = ring.frame_slot(0, (480, 640, 3))
        assert view.shape == (480, 640, 3)
        view[:] = 7
        assert ring.frame_slot(0)[479, 639, 2] == 7
        assert ring.frame_slot(0)[480, 640, 2] == 0
    finally:
        ring.close()


def test_blobs_are_optional():
    ctx = mp.get_context("spawn")
    ring, _free_q = FrameRing.create(ctx, slots=1, frame_shape=FRAME_SHAPE)
    try:
        assert ring.blobs == []
        assert ring.spec.blob_bytes == 0
        assert ring.frame_slot(0).shape == FRAME_SHAPE
    finally:
        ring.close()
