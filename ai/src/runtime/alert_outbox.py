"""Decouples pipeline alert/media writes from where they actually land.

A pipeline thread calling create_alert() needs the alert_id back immediately
-- it uses it as the directory name for the snapshot/clip it writes next --
but does not need the row to exist in the store at that instant; nothing in
a run reads an alert back before the run ends. That gap is exactly what lets
the write move off the pipeline thread: AlertOutbox has the same call
surface as SqliteAlertStore (start_run/finish_run/create_alert/
add_alert_media/close), generates the id itself and returns it right away,
and hands the actual write to one dedicated writer thread draining a queue.

This matters even with today's local SQLite sink, not just a future
Postgres/R2 one: a pipeline thread's hot path no longer waits on disk I/O or
a lock held by another camera's write. It matters more once the sink is
network calls to Neon/R2 -- that is the seam a future cloud-db backend plugs
into via sink_factory, without pipelines or business_tier changing at all.

One writer thread, not a pool: the FK-shaped ordering pipelines depend on
(an alert's row must exist before its add_alert_media row referencing it)
only holds because a single thread drains the queue in the order things were
put() -- one camera's own sequential calls stay in that relative order, and
sqlite3 connections are thread-affine besides, so the sink can only ever be
touched from the one thread that created it.

A failed write is retried or dropped, never allowed to stop the writes after
it. This used to remember the first failure and skip every later write until
close() -- harmless in a three-minute benchmark, and in a run that never ends
it meant one dropped Neon connection silently discarded every alert for the
rest of the process's life, with nothing logged until shutdown. Now the sink
says which failures are worth waiting out (`sink.is_transient(exc)`: a lost
connection, an R2 timeout) and those are retried in place with backoff, which
keeps the queue order the FK depends on. Anything else is logged and dropped
on its own, along with any media write for an alert whose row never landed.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Union

logger = logging.getLogger(__name__)

#: Backoff between retries of a transient failure, doubling up to the cap.
RETRY_BACKOFF_S = 0.5
RETRY_BACKOFF_MAX_S = 30.0
#: Once close() is called, how long transient failures may still be retried.
#: While running, an outage is waited out however long it lasts -- the
#: alternative is dropping alerts. At shutdown something has to give, and the
#: parent terminates a business process that has not exited well before this
#: would let it wait forever.
CLOSE_RETRY_BUDGET_S = 120.0


@dataclass
class _StartRun:
    run_id: str
    camera_id: str
    source_ref: str
    status: str
    metrics: dict[str, Any]


@dataclass
class _FinishRun:
    run_id: str
    status: str
    metrics: dict[str, Any]


@dataclass
class _CreateAlert:
    alert_id: str
    kwargs: dict[str, Any]


@dataclass
class _AddAlertMedia:
    media_id: str
    kwargs: dict[str, Any]


@dataclass
class _TouchCamera:
    camera_id: str
    at: str


_CLOSE = object()
_WriteOp = Union[_StartRun, _FinishRun, _CreateAlert, _AddAlertMedia, _TouchCamera]


class AlertOutbox:
    """Drop-in replacement for a *_store's write methods, backed by a queue.

    sink_factory is called once, from inside the writer thread, so a sqlite3
    connection built by it stays on the thread that will ever use it.
    """

    def __init__(self, sink_factory: Callable[[], Any]):
        self._queue: "queue.Queue[_WriteOp | object]" = queue.Queue()
        self._error: BaseException | None = None
        self._failures = 0
        self._close_deadline: float | None = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._drain, args=(sink_factory,), daemon=True, name="alert-outbox-writer")
        self._thread.start()

    def start_run(self, camera_id: str, source_ref: str, status: str = "running",
                  metrics: dict[str, Any] | None = None) -> str:
        run_id = str(uuid.uuid4())
        self._queue.put(_StartRun(run_id, camera_id, source_ref, status, metrics or {}))
        return run_id

    def finish_run(self, run_id: str, status: str, metrics: dict[str, Any] | None = None) -> None:
        self._queue.put(_FinishRun(run_id, status, metrics or {}))

    def create_alert(self, **kwargs: Any) -> str:
        alert_id = str(uuid.uuid4())
        self._queue.put(_CreateAlert(alert_id, kwargs))
        return alert_id

    def add_alert_media(self, **kwargs: Any) -> str:
        media_id = str(uuid.uuid4())
        self._queue.put(_AddAlertMedia(media_id, kwargs))
        return media_id

    def touch_camera(self, camera_id: str, at: str) -> None:
        """Best effort: the next heartbeat supersedes this one, so it is never
        retried and a failure is not a dropped write (see _drain)."""
        self._queue.put(_TouchCamera(camera_id, at))

    def close(self) -> None:
        """Block until every enqueued write has landed or been given up on,
        then stop the writer thread. Re-raises the first write failure, if
        any -- a dropped alert was already logged when it happened, and still
        must not look like a clean shutdown. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        self._close_deadline = time.monotonic() + CLOSE_RETRY_BUDGET_S
        self._queue.put(_CLOSE)
        self._thread.join()
        if self._error is not None:
            if self._failures > 1:
                logger.error("alert outbox: %d writes were dropped in total", self._failures)
            raise self._error

    def _drain(self, sink_factory: Callable[[], Any]) -> None:
        try:
            sink = sink_factory()
        except BaseException as exc:  # noqa: BLE001 - surfaced via close()
            # No sink means nothing below can land. Keep consuming so put()
            # never backs up behind a dead writer, and let close() raise.
            logger.error("alert outbox: could not build the sink: %r", exc)
            self._error = exc
            while self._queue.get() is not _CLOSE:
                self._failures += 1
            return
        is_transient = getattr(sink, "is_transient", None) or (lambda _exc: False)
        warm = getattr(sink, "warm", None)
        if warm is not None:
            # Open network connections now rather than on the first alert.
            # Best effort: a failure here is retried by the first real write.
            try:
                warm()
            except Exception as exc:  # noqa: BLE001
                logger.warning("alert outbox: could not pre-connect the sink: %r", exc)
        # Alerts whose row was given up on. Their media rows would fail the
        # FK anyway, and for the cloud sink would first upload a file nothing
        # will ever reference.
        dropped_alerts: set[str] = set()
        try:
            while True:
                item = self._queue.get()
                if item is _CLOSE:
                    break
                if isinstance(item, _TouchCamera):
                    # Never retried and never counted as a failure: an outage
                    # must not hold alerts behind stale heartbeats, and a
                    # missed one is replaced by the next ~30 s later.
                    try:
                        self._apply(sink, item)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("alert outbox: camera heartbeat skipped: %r", exc)
                    continue
                if (isinstance(item, _AddAlertMedia)
                        and item.kwargs.get("alert_id") in dropped_alerts):
                    self._record_failure(item, RuntimeError(
                        f"alert {item.kwargs['alert_id']} was never written, "
                        f"so its media is skipped"))
                    continue
                if not self._apply_with_retry(sink, item, is_transient):
                    if isinstance(item, _CreateAlert):
                        dropped_alerts.add(item.alert_id)
        finally:
            sink.close()

    def _apply_with_retry(self, sink: Any, item: _WriteOp, is_transient) -> bool:
        backoff = RETRY_BACKOFF_S
        attempt = 0
        while True:
            attempt += 1
            try:
                self._apply(sink, item)
                if attempt > 1:
                    logger.warning("alert outbox: %s landed after %d attempts",
                                   type(item).__name__, attempt)
                return True
            except BaseException as exc:  # noqa: BLE001 - logged, then retried or dropped
                closing_expired = (self._close_deadline is not None
                                   and time.monotonic() >= self._close_deadline)
                if not is_transient(exc) or closing_expired:
                    self._record_failure(item, exc)
                    return False
                logger.warning(
                    "alert outbox: %s failed (attempt %d, %d writes queued behind it), "
                    "retrying in %.1fs: %r",
                    type(item).__name__, attempt, self._queue.qsize(), backoff, exc)
                time.sleep(backoff)
                backoff = min(backoff * 2.0, RETRY_BACKOFF_MAX_S)

    def _record_failure(self, item: _WriteOp, exc: BaseException) -> None:
        self._failures += 1
        if self._error is None:
            self._error = exc
        logger.error("alert outbox: dropped %s: %r", type(item).__name__, exc)

    @staticmethod
    def _apply(sink: Any, item: _WriteOp) -> None:
        if isinstance(item, _StartRun):
            sink.start_run(item.camera_id, item.source_ref, item.status, item.metrics,
                            run_id=item.run_id)
        elif isinstance(item, _FinishRun):
            sink.finish_run(item.run_id, item.status, item.metrics)
        elif isinstance(item, _CreateAlert):
            sink.create_alert(alert_id=item.alert_id, **item.kwargs)
        elif isinstance(item, _AddAlertMedia):
            sink.add_alert_media(media_id=item.media_id, **item.kwargs)
        elif isinstance(item, _TouchCamera):
            touch = getattr(sink, "touch_camera", None)
            if touch is not None:
                touch(item.camera_id, item.at)
        else:  # pragma: no cover - exhaustive by construction
            raise TypeError(f"unknown outbox write op: {item!r}")
