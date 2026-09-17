"""Put one store, its staff faces and its owner into NeonDB.

Everything a cloud-db run and the dashboard API need to exist beforehand:

  stores / cameras        from the manifest, via the same seed_store_topology_pg
                          the pipeline runs at startup (idempotent)
  persons / face_embeddings
                          staff faces from backend/data/_gallery_export.json --
                          the two real faces build_staff_db extracted once:
                          staff_a (storage_1.mp4, the cold storage camera) and
                          staff_b (storage_2.mp4, the storage camera) --
                          re-registered under this store's store_id/company_id,
                          which FaceGallery.match() filters on
  owners / owner_stores   the dashboard login, linked to the store
  watchlist               optional: one non-staff person ("Regular shoplifter")
                          whose embedding is ArcFace on a 112x112 image. Use a
                          crop that is NOT a real person's face: flagging a real,
                          innocent face as a shoplifter is the harm to avoid;
                          for the entrance pipeline to match against. The chip
                          is uploaded to R2 and its key kept in image_path.

Re-runnable: rows are upserted, and each staff member's embeddings are
replaced rather than appended, so a second run does not double the candidates
match() compares against. The owner's password is only set when the owner is
first created or --reset-password is passed; it is generated if not given and
printed once.

    python backend/tools/seed_cloud_store.py \\
        --manifest backend/config/stores/store_test_01.json \\
        --owner-email owner.test01@liquir.local --owner-name "Owner Test 01"
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.src.common.logging_setup import configure_logging  # noqa: E402
from backend.src.common.paths import backend_root  # noqa: E402

# source person in the export -> suffix for this store's person_id
STAFF = {"staff_a": "staff_a", "staff_b": "staff_b"}


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--manifest", required=True)
    p.add_argument("--export", default=str(backend_root() / "data/_gallery_export.json"))
    p.add_argument("--owner-email", required=True)
    p.add_argument("--owner-name", required=True)
    p.add_argument("--owner-password", default=None,
                   help="omit to generate one; it is printed once")
    p.add_argument("--reset-password", action="store_true")
    p.add_argument("--watchlist-chip", default=None,
                   help="112x112 face chip to register as a non-staff watchlist person")
    p.add_argument("--watchlist-name", default="Regular shoplifter")
    args = p.parse_args()

    _load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set (environment or .env)")

    import bcrypt
    import numpy as np
    import psycopg2

    from backend.src.infra.local_config import camera_specs_from_manifest
    from backend.src.infra.postgres_alert_store import seed_store_topology_pg
    from backend.src.infra.postgres_face_gallery import PostgresFaceGallery

    specs = camera_specs_from_manifest(args.manifest)
    store_id, company_id = specs[0].store_id, specs[0].company_id
    seed_store_topology_pg(dsn, specs)
    print(f"store  {store_id} ({specs[0].store_display_name}), {len(specs)} cameras")

    export = json.loads(Path(args.export).read_text())
    latest: dict[str, dict] = {}
    for e in export["face_embeddings"]:
        if e["person_id"] not in latest or e["created_at"] > latest[e["person_id"]]["created_at"]:
            latest[e["person_id"]] = e
    gallery = PostgresFaceGallery(dsn)
    for source_id, suffix in STAFF.items():
        source = next(pp for pp in export["persons"] if pp["person_id"] == source_id)
        person_id = f"{store_id}_{suffix}"
        gallery.upsert_person(
            person_id, display_name=source["display_name"], is_staff=True,
            company_id=company_id, store_id=store_id,
            metadata={"seeded_from": source_id, "source_note": json.loads(
                source.get("metadata_json") or "{}").get("note")})
        emb = latest[source_id]
        gallery.replace_embeddings(
            person_id, np.frombuffer(bytes.fromhex(emb["embedding"]), np.float32),
            detector_score=emb.get("detector_score"),
            source=f"seed_cloud_store.py (from {source_id})")
        print(f"staff  {person_id:28} {source['display_name']}")
    summary = gallery.summarize(store_id=store_id, company_id=company_id)
    print(f"       gallery for {store_id}/{company_id}: {summary['persons']} person(s), "
          f"{summary['embeddings']} embedding(s)")

    if args.watchlist_chip:
        _seed_watchlist(args, dsn, store_id, company_id, gallery)

    now = datetime.now(timezone.utc).isoformat()
    generated = None
    conn = psycopg2.connect(dsn, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT owner_id FROM owners WHERE email = %s", (args.owner_email,))
            row = cur.fetchone()
            password = args.owner_password
            if row is None or args.reset_password:
                if password is None:
                    password = generated = secrets.token_urlsafe(12)
                password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            if row is None:
                owner_id = f"owner_{uuid.uuid4().hex[:12]}"
                cur.execute(
                    "INSERT INTO owners (owner_id, display_name, email, password_hash, "
                    " metadata_json, created_at, updated_at) VALUES (%s, %s, %s, %s, '{}', %s, %s)",
                    (owner_id, args.owner_name, args.owner_email, password_hash, now, now))
                action = "created"
            else:
                owner_id = row[0]
                cur.execute("UPDATE owners SET display_name = %s, updated_at = %s WHERE owner_id = %s",
                            (args.owner_name, now, owner_id))
                if args.reset_password:
                    cur.execute("UPDATE owners SET password_hash = %s WHERE owner_id = %s",
                                (password_hash, owner_id))
                action = "updated"
            cur.execute(
                "INSERT INTO owner_stores (owner_id, store_id, created_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (owner_id, store_id) DO NOTHING", (owner_id, store_id, now))
        conn.commit()
    finally:
        conn.close()
    print(f"owner  {owner_id} {args.owner_name} <{args.owner_email}> {action}, linked to {store_id}")
    if generated:
        print(f"       generated password (shown once): {generated}")


def _seed_watchlist(args, dsn, store_id, company_id, gallery) -> None:
    import base64
    import hashlib

    import boto3
    import cv2

    from backend.src.common.paths import model_path
    from backend.src.perception.face_identity import ArcFaceEmbedder

    chip = cv2.imread(args.watchlist_chip)
    if chip is None or chip.shape[:2] != (112, 112):
        raise SystemExit(f"{args.watchlist_chip}: expected a readable 112x112 image")
    # Same preprocessing the face tier's ArcFace service applies to its chips.
    embedding = ArcFaceEmbedder(model_path("ms1mv3_arcface_r18.onnx"),
                                providers=["CPUExecutionProvider"], prefer_coreml=False)(chip)

    person_id = f"{store_id}_watchlist_01"
    body = Path(args.watchlist_chip).read_bytes()
    key = f"watchlist/{person_id}/face.jpg"
    s3 = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"],
                      aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                      aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                      region_name="auto")
    s3.put_object(Bucket=os.environ["R2_BUCKET"], Key=key, Body=body,
                  ContentMD5=base64.b64encode(hashlib.md5(body).digest()).decode())

    gallery.upsert_person(
        person_id, display_name=args.watchlist_name, is_staff=False,
        company_id=company_id, store_id=store_id,
        metadata={"role": "watchlist", "chip_source": Path(args.watchlist_chip).name,
                  "synthetic": True})
    gallery.replace_embeddings(person_id, embedding, source="seed_cloud_store.py (watchlist chip)",
                               image_path=key)
    watch = gallery.summarize(store_id=store_id, company_id=company_id, is_staff=False)
    print(f"watch  {person_id:28} {args.watchlist_name}  (chip -> r2://{key}); "
          f"watchlist now {watch['persons']} person(s)")


if __name__ == "__main__":
    main()
