"""Measure what it costs to move a frame between processes.

The proposed split puts decoding and inference in separate processes, which
only helps if handing a frame across that boundary is cheap. Done naively it
is not: a 1080p frame is 6.2MB, and pickling that through a
multiprocessing.Queue copies it twice and serialises it in between. Done via
shared memory only a name and a shape cross the queue, and the pixels never
move.

This measures both, on this machine, before any of the architecture is
written. It also checks the two things a container can quietly break:
/dev/shm large enough to hold frames in flight, and spawn working, which
CUDA requires because it cannot fork.

    python backend/src/app/diag_ipc.py
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import time
from multiprocessing import shared_memory

import numpy as np

FRAME_H, FRAME_W = 1080, 1920
FRAME_BYTES = FRAME_H * FRAME_W * 3


def _consumer_shm(inbox: mp.Queue, outbox: mp.Queue) -> None:
    """Attach to each block by name and read it, never copying the pixels."""
    while True:
        item = inbox.get()
        if item is None:
            return
        name, shape, dtype = item
        shm = shared_memory.SharedMemory(name=name)
        arr = np.ndarray(shape, dtype=dtype, buffer=shm.buf)
        checksum = int(arr[0, 0, 0]) + int(arr[-1, -1, -1])
        shm.close()
        outbox.put(checksum)


def _consumer_shm_cached(inbox: mp.Queue, outbox: mp.Queue) -> None:
    """Same, but map each block once and keep it mapped.

    The reference implementation re-opens the block by name on every frame,
    which means an mmap and munmap per frame. With a fixed pool the set of
    blocks is small and known, so the mapping can be kept.
    """
    cache: dict[str, np.ndarray] = {}
    handles = []
    try:
        while True:
            item = inbox.get()
            if item is None:
                return
            name, shape, dtype = item
            arr = cache.get(name)
            if arr is None:
                shm = shared_memory.SharedMemory(name=name)
                handles.append(shm)
                arr = np.ndarray(shape, dtype=dtype, buffer=shm.buf)
                cache[name] = arr
            outbox.put(int(arr[0, 0, 0]) + int(arr[-1, -1, -1]))
    finally:
        for shm in handles:
            shm.close()


def _consumer_echo(inbox: mp.Queue, outbox: mp.Queue) -> None:
    """Acknowledge without touching a frame, to isolate the queue's own cost."""
    while True:
        if inbox.get() is None:
            return
        outbox.put(1)


def _consumer_pickle(inbox: mp.Queue, outbox: mp.Queue) -> None:
    while True:
        arr = inbox.get()
        if arr is None:
            return
        outbox.put(int(arr[0, 0, 0]) + int(arr[-1, -1, -1]))


def _time_round_trips(ctx, target, feed, n: int) -> float:
    inbox, outbox = ctx.Queue(), ctx.Queue()
    proc = ctx.Process(target=target, args=(inbox, outbox))
    proc.start()
    try:
        feed(inbox, outbox, 5)  # warm the child and the import machinery
        start = time.perf_counter()
        feed(inbox, outbox, n)
        elapsed = time.perf_counter() - start
    finally:
        inbox.put(None)
        proc.join(timeout=10)
        if proc.is_alive():
            proc.terminate()
    return elapsed / n


def bench_shm(ctx, frame: np.ndarray, n: int, pool: int, *, cached: bool = False,
              copy: bool = True) -> float:
    """Hand frames over through a pool of pre-allocated shared blocks.

    Allocating a block per frame is what the reference implementation does,
    and it is simpler, but it pays the allocation every frame. A pool reuses
    them, which is what a real ring buffer would do.
    """
    blocks = [shared_memory.SharedMemory(create=True, size=FRAME_BYTES) for _ in range(pool)]
    views = [np.ndarray(frame.shape, dtype=frame.dtype, buffer=b.buf) for b in blocks]

    def feed(inbox, outbox, count):
        for i in range(count):
            slot = i % pool
            if copy:
                np.copyto(views[slot], frame)
            inbox.put((blocks[slot].name, frame.shape, frame.dtype))
            outbox.get()

    consumer = _consumer_shm_cached if cached else _consumer_shm
    try:
        return _time_round_trips(ctx, consumer, feed, n)
    finally:
        for b in blocks:
            b.close()
            b.unlink()


def bench_shm_fresh(ctx, frame: np.ndarray, n: int) -> float:
    """Allocate a new block per frame, the way the reference does."""
    def feed(inbox, outbox, count):
        for _ in range(count):
            shm = shared_memory.SharedMemory(create=True, size=FRAME_BYTES)
            view = np.ndarray(frame.shape, dtype=frame.dtype, buffer=shm.buf)
            np.copyto(view, frame)
            inbox.put((shm.name, frame.shape, frame.dtype))
            outbox.get()
            shm.close()
            shm.unlink()

    return _time_round_trips(ctx, _consumer_shm, feed, n)


def bench_pickle(ctx, frame: np.ndarray, n: int) -> float:
    def feed(inbox, outbox, count):
        for _ in range(count):
            inbox.put(frame)
            outbox.get()

    return _time_round_trips(ctx, _consumer_pickle, feed, n)


def bench_queue_only(ctx, frame: np.ndarray, n: int) -> float:
    """Round-trip a name and a shape, with no frame data involved at all."""
    def feed(inbox, outbox, count):
        for _ in range(count):
            inbox.put(("psm_placeholder", frame.shape, frame.dtype))
            outbox.get()

    return _time_round_trips(ctx, _consumer_echo, feed, n)


def bench_copy_only(frame: np.ndarray, n: int) -> float:
    """Cost of writing one frame into a shared block, with no IPC at all.

    This is the part a decoder can delete outright by decoding straight into
    the shared block instead of decoding elsewhere and copying in.
    """
    shm = shared_memory.SharedMemory(create=True, size=FRAME_BYTES)
    try:
        view = np.ndarray(frame.shape, dtype=frame.dtype, buffer=shm.buf)
        np.copyto(view, frame)
        start = time.perf_counter()
        for _ in range(n):
            np.copyto(view, frame)
        return (time.perf_counter() - start) / n
    finally:
        shm.close()
        shm.unlink()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--frames", type=int, default=200)
    p.add_argument("--pool", type=int, default=16)
    p.add_argument("--rate", type=float, default=112.0,
                   help="Aggregate frames per second to express the cost against.")
    args = p.parse_args()

    ctx = mp.get_context("spawn")  # what CUDA forces; fork is not an option
    frame = np.random.randint(0, 255, (FRAME_H, FRAME_W, 3), dtype=np.uint8)
    mb = FRAME_BYTES / 1e6
    print(f"frame {FRAME_W}x{FRAME_H}x3 = {mb:.1f} MB, start method = {ctx.get_start_method()}\n")

    results = {
        "queue round-trip only, no frame": bench_queue_only(ctx, frame, args.frames),
        "write one frame into a shared block": bench_copy_only(frame, args.frames),
        f"shared memory, pool of {args.pool}": bench_shm(ctx, frame, args.frames, args.pool),
        "  ... reader keeps blocks mapped": bench_shm(ctx, frame, args.frames, args.pool, cached=True),
        "  ... and decoder writes in place": bench_shm(ctx, frame, args.frames, args.pool, cached=True, copy=False),
        "shared memory, fresh block per frame": bench_shm_fresh(ctx, frame, args.frames),
        "pickle through mp.Queue": bench_pickle(ctx, frame, args.frames),
    }

    print(f"{'transport':<38s} {'per frame':>10s} {'throughput':>12s} {'at ' + str(int(args.rate)) + ' fps':>12s}")
    for name, seconds in results.items():
        busy = seconds * args.rate * 100.0
        print(f"{name:<38s} {seconds*1000:8.3f} ms {mb/seconds:9.0f} MB/s {busy:10.1f}% cpu")

    floor = results["queue round-trip only, no frame"]
    pooled = results[f"shared memory, pool of {args.pool}"]
    worst = results["pickle through mp.Queue"]
    print(f"\nshared memory with a pool is {worst/pooled:.1f}x cheaper than pickling the array.")
    print(f"Of its {pooled*1000:.3f} ms, {floor*1000:.3f} ms is the queue itself and cannot be removed;")
    print(f"the rest is the copy, which a decoder writing straight into the block would not pay.")


if __name__ == "__main__":
    main()
