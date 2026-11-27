from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
import time
from typing import Any

import fcntl

from ai.src.common.stream_uri import redact_stream_credentials
from ai.src.common.paths import schema_path
from ai.src.infra.camera_metadata import camera_metadata_json


@contextmanager
def _bootstrap_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def seed_store_topology(db_path: str | Path, specs: list) -> None:
    """Populate stores and cameras from the run's own CameraStreamSpecs.

    alerts and pipeline_runs both carry foreign keys into these two tables,
    and nothing else in the split pipeline ever wrote a row into either --
    the FK was declared, never satisfied, which is why turning the pragma on
    used to break the first start_run() on any fresh database. Called once
    from the parent, before any tier process starts writing, using the same
    specs that were merged (possibly across several stores' manifests -- see
    camera_specs_from_manifests) to build the rings.

    INSERT OR REPLACE, not INSERT: re-running against an existing database
    (the common case -- every restart reuses the same db_path) must update a
    camera's display_name or source_uri if the manifest changed, not fail on
    a duplicate key or silently keep stale data.
    """
    db_file = Path(str(db_path))
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        with _bootstrap_lock(db_file.with_suffix(db_file.suffix + ".bootstrap.lock")):
            time.sleep(0.01)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(schema_path("local_dev_schema.sql").read_text())

            stores_seen: dict[str, tuple[str, str]] = {}
            for spec in specs:
                stores_seen[spec.store_id] = (spec.store_display_name, spec.store_timezone)
            for store_id, (display_name, timezone) in stores_seen.items():
                conn.execute(
                    # display_name is the manifest's, not the store_id repeated.
                    # It used to be the latter, so every dashboard reading this
                    # table showed "store_1" where the manifest said "Store 1"
                    # -- and the ON CONFLICT clause did not refresh it either,
                    # so correcting the manifest changed nothing on a restart.
                    "INSERT INTO stores (store_id, display_name, timezone) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT (store_id) DO UPDATE SET "
                    "display_name = excluded.display_name, timezone = excluded.timezone",
                    (store_id, display_name, timezone),
                )
            for spec in specs:
                conn.execute(
                    "INSERT INTO cameras "
                    "(camera_id, store_id, display_name, camera_type, source_type, source_uri, metadata_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (camera_id) DO UPDATE SET "
                    "store_id = excluded.store_id, display_name = excluded.display_name, "
                    "camera_type = excluded.camera_type, source_type = excluded.source_type, "
                    "source_uri = excluded.source_uri, metadata_json = excluded.metadata_json",
                    # Redacted: an rtsp:// source carries the camera password
                    # inline, and this row is read back by dashboards and
                    # copied into every backup of the database.
                    (spec.camera_id, spec.store_id, spec.display_name, spec.camera_type,
                     spec.source_kind, redact_stream_credentials(spec.source_uri),
                     camera_metadata_json(spec)),
                )
            conn.commit()
    finally:
        conn.close()


class SqliteAlertStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=30.0)
        self.conn.execute("PRAGMA busy_timeout=30000")
        # foreign_keys=ON was tried and reverted once, because it was wrong on
        # both ends: pipeline_runs.camera_id referenced cameras while nothing
        # seeded stores/cameras (every start_run() died with "FOREIGN KEY
        # constraint failed" on a fresh db, verified before it reached a GPU
        # run), and alerts -- the table that mattered most -- declared no
        # foreign keys at all, so a bogus alert inserted happily with the
        # pragma on regardless.
        #
        # Both are fixed now: alerts declares FKs to stores and cameras (see
        # local_dev_schema.sql), and seed_store_topology() populates stores
        # and cameras from the run's own CameraStreamSpecs before any tier
        # starts writing. The pragma is a plain assertion at that point, not
        # a guess.
        self.conn.execute("PRAGMA foreign_keys=ON")
        with _bootstrap_lock(db_file.with_suffix(db_file.suffix + ".bootstrap.lock")):
            # Give the connection a short grace window after taking the lock so
            # concurrent openers that were already blocked on sqlite can drain.
            time.sleep(0.01)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self._ensure_schema()

    def close(self):
        self.conn.close()

    def _ensure_schema(self):
        self.conn.executescript(schema_path("local_dev_schema.sql").read_text())
        # A database created before cameras.last_frame_at existed. SQLite has
        # no ADD COLUMN IF NOT EXISTS.
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(cameras)")}
        if "last_frame_at" not in columns:
            self.conn.execute("ALTER TABLE cameras ADD COLUMN last_frame_at TEXT")
        self.conn.commit()

    def touch_camera(self, camera_id: str, at: str) -> None:
        """Record that a frame of this camera was just processed."""
        self.conn.execute("UPDATE cameras SET last_frame_at=? WHERE camera_id=?", (at, camera_id))
        self.conn.commit()

    def start_run(self, camera_id: str, source_ref: str, status: str = "running",
                  metrics: dict[str, Any] | None = None, run_id: str | None = None) -> str:
        run_id = run_id or str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO pipeline_runs
            (run_id, camera_id, started_at, stopped_at, status, source_ref, metrics_json)
            VALUES (?, ?, datetime('now'), NULL, ?, ?, ?)
            """,
            (
                run_id,
                camera_id,
                status,
                source_ref,
                json.dumps(metrics or {}, ensure_ascii=True, sort_keys=True),
            ),
        )
        self.conn.commit()
        return run_id

    def finish_run(self, run_id: str, status: str, metrics: dict[str, Any] | None = None):
        self.conn.execute(
            """
            UPDATE pipeline_runs
            SET stopped_at = datetime('now'),
                status = ?,
                metrics_json = ?
            WHERE run_id = ?
            """,
            (
                status,
                json.dumps(metrics or {}, ensure_ascii=True, sort_keys=True),
                run_id,
            ),
        )
        self.conn.commit()

    def create_alert(
        self,
        *,
        store_id: str,
        camera_id: str,
        run_id: str | None,
        pipeline_type: str,
        event_type: str,
        severity: str,
        opened_at: str,
        identity_person_id: str | None,
        identity_label: str | None,
        identity_score: float | None,
        reason_codes: list[str],
        metadata: dict[str, Any] | None = None,
        status: str = "open",
        alert_id: str | None = None,
    ) -> str:
        alert_id = alert_id or str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO alerts
            (alert_id, store_id, camera_id, run_id, pipeline_type, event_type,
             severity, status, opened_at, closed_at, identity_person_id,
             identity_label, identity_score, reason_codes_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                alert_id,
                store_id,
                camera_id,
                run_id,
                pipeline_type,
                event_type,
                severity,
                status,
                opened_at,
                identity_person_id,
                identity_label,
                identity_score,
                json.dumps(reason_codes, ensure_ascii=True),
                json.dumps(metadata or {}, ensure_ascii=True, sort_keys=True),
            ),
        )
        self.conn.commit()
        return alert_id

    def add_alert_media(
        self,
        *,
        alert_id: str,
        kind: str,
        path: str,
        mime_type: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        metadata: dict[str, Any] | None = None,
        media_id: str | None = None,
    ) -> str:
        media_id = media_id or str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO alert_media
            (media_id, alert_id, kind, path, mime_type, started_at, ended_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                media_id,
                alert_id,
                kind,
                path,
                mime_type,
                started_at,
                ended_at,
                json.dumps(metadata or {}, ensure_ascii=True, sort_keys=True),
            ),
        )
        self.conn.commit()
        return media_id
