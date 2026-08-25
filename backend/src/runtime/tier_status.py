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
    """
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


def await_message(q, procs, *, what: str, timeout_s: float = 600.0, poll_s: float = 0.5):
    """Get one message, but give up if a child died or nothing ever comes.

    `procs` is every tier process. Checking their exit codes between polls is
    what catches the deaths no exception handler can report -- a killed or
    segfaulted process sends nothing at all.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            item = q.get(timeout=poll_s)
        except _queue.Empty:
            dead = [p for p in procs if p.exitcode not in (None, 0)]
            if dead:
                raise TierProcessError(
                    f"waiting for {what}, but {len(dead)} tier process(es) exited: "
                    + ", ".join(f"{p.name} (exit {p.exitcode})" for p in dead))
            if time.monotonic() > deadline:
                alive = [p.name for p in procs if p.is_alive()]
                raise TierProcessError(
                    f"timed out after {timeout_s:.0f}s waiting for {what}; "
                    f"still alive: {', '.join(alive) or 'none'}")
            continue
        if isinstance(item, TierFailure):
            raise TierProcessError(f"{item.role} tier failed while producing {what}:\n{item.error}")
        return item
