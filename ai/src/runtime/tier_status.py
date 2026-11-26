"""A failure in any tier process has to reach the parent, or the run hangs.

Every tier process follows the same shape: set up, report ready, wait for
go, work, report stats. The parent blocks on ready_q while they load and on
stats_q while they run. Neither get() has a timeout, so anything that stops
a child before its next put() -- an exception during model load, a video
larger than the ring, a dtype mismatch, a hard crash -- leaves the parent
waiting on a message that will never come. The run does not fail; it stops,
with no error and no output, which is the worst way for it to end because
there is nothing to read afterwards.

This closes that with two halves that have to be used together:

  child   tier_guard() wraps the whole body. Any exception becomes a
          TierFailure put on *both* queues, so the parent unblocks whichever
          one it happens to be waiting on.

  parent  await_message() polls with a short timeout and, between polls,
          checks whether any child has died. That covers what the child half
          cannot: a segfault or an OOM kill runs no Python at all, so no
          TierFailure is ever sent. Liveness is the only signal left there.

Both halves are needed. The failure channel gives a real traceback; the
liveness check guarantees the parent stops waiting even when there is no
traceback to be had.
"""
from __future__ import annotations

import queue as _queue
import sys
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class TierFailure:
    """A child telling the parent why it is not going to finish."""

    role: str
    error: str

    def __repr__(self) -> str:  # keeps the driver's stats dump readable
        first = self.error.strip().splitlines()[-1] if self.error.strip() else "?"
        return f"TierFailure(role={self.role!r}, {first})"


class TierProcessError(RuntimeError):
    """Raised in the parent when a tier process failed or died."""


@contextmanager
def tier_guard(role: str, ready_q, stats_q):
    """Wrap a tier process body so its failure reaches the parent.

    Puts the failure on both queues because the parent's position is not
    knowable from here: it is on ready_q if this died during load, and on
    stats_q if it died mid-run. A spare message on the queue the parent had
    already moved past is harmless -- the queue is discarded at the end of
    the run either way.

    Also where a tier stops listening to Ctrl+C. The terminal delivers SIGINT
    to every process in its foreground group at once, so without this each
    tier would die mid-frame on its own KeyboardInterrupt while the parent
    was trying to stop them in order -- and the business tier's unflushed
    alerts would go with it. Only the parent handles it (see
    run_split_pipeline.main); the tiers stop when told to, through the same
    queues that end a finite run. SIGTERM is untouched, so terminate() works.

    Ignoring Ctrl+C removes the one thing that used to take a tier down along
    with a parent that died abruptly (kill -9, an error during its own
    shutdown). So each tier also watches its parent and exits when it is
    gone; otherwise an inference tier would outlive it holding its CUDA
    context, and the next start on the same GPU would inherit the orphan.
    """
    import multiprocessing
    import os
    import signal
    import threading

    from ai.src.common.logging_setup import configure_logging

    # A spawned tier is a fresh interpreter: the parent's logging setup does
    # not carry over, and without this every INFO line a tier logs was
    # silently dropped (Python's last-resort handler prints WARNING and up).
    # The level and log file still come from the environment, which spawn
    # does inherit.
    configure_logging()
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    parent = multiprocessing.parent_process()
    if parent is not None:
        def _exit_with_parent():
            parent.join()
            print(f"[{role}] parent process is gone; exiting", file=sys.stderr)
            os._exit(1)

        threading.Thread(target=_exit_with_parent, daemon=True,
                         name=f"{role}-parent-watchdog").start()
    try:
        yield
    except BaseException:
        failure = TierFailure(role=role, error=traceback.format_exc())
        print(f"[{role}] failed:\n{failure.error}", file=sys.stderr)
        for q in (ready_q, stats_q):
            try:
                q.put(failure)
            except BaseException:
                # A queue already closed by a parent that gave up. Nothing
                # useful left to do, and raising here would replace the real
                # traceback with a meaningless one.
                pass
        raise


def await_message(q, procs, *, what: str, timeout_s: float | None = 600.0,
                  poll_s: float = 0.5):
    """Get one message, but give up if a child died or nothing ever comes.

    `procs` is every tier process. Checking their exit codes between polls is
    what catches the deaths no exception handler can report -- a killed or
    segfaulted process sends nothing at all.

    timeout_s=None waits as long as every child stays alive. That is the right
    bound for a wait whose length is the length of the run itself -- decoder
    stats arrive only when the streams end, which for strategy=forever is
    never. A fixed deadline there did not detect a hang, it capped every run
    at ten minutes. Liveness is still checked, so a crash still surfaces.
    """
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while True:
        try:
            item = q.get(timeout=poll_s)
        except _queue.Empty:
            dead = [p for p in procs if p.exitcode not in (None, 0)]
            if dead:
                raise TierProcessError(
                    f"waiting for {what}, but {len(dead)} tier process(es) exited: "
                    + ", ".join(f"{p.name} (exit {p.exitcode})" for p in dead))
            if deadline is not None and time.monotonic() > deadline:
                alive = [p.name for p in procs if p.is_alive()]
                raise TierProcessError(
                    f"timed out after {timeout_s:.0f}s waiting for {what}; "
                    f"still alive: {', '.join(alive) or 'none'}")
            continue
        if isinstance(item, TierFailure):
            raise TierProcessError(f"{item.role} tier failed while producing {what}:\n{item.error}")
        return item
