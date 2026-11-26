"""Postgres (NeonDB) counterpart of SqliteAlertStore -- same call surface
(start_run/finish_run/create_alert/add_alert_media/close), same schema
(ai/sql/postgres_schema.sql mirrors local_dev_schema.sql table for
table), so AlertOutbox's sink_factory can point at either one without the
business tier or any pipeline class knowing which.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from ai.src.common.stream_uri import redact_stream_credentials
from ai.src.common.paths import schema_path


def seed_store_topology_pg(dsn: str, specs: list) -> None:
    """Postgres counterpart of sqlite_alert_store.seed_store_topology --
    same reason to exist: alerts/pipeline_runs FK into stores/cameras, and
    nothing else populates those two tables."""
    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(schema_path("postgres_schema.sql").read_text())

            stores_seen: dict[str, tuple[str, str]] = {}
            for spec in specs:
                stores_seen[spec.store_id] = (spec.store_display_name, spec.store_timezone)
            for store_id, (display_name, timezone) in stores_seen.items():
                cur.execute(
                    # Same fix as the SQLite seeder: the manifest's display_name,
                    # and refreshed on conflict so correcting it takes effect.
                    "INSERT INTO stores (store_id, display_name, timezone) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (store_id) DO UPDATE SET "
                    "display_name = excluded.display_name, timezone = excluded.timezone",
                    (store_id, display_name, timezone),
                )
            for spec in specs:
                cur.execute(
                    "INSERT INTO cameras "
                    "(camera_id, store_id, display_name, camera_type, source_type, source_uri) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (camera_id) DO UPDATE SET "
                    "store_id = excluded.store_id, display_name = excluded.display_name, "
                    "camera_type = excluded.camera_type, source_type = excluded.source_type, "
                    "source_uri = excluded.source_uri",
                    # Redacted for the same reason as the SQLite path: the
                    # camera password must not be written to the cloud DB.
                    (spec.camera_id, spec.store_id, spec.display_name, spec.camera_type,
                     spec.source_kind, redact_stream_credentials(spec.source_uri)),
                )
        conn.commit()
    finally:
        conn.close()


class PostgresAlertStore:
    """One connection, opened on first use and reopened after it is lost.

    It used to be opened once in __init__ and trusted for the life of the
    process. A run of a few minutes never noticed. A run that does not end
    will see that connection drop -- Neon suspending an idle compute, a
    network blip -- and every write after it would fail. So a connection-level
    error discards the connection, the caller (AlertOutbox) sees
    is_transient() and retries, and the retry opens a fresh one.

    Every INSERT is ON CONFLICT DO NOTHING on its client-generated id. That is
    what makes the retry safe: a commit can succeed on the server and the
    connection die before the acknowledgement arrives, and the retry must then
    be a no-op rather than a primary-key violation that drops the write.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        # Not connected here: the sink is built on the writer thread at
        # startup, and a Neon hiccup at that instant should be retried like
        # any other, not take the whole outbox down before it has started.
        self.conn = None

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    @staticmethod
    def is_transient(exc: BaseException) -> bool:
        import psycopg2

        # OperationalError covers a lost or refused connection and a server
        # going away mid-statement; InterfaceError is using a connection
        # psycopg2 already knows is closed. Everything else -- a constraint,
        # a type, a missing table -- will fail the same way on every retry.
        return isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError))

    def _connection(self):
        import psycopg2

        if self.conn is None or self.conn.closed:
            self.conn = psycopg2.connect(
                self.dsn, connect_timeout=10,
                # TCP keepalives, so a connection that died silently is noticed
                # in about a minute instead of on the kernel's multi-hour timeout,
                # with the writer thread blocked on it the whole time.
                keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3)
        return self.conn

    def _execute(self, sql: str, params: tuple) -> None:
        conn = self._connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
        except Exception as exc:
            if self.is_transient(exc):
                try:
                    conn.close()
                except Exception:
                    pass
                self.conn = None
            else:
                # The transaction is aborted; without a rollback every later
                # statement on this connection fails too.
                try:
                    conn.rollback()
                except Exception:
                    self.conn = None
            raise

    def start_run(self, camera_id: str, source_ref: str, status: str = "running",
                  metrics: dict[str, Any] | None = None, run_id: str | None = None) -> str:
        run_id = run_id or str(uuid.uuid4())
        self._execute(
            "INSERT INTO pipeline_runs "
            "(run_id, camera_id, started_at, stopped_at, status, source_ref, metrics_json) "
            "VALUES (%s, %s, now()::text, NULL, %s, %s, %s) "
            "ON CONFLICT (run_id) DO NOTHING",
            (run_id, camera_id, status, source_ref,
             json.dumps(metrics or {}, ensure_ascii=True, sort_keys=True)),
        )
        return run_id

    def finish_run(self, run_id: str, status: str, metrics: dict[str, Any] | None = None):
        self._execute(
            "UPDATE pipeline_runs SET stopped_at = now()::text, status = %s, "
            "metrics_json = %s WHERE run_id = %s",
            (status, json.dumps(metrics or {}, ensure_ascii=True, sort_keys=True), run_id),
        )

    def create_alert(
        self, *, store_id: str, camera_id: str, run_id: str | None, pipeline_type: str,
        event_type: str, severity: str, opened_at: str, identity_person_id: str | None,
        identity_label: str | None, identity_score: float | None, reason_codes: list[str],
        metadata: dict[str, Any] | None = None, status: str = "open",
        alert_id: str | None = None,
    ) -> str:
        alert_id = alert_id or str(uuid.uuid4())
        self._execute(
            "INSERT INTO alerts "
            "(alert_id, store_id, camera_id, run_id, pipeline_type, event_type, "
            " severity, status, opened_at, closed_at, identity_person_id, "
            " identity_label, identity_score, reason_codes_json, metadata_json) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s) "
            "ON CONFLICT (alert_id) DO NOTHING",
            (alert_id, store_id, camera_id, run_id, pipeline_type, event_type, severity,
             status, opened_at, identity_person_id, identity_label, identity_score,
             json.dumps(reason_codes, ensure_ascii=True),
             json.dumps(metadata or {}, ensure_ascii=True, sort_keys=True)),
        )
        return alert_id

    def add_alert_media(
        self, *, alert_id: str, kind: str, path: str, mime_type: str | None = None,
        started_at: str | None = None, ended_at: str | None = None,
        metadata: dict[str, Any] | None = None, media_id: str | None = None,
    ) -> str:
        media_id = media_id or str(uuid.uuid4())
        self._execute(
            "INSERT INTO alert_media "
            "(media_id, alert_id, kind, path, mime_type, started_at, ended_at, metadata_json) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (media_id) DO NOTHING",
            (media_id, alert_id, kind, path, mime_type, started_at, ended_at,
             json.dumps(metadata or {}, ensure_ascii=True, sort_keys=True)),
        )
        return media_id
