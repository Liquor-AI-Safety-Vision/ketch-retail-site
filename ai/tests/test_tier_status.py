"""A dead tier process must never leave the parent waiting.

This is the property the split driver depends on and could not previously
claim. Every tier reports ready, then reports stats, and the parent blocks on
both. Anything that stops a child in between -- an exception during model
load, a video bigger than the ring, a mismatched model dtype, an OOM kill --
used to turn into a parent blocked on a message that would never arrive. The
run did not fail; it stopped, silently, which is worse than failing because
there is nothing left to read.

Two failure modes need covering and they need different mechanisms:

  raises   the child can still run Python, so tier_guard sends a TierFailure
           carrying the traceback
  killed   the child cannot run anything (os._exit stands in for a segfault
           or the OOM killer), so nothing is ever sent and only a liveness
           check can notice

Both must end in TierProcessError rather than a hang, and the first must
carry the original traceback -- a timeout that says nothing about the cause
is only marginally better than the hang it replaced.

    python -m pytest ai/tests/test_tier_status.py -q
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.src.runtime.tier_status import (  # noqa: E402
    TierProcessError, await_message, tier_guard)


def _child_ok(ready_q, stats_q):
    with tier_guard("test", ready_q, stats_q):
        ready_q.put(("test", 1))
        stats_q.put("done")


def _child_raises_before_ready(ready_q, stats_q):
    with tier_guard("test", ready_q, stats_q):
        raise ValueError("model load blew up")


def _child_raises_after_ready(ready_q, stats_q):
    with tier_guard("test", ready_q, stats_q):
        ready_q.put(("test", 1))
        raise ValueError("frame bigger than the ring")


def _child_killed_before_ready(ready_q, stats_q):
    # Stands in for a segfault or the OOM killer: no exception handler runs,
    # so tier_guard never gets the chance to report anything.
    os._exit(9)


def _run(target):
    ctx = mp.get_context("spawn")
    ready_q, stats_q = ctx.Queue(), ctx.Queue()
    proc = ctx.Process(target=target, args=(ready_q, stats_q), name="tier-under-test")
    proc.start()
    try:
        yield_procs = [proc]
        return ready_q, stats_q, yield_procs, proc
    except BaseException:
        proc.terminate()
        raise


def test_healthy_child_delivers_both_messages():
    ready_q, stats_q, procs, proc = _run(_child_ok)
    try:
        assert await_message(ready_q, procs, what="ready", timeout_s=30) == ("test", 1)
        assert await_message(stats_q, procs, what="stats", timeout_s=30) == "done"
    finally:
        proc.join(timeout=10)


def test_child_raising_before_ready_reaches_the_parent():
    ready_q, stats_q, procs, proc = _run(_child_raises_before_ready)
    try:
        started = time.monotonic()
        with pytest.raises(TierProcessError) as excinfo:
            await_message(ready_q, procs, what="ready", timeout_s=30)
        elapsed = time.monotonic() - started
        # The traceback matters as much as the raise: a bare timeout would
        # tell the operator nothing about which tier failed or why.
        assert "model load blew up" in str(excinfo.value)
        assert elapsed < 25, f"took {elapsed:.1f}s -- should fail on the message, not the timeout"
    finally:
        proc.join(timeout=10)


def test_child_raising_after_ready_reaches_the_parent():
    ready_q, stats_q, procs, proc = _run(_child_raises_after_ready)
    try:
        assert await_message(ready_q, procs, what="ready", timeout_s=30) == ("test", 1)
        with pytest.raises(TierProcessError) as excinfo:
            await_message(stats_q, procs, what="stats", timeout_s=30)
        assert "frame bigger than the ring" in str(excinfo.value)
    finally:
        proc.join(timeout=10)


def test_hard_killed_child_is_caught_by_liveness_not_timeout():
    """The case no exception handler can report."""
    ready_q, stats_q, procs, proc = _run(_child_killed_before_ready)
    try:
        started = time.monotonic()
        with pytest.raises(TierProcessError) as excinfo:
            # A long timeout on purpose: if liveness were not checked, this
            # would sit here for the full 60s and the test would catch it.
            await_message(ready_q, procs, what="ready", timeout_s=60)
        elapsed = time.monotonic() - started
        assert "exit 9" in str(excinfo.value)
        assert elapsed < 20, f"took {elapsed:.1f}s -- liveness check did not fire"
    finally:
        proc.join(timeout=10)


def _child_reports_late(ready_q, stats_q):
    with tier_guard("test", ready_q, stats_q):
        ready_q.put(("test", 1))
        time.sleep(3.0)
        stats_q.put("done")


def test_no_deadline_waits_out_a_long_run_but_a_deadline_does_not():
    """Decoder stats arrive when the streams end. With the old fixed 600s
    deadline on that wait, every run longer than ten minutes was torn down
    as a 'hang'. Scaled down here: 3s of work, against a 1s deadline and
    against none."""
    ready_q, stats_q, procs, proc = _run(_child_reports_late)
    try:
        assert await_message(ready_q, procs, what="ready", timeout_s=30) == ("test", 1)
        with pytest.raises(TierProcessError) as excinfo:
            await_message(stats_q, procs, what="stats", timeout_s=1)
        assert "timed out" in str(excinfo.value)
    finally:
        proc.join(timeout=10)

    ready_q, stats_q, procs, proc = _run(_child_reports_late)
    try:
        assert await_message(ready_q, procs, what="ready", timeout_s=30) == ("test", 1)
        assert await_message(stats_q, procs, what="stats", timeout_s=None) == "done"
    finally:
        proc.join(timeout=10)
