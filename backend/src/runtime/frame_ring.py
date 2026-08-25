"""A fixed pool of shared-memory slots for handing frames between processes.

Every rule enforced here came out of a measurement, so they are worth stating
rather than leaving as style. Per 1920x1080x3 frame, expressed as the share of
one core at the 112 FPS the system sustains today:

    queue round-trip only, no frame          0.92 ms     10% of a core
    written in place, reader keeps it mapped 0.98 ms     11%
    copied into a pooled slot                3.08 ms     35%
    fresh block allocated per frame         10.87 ms    122%
    the array pickled through mp.Queue      39.52 ms    443%

The last line is the one that decides the design. Pickling is not a slow path,
it is a different architecture: 443% of a core means four cores doing nothing
but moving frames, which spends the entire benefit of splitting the tiers
before any of it arrives. So only a slot index and a little metadata ever go
on a queue.

Three consequences follow, and this class exists to make them hard to get
wrong:

  * The pool is fixed. Allocating per frame costs more than a full core at
    this frame rate. The reference implementation does allocate per frame,
    having tried a pool and abandoned it, and that is the one place its design
    should not be copied.
  * Readers map each block once and keep it mapped. Re-opening by name per
    frame measured 0.35ms of waste.
  * Producers write into the slot directly rather than building a frame
    elsewhere and copying it in. That is the difference between 0.98ms and
    3.08ms, and it is why `slot()` hands out the destination array instead of
    accepting a finished one.

Slots are recycled explicitly: a producer takes one from the free list, fills
it, and it stays out of circulation until a consumer releases it. Nothing
reclaims a slot automatically, because a consumer that is still reading a
frame whose slot has been handed back would read a different frame partway
through.
"""
from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass
from multiprocessing import shared_memory
from typing import Any

import numpy as np


@dataclass(frozen=True)
class RingSpec:
    """Everything a process needs to attach to a ring it did not create.

    Small and picklable on purpose: this is what crosses to a child process at
    spawn time, while the pixels stay where they are.
    """

    names: tuple[str, ...]
    frame_shape: tuple[int, int, int]
    blob_shape: tuple[int, ...] | None
    frame_dtype: str = "uint8"
    blob_dtype: str = "float32"

    @property
    def frame_bytes(self) -> int:
        return int(np.prod(self.frame_shape)) * np.dtype(self.frame_dtype).itemsize

    @property
    def blob_bytes(self) -> int:
        if self.blob_shape is None:
            return 0
        return int(np.prod(self.blob_shape)) * np.dtype(self.blob_dtype).itemsize


class FrameRing:
    """One producer's pool of slots, attachable read-only by any consumer.

    A slot holds a frame and, optionally, the preprocessed blob derived from
    it. Keeping both in one block means one index identifies everything a
    downstream tier needs: the blob for the model, the frame for cropping.
    """

    def __init__(self, spec: RingSpec, *, owner: bool = False):
        self.spec = spec
        self.owner = owner
        self._shms: list[shared_memory.SharedMemory] = []
        self.frames: list[np.ndarray] = []
        self.blobs: list[np.ndarray] = []

        blob_bytes = spec.blob_bytes
        for name in spec.names:
            shm = shared_memory.SharedMemory(name=name)
            self._shms.append(shm)
            if spec.blob_shape is not None:
                self.blobs.append(
                    np.ndarray(spec.blob_shape, np.dtype(spec.blob_dtype), buffer=shm.buf)
                )
            self.frames.append(
                np.ndarray(spec.frame_shape, np.dtype(spec.frame_dtype),
                           buffer=shm.buf, offset=blob_bytes)
            )

    @classmethod
    def create(cls, ctx, *, slots: int, frame_shape, blob_shape=None,
               frame_dtype="uint8", blob_dtype="float32") -> tuple["FrameRing", Any]:
        """Allocate a pool and the free list that hands its slots out.

        Returns the ring and a queue pre-filled with every slot index. The
        queue is the only admission control there is: a producer that cannot
        get a slot blocks, which is the backpressure that stops a fast decoder
        from running memory away from a slow consumer.
        """
        spec = RingSpec(names=(), frame_shape=tuple(frame_shape),
                        blob_shape=tuple(blob_shape) if blob_shape else None,
                        frame_dtype=frame_dtype, blob_dtype=blob_dtype)
        size = spec.frame_bytes + spec.blob_bytes
        names = []
        blocks = []
        try:
            for _ in range(slots):
                shm = shared_memory.SharedMemory(create=True, size=size)
                blocks.append(shm)
                names.append(shm.name)
        except BaseException:
            for shm in blocks:
                shm.close()
                shm.unlink()
            raise

        spec = RingSpec(names=tuple(names), frame_shape=spec.frame_shape,
                        blob_shape=spec.blob_shape, frame_dtype=frame_dtype,
                        blob_dtype=blob_dtype)
        free_q = ctx.Queue()
        for slot in range(slots):
            free_q.put(slot)

        ring = cls(spec, owner=True)
        # The creating handles are redundant now that the ring has mapped every
        # block itself, and holding two mappings per block just doubles the
        # bookkeeping at unlink time.
        for shm in blocks:
            shm.close()
        return ring, free_q

    def frame_slot(self, slot: int, shape: tuple[int, int, int] | None = None) -> np.ndarray:
        """The destination to decode into, cropped to the real frame size.

        Handed out rather than taking a finished frame, because writing here
        directly is what keeps the cost at 0.98ms instead of 3.08ms.
        """
        buf = self.frames[slot]
        if shape is None:
            return buf
        h, w = shape[0], shape[1]
        return buf[:h, :w]

    def blob_slot(self, slot: int) -> np.ndarray:
        return self.blobs[slot]

    def close(self) -> None:
        """Detach, and destroy the blocks if this process created them.

        Only the creator unlinks. Attaching processes call close(), which
        detaches without destroying, so a consumer shutting down early cannot
        pull the memory out from under everyone else. A "leaked shared_memory
        objects" warning at exit means an owner died before reaching here, not
        that a consumer failed to clean up.
        """
        for shm in self._shms:
            shm.close()
            if self.owner:
                shm.unlink()
        self._shms.clear()
        self.frames.clear()
        self.blobs.clear()

    def __enter__(self) -> "FrameRing":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
