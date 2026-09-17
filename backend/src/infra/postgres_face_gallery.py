"""Staff face gallery in Postgres (NeonDB), for --storage-mode cloud-db.

Same read surface as perception.face_identity.FaceGallery -- iter_embeddings,
summarize, match -- over the persons/face_embeddings tables postgres_schema.sql
already defines, so a cloud deployment keeps its staff next to its stores and
alerts instead of in a SQLite file that exists only on one GPU box (and is
gone when that box is recycled).

Read from memory, refreshed on a timer, not queried per identification. match()
walks every embedding in scope, and a face request is made every frame for
some pipelines; one round trip per request to a database on another continent
would put the network in the face tier's hot path. A roster is tiny and changes
rarely, so a refresh every REFRESH_S is plenty. If a refresh fails the previous
roster is kept and the failure logged -- losing Neon for a minute should not
turn every staff member into an unknown intruder.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

import numpy as np

from backend.src.common.stream_uri import redact_stream_credentials
from backend.src.perception.face_identity import FaceGallery

logger = logging.getLogger(__name__)

REFRESH_S = 60.0


def is_postgres_ref(ref: str | None) -> bool:
    return bool(ref) and str(ref).startswith(("postgres://", "postgresql://"))


class PostgresFaceGallery(FaceGallery):
    # Deliberately does not call FaceGallery.__init__: that opens SQLite.
    # match() is inherited unchanged; it only reads through iter_embeddings.
    def __init__(self, dsn: str, refresh_s: float = REFRESH_S):  # noqa: D107
        self.dsn = dsn
        self.db_path = redact_stream_credentials(dsn)
        self.refresh_s = refresh_s
        self._rows: list[dict] | None = None
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def _connect(self):
        import psycopg2

        return psycopg2.connect(self.dsn, connect_timeout=10)

    def close(self):
        pass  # connections are opened per refresh and closed straight away

    def _load(self) -> list[dict]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT e.embedding_id, e.person_id, e.embedding, e.image_path, "
                    "       p.display_name, p.is_staff, p.company_id, p.store_id "
                    "FROM face_embeddings e JOIN persons p USING (person_id)")
                return [{
                    "embedding_id": int(r[0]), "person_id": r[1],
                    "embedding": np.frombuffer(bytes(r[2]), np.float32),
                    "image_path": r[3], "display_name": r[4], "is_staff": bool(r[5]),
                    "company_id": r[6], "store_id": r[7],
                } for r in cur.fetchall()]
        finally:
            conn.close()

    def _roster(self) -> list[dict]:
        with self._lock:
            stale = time.monotonic() - self._loaded_at >= self.refresh_s
            if self._rows is None or stale:
                try:
                    self._rows = self._load()
                    self._loaded_at = time.monotonic()
                except Exception as exc:
                    if self._rows is None:
                        raise
                    logger.warning("face gallery refresh from %s failed, keeping the "
                                   "previous %d embeddings: %r", self.db_path, len(self._rows), exc)
                    self._loaded_at = time.monotonic()
            return self._rows

    def iter_embeddings(self, store_id=None, company_id=None, is_staff=None):
        for row in self._roster():
            if store_id is not None and row["store_id"] != store_id:
                continue
            if company_id is not None and row["company_id"] != company_id:
                continue
            if is_staff is not None and row["is_staff"] != bool(is_staff):
                continue
            yield row

    def summarize(self, store_id=None, company_id=None, is_staff=None):
        rows = list(self.iter_embeddings(store_id=store_id, company_id=company_id,
                                         is_staff=is_staff))
        return {
            "persons": len({r["person_id"] for r in rows}),
            "embeddings": len(rows),
            "known_store_ids": sorted({r["store_id"] for r in self._roster() if r["store_id"]}),
        }

    # Writes, for seeding. Idempotent so a seed can be re-run.
    def upsert_person(self, person_id, display_name, is_staff=True,
                      company_id=None, store_id=None, metadata=None):
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO persons (person_id, display_name, is_staff, company_id, "
                    " store_id, metadata_json, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (person_id) DO UPDATE SET display_name = excluded.display_name, "
                    " is_staff = excluded.is_staff, company_id = excluded.company_id, "
                    " store_id = excluded.store_id, metadata_json = excluded.metadata_json, "
                    " updated_at = excluded.updated_at",
                    (person_id, display_name, int(is_staff), company_id, store_id,
                     json.dumps(metadata or {}, ensure_ascii=True, sort_keys=True), now, now))
            conn.commit()
        finally:
            conn.close()
        self._rows = None

    def replace_embeddings(self, person_id, embedding, detector_score=None, source=None,
                           image_path=None):
        """Make `embedding` this person's only one, so re-seeding does not stack
        duplicates that would each count as a separate match candidate."""
        emb = np.asarray(embedding, np.float32).reshape(-1)
        emb = emb / (np.linalg.norm(emb) + 1e-9)
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM face_embeddings WHERE person_id = %s", (person_id,))
                cur.execute(
                    "INSERT INTO face_embeddings (person_id, embedding, detector_score, source, "
                    " image_path, metadata_json, created_at) VALUES (%s, %s, %s, %s, %s, '{}', %s)",
                    (person_id, emb.tobytes(), detector_score, source, image_path, now))
            conn.commit()
        finally:
            conn.close()
        self._rows = None


def open_face_gallery(ref: str):
    """A SQLite path or a postgres:// DSN, whichever the run was given."""
    if is_postgres_ref(ref):
        return PostgresFaceGallery(ref)
    return FaceGallery(ref)
