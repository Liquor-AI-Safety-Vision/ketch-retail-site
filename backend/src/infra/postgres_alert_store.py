"""Postgres (NeonDB) counterpart of SqliteAlertStore -- same call surface
(start_run/finish_run/create_alert/add_alert_media/close), same schema
(backend/sql/postgres_schema.sql mirrors local_dev_schema.sql table for
table), so AlertOutbox's sink_factory can point at either one without the
business tier or any pipeline class knowing which.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any


def seed_store_topology_pg(dsn: str, specs: list) -> None:
    """Postgres counterpart of sqlite_alert_store.seed_store_topology --
    same reason to exist: alerts/pipeline_runs FK into stores/cameras, and
    nothing else populates those two tables."""
    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            root = Path(__file__).resolve().parents[3]
            schema_path = root / "backend/sql/postgres_schema.sql"
            cur.execute(schema_path.read_text())

            stores_seen: dict[str, str] = {}
            for spec in specs:
                stores_seen[spec.store_id] = spec.store_timezone
            for store_id, timezone in stores_seen.items():
                cur.execute(
                    "INSERT INTO stores (store_id, display_name, timezone) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (store_id) DO UPDATE SET timezone = excluded.timezone",
                    (store_id, store_id, timezone),
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
                    (spec.camera_id, spec.store_id, spec.display_name, spec.camera_type,
                     spec.source_kind, spec.source_uri),
                )
        conn.commit()
    finally:
        conn.close()


class PostgresAlertStore:
    def __init__(self, dsn: str):
        import psycopg2

        self.conn = psycopg2.connect(dsn)

    def close(self):
        self.conn.close()

    def start_run(self, camera_id: str, source_ref: str, status: str = "running",
                  metrics: dict[str, Any] | None = None, run_id: str | None = None) -> str:
        run_id = run_id or str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pipeline_runs "
                "(run_id, camera_id, started_at, stopped_at, status, source_ref, metrics_json) "
                "VALUES (%s, %s, now()::text, NULL, %s, %s, %s)",
                (run_id, camera_id, status, source_ref,
                 json.dumps(metrics or {}, ensure_ascii=True, sort_keys=True)),
            )
        self.conn.commit()
        return run_id

    def finish_run(self, run_id: str, status: str, metrics: dict[str, Any] | None = None):
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE pipeline_runs SET stopped_at = now()::text, status = %s, "
                "metrics_json = %s WHERE run_id = %s",
                (status, json.dumps(metrics or {}, ensure_ascii=True, sort_keys=True), run_id),
            )
        self.conn.commit()

    def create_alert(
        self, *, store_id: str, camera_id: str, run_id: str | None, pipeline_type: str,
        event_type: str, severity: str, opened_at: str, identity_person_id: str | None,
        identity_label: str | None, identity_score: float | None, reason_codes: list[str],
        metadata: dict[str, Any] | None = None, status: str = "open",
        alert_id: str | None = None,
    ) -> str:
        alert_id = alert_id or str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO alerts "
                "(alert_id, store_id, camera_id, run_id, pipeline_type, event_type, "
                " severity, status, opened_at, closed_at, identity_person_id, "
                " identity_label, identity_score, reason_codes_json, metadata_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s)",
                (alert_id, store_id, camera_id, run_id, pipeline_type, event_type, severity,
                 status, opened_at, identity_person_id, identity_label, identity_score,
                 json.dumps(reason_codes, ensure_ascii=True),
                 json.dumps(metadata or {}, ensure_ascii=True, sort_keys=True)),
            )
        self.conn.commit()
        return alert_id

    def add_alert_media(
        self, *, alert_id: str, kind: str, path: str, mime_type: str | None = None,
        started_at: str | None = None, ended_at: str | None = None,
        metadata: dict[str, Any] | None = None, media_id: str | None = None,
    ) -> str:
        media_id = media_id or str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO alert_media "
                "(media_id, alert_id, kind, path, mime_type, started_at, ended_at, metadata_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (media_id, alert_id, kind, path, mime_type, started_at, ended_at,
                 json.dumps(metadata or {}, ensure_ascii=True, sort_keys=True)),
            )
        self.conn.commit()
        return media_id
