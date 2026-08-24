from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
import time
from typing import Any

import fcntl


@contextmanager
def _bootstrap_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class SqliteAlertStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=30.0)
        self.conn.execute("PRAGMA busy_timeout=30000")
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
        root = Path(__file__).resolve().parents[3]
        schema_path = root / "backend/sql/local_dev_schema.sql"
        if not schema_path.exists():
            return
        self.conn.executescript(schema_path.read_text())
        self.conn.commit()

    def start_run(self, camera_id: str, source_ref: str, status: str = "running",
                  metrics: dict[str, Any] | None = None) -> str:
        run_id = str(uuid.uuid4())
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
    ) -> str:
        alert_id = str(uuid.uuid4())
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
    ) -> str:
        media_id = str(uuid.uuid4())
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
