"""Read-only, owner-scoped queries for the dashboard API.

postgres_alert_store.py (backend/src/infra) only ever writes -- start_run,
finish_run, create_alert, add_alert_media -- because the pipeline is its only
caller. This is the read side the pipeline never needed: list_stores /
list_cameras / list_alerts, every one of them joined through owner_stores so
an owner only ever sees rows for stores they're linked to, plus turning the
R2 object key alert_media.path already holds into a URL the browser can
actually load (see cloud_alert_sink.py's docstring -- the key was always
meant to be turned into a URL by something downstream of it, and until now
nothing was).

A short in-memory cache sits in front of the alerts query specifically: the
dashboard polls this endpoint every few seconds per open browser tab, and
without a cache every one of those polls would hit NeonDB directly, keeping
its compute endpoint awake continuously (Neon bills by awake time, not query
count) regardless of how many tabs are actually open.
"""
from __future__ import annotations

import time
from typing import Any


class _TTLCache:
    def __init__(self, ttl_s: float):
        self.ttl_s = ttl_s
        self._store: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any):
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: Any, value: Any) -> None:
        self._store[key] = (time.monotonic() + self.ttl_s, value)


class AlertReader:
    def __init__(self, dsn: str, s3, bucket: str,
                 alerts_cache_ttl_s: float = 8.0, presigned_url_ttl_s: int = 3600):
        import psycopg2

        self.conn = psycopg2.connect(dsn)
        self.s3 = s3
        self.bucket = bucket
        self.presigned_url_ttl_s = presigned_url_ttl_s
        self._alerts_cache = _TTLCache(alerts_cache_ttl_s)

    def close(self):
        self.conn.close()

    def _owned_store_ids(self, owner_id: str) -> list[str]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT store_id FROM owner_stores WHERE owner_id = %s", (owner_id,))
            return [row[0] for row in cur.fetchall()]

    def get_owner_by_email(self, email: str) -> dict | None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT owner_id, password_hash FROM owners WHERE email = %s", (email,))
            row = cur.fetchone()
        if row is None:
            return None
        return {"owner_id": row[0], "password_hash": row[1]}

    def list_stores(self, owner_id: str) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT s.store_id, s.store_code, s.display_name, s.timezone "
                "FROM stores s "
                "JOIN owner_stores os ON os.store_id = s.store_id "
                "WHERE os.owner_id = %s "
                "ORDER BY s.display_name",
                (owner_id,),
            )
            rows = cur.fetchall()
        return [
            {"store_id": r[0], "store_code": r[1], "display_name": r[2], "timezone": r[3]}
            for r in rows
        ]

    def list_cameras(self, owner_id: str, store_id: str | None = None) -> list[dict]:
        store_ids = self._owned_store_ids(owner_id)
        if store_id is not None:
            store_ids = [s for s in store_ids if s == store_id]
        if not store_ids:
            return []
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT camera_id, store_id, camera_code, display_name, camera_type, "
                "is_active FROM cameras WHERE store_id = ANY(%s) ORDER BY display_name",
                (store_ids,),
            )
            rows = cur.fetchall()
        return [
            {
                "camera_id": r[0], "store_id": r[1], "camera_code": r[2],
                "display_name": r[3], "camera_type": r[4], "is_active": bool(r[5]),
            }
            for r in rows
        ]

    def _media_url(self, object_key: str) -> str:
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=self.presigned_url_ttl_s,
        )

    def list_alerts(
        self, owner_id: str, store_id: str | None = None, status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        cache_key = (owner_id, store_id, status, limit)
        cached = self._alerts_cache.get(cache_key)
        if cached is not None:
            return cached

        store_ids = self._owned_store_ids(owner_id)
        if store_id is not None:
            store_ids = [s for s in store_ids if s == store_id]
        if not store_ids:
            self._alerts_cache.set(cache_key, [])
            return []

        query = (
            "SELECT alert_id, store_id, camera_id, event_type, severity, status, "
            "opened_at, closed_at, identity_label, reason_codes_json "
            "FROM alerts WHERE store_id = ANY(%s)"
        )
        params: list[Any] = [store_ids]
        if status is not None:
            query += " AND status = %s"
            params.append(status)
        query += " ORDER BY opened_at DESC LIMIT %s"
        params.append(limit)

        with self.conn.cursor() as cur:
            cur.execute(query, params)
            alert_rows = cur.fetchall()

        if not alert_rows:
            self._alerts_cache.set(cache_key, [])
            return []

        alert_ids = [r[0] for r in alert_rows]
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT media_id, alert_id, kind, path, mime_type, started_at, ended_at "
                "FROM alert_media WHERE alert_id = ANY(%s)",
                (alert_ids,),
            )
            media_rows = cur.fetchall()

        media_by_alert: dict[str, list[dict]] = {}
        for m in media_rows:
            media_by_alert.setdefault(m[1], []).append({
                "media_id": m[0], "kind": m[2],
                "url": self._media_url(m[3]) if m[3] else None,
                "mime_type": m[4], "started_at": m[5], "ended_at": m[6],
            })

        import json

        result = []
        for r in alert_rows:
            result.append({
                "alert_id": r[0], "store_id": r[1], "camera_id": r[2],
                "event_type": r[3], "severity": r[4], "status": r[5],
                "opened_at": r[6], "closed_at": r[7], "identity_label": r[8],
                "reason_codes": json.loads(r[9]) if r[9] else [],
                "media": media_by_alert.get(r[0], []),
            })
        self._alerts_cache.set(cache_key, result)
        return result
