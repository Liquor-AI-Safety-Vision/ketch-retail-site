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
"""
from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Union


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


_CLOSE = object()
_WriteOp = Union[_StartRun, _FinishRun, _CreateAlert, _AddAlertMedia]


class AlertOutbox:
    """Drop-in replacement for a *_store's write methods, backed by a queue.

    sink_factory is called once, from inside the writer thread, so a sqlite3
    connection built by it stays on the thread that will ever use it.
    """

    def __init__(self, sink_factory: Callable[[], Any]):
        self._queue: "queue.Queue[_WriteOp | object]" = queue.Queue()
        self._error: BaseException | None = None
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

    def close(self) -> None:
        """Block until every enqueued write has actually landed, then stop
        the writer thread. Re-raises the first write failure, if any -- a
        silently dropped alert is worse than a late one raising here."""
        self._queue.put(_CLOSE)
        self._thread.join()
        if self._error is not None:
            raise self._error

    def _drain(self, sink_factory: Callable[[], Any]) -> None:
        sink = sink_factory()
        try:
            while True:
                item = self._queue.get()
                if item is _CLOSE:
                    break
                if self._error is not None:
                    # A prior write already failed; close() will raise it.
                    # Draining the rest would keep writing after a torn
                    # state instead of stopping at the first problem.
                    continue
                try:
                    self._apply(sink, item)
                except BaseException as exc:  # noqa: BLE001 - surfaced via close()
                    self._error = exc
        finally:
            sink.close()

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
        else:  # pragma: no cover - exhaustive by construction
            raise TypeError(f"unknown outbox write op: {item!r}")
