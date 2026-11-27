"""Put one store and its staff/watchlist faces into NeonDB.

Everything a cloud-db run needs to exist beforehand:

  stores / cameras        from the manifest, via the same seed_store_topology_pg
                          the pipeline runs at startup (idempotent)
  persons / face_embeddings
                          staff faces from ai/data/_gallery_export.json --
                          the two real faces build_staff_db extracted once:
                          staff_a (storage_1.mp4, the cold storage camera) and
                          staff_b (storage_2.mp4, the storage camera) --
                          re-registered under this store's store_id/company_id,
                          which FaceGallery.match() filters on
  watchlist               optional: one non-staff person ("Regular shoplifter")
                          whose embedding is ArcFace on a 112x112 image. Use a
                          crop that is NOT a real person's face: flagging a real,
                          innocent face as a shoplifter is the harm to avoid;
                          for the entrance pipeline to match against. The chip
                          is uploaded to R2 and its key kept in image_path.

Re-runnable: rows are upserted, and each staff member's embeddings are
replaced rather than appended, so a second run does not double the candidates
match() compares against.

Owner accounts are not created here. Logins belong to the Go API in backend/
(Argon2id hashes, roles, sessions); a bcrypt hash written from Python would not
verify there. Create the owner through backend/ and link it to this store_id.

    python ai/tools/seed_cloud_store.py \\
        --manifest ai/config/stores/store_test_01.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.src.common.env_file import load_ai_env  # noqa: E402
from ai.src.common.logging_setup import configure_logging  # noqa: E402
from ai.src.common.paths import ai_root  # noqa: E402

# source person in the export -> suffix for this store's person_id
STAFF = {"staff_a": "staff_a", "staff_b": "staff_b"}


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--manifest", required=True)
    p.add_argument("--export", default=str(ai_root() / "data/_gallery_export.json"))
    p.add_argument("--watchlist-chip", default=None,
                   help="112x112 face chip to register as a non-staff watchlist person")
    p.add_argument("--watchlist-name", default="Regular shoplifter")
    args = p.parse_args()

    load_ai_env()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set (environment or ai/.env)")

    import numpy as np

    from ai.src.infra.local_config import camera_specs_from_manifest
    from ai.src.infra.postgres_alert_store import seed_store_topology_pg
    from ai.src.infra.postgres_face_gallery import PostgresFaceGallery

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


def _seed_watchlist(args, dsn, store_id, company_id, gallery) -> None:
    import base64
    import hashlib

    import boto3
    import cv2

    from ai.src.common.paths import model_path
    from ai.src.perception.face_identity import ArcFaceEmbedder

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
