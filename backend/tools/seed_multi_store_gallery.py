"""Seed six independent per-store staff identities for the 4-store test.

Real footage is limited to two distinct faces (the same crops build_staff_db
extracted from storage_1.mp4 and storage_2.mp4 once, exported to
backend/data/_gallery_export.json). Reusing those embedding bytes across
several stores does not mean the same physical person works at all of them
-- each store's roster is independent, and this script gives each one its
own person_id even where the underlying face vector is identical. That is
what makes the test meaningful: two people can share a face vector by
construction and still be scoped correctly if store_id filtering works.

    store_1: store1_staff_a, store1_staff_b   (both)
    store_2: store2_staff_a, store2_staff_b   (both)
    store_3: store3_staff_a                    (A only)
    store_4: store4_staff_b                    (B only)

Camera-to-video mapping means every cold_storage_N camera plays the same
footage regardless of N (the "A" face) and every storage_N camera plays the
same footage regardless of N (the "B" face) -- so store_3's storage_3 camera
sees a real, registered-elsewhere face that store_3 never enrolled, and
store_4's cold_storage_4 camera sees the mirror case. Both must come back
unmatched if the store scoping in FaceGallery.match() is doing its job.

    python backend/tools/seed_multi_store_gallery.py --db backend/data/multi_store.sqlite
"""
from __future__ import annotations

import argparse
import json
import sys
from backend.src.common.paths import backend_root
from pathlib import Path
from backend.src.common.logging_setup import configure_logging

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# store_id -> [(person_id, source_person_id_in_export), ...]
ROSTER = {
    "store_1": [("store1_staff_a", "staff_a"), ("store1_staff_b", "staff_b")],
    "store_2": [("store2_staff_a", "staff_a"), ("store2_staff_b", "staff_b")],
    "store_3": [("store3_staff_a", "staff_a")],
    "store_4": [("store4_staff_b", "staff_b")],
}


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", required=True, help="the merged db_path/gallery_db_path file")
    p.add_argument("--export", default=str(backend_root() / "data/_gallery_export.json"))
    args = p.parse_args()

    export = json.loads(Path(args.export).read_text())
    # Latest embedding per source person, not every re-registration on file.
    latest_embedding: dict[str, dict] = {}
    for e in export["face_embeddings"]:
        prior = latest_embedding.get(e["person_id"])
        if prior is None or e["created_at"] > prior["created_at"]:
            latest_embedding[e["person_id"]] = e

    import numpy as np
    from backend.src.perception.face_identity import FaceGallery

    gallery = FaceGallery(args.db)
    try:
        for store_id, members in ROSTER.items():
            for person_id, source_id in members:
                source_person = next(pp for pp in export["persons"] if pp["person_id"] == source_id)
                gallery.upsert_person(
                    person_id, display_name=f"{source_person['display_name']} ({store_id})",
                    is_staff=True, company_id=source_person.get("company_id"),
                    store_id=store_id)
                emb = latest_embedding[source_id]
                # frombuffer, not asarray -- the export stored these bytes as
                # a hex-encoded float32 vector, not a sequence of small
                # integers to cast. asarray(bytes, float32) would silently
                # reinterpret each individual byte as its own float instead
                # of reading the vector back.
                vector = np.frombuffer(bytes.fromhex(emb["embedding"]), dtype=np.float32)
                gallery.add_embedding(
                    person_id, vector, detector_score=emb.get("detector_score"),
                    source=f"seed_multi_store_gallery.py (from {source_id})")
                print(f"  {store_id:10} {person_id:16} <- {source_id}")

        print()
        for store_id in ROSTER:
            s = gallery.summarize(store_id=store_id)
            print(f"  {store_id}: {s['persons']} person(s), {s['embeddings']} embedding(s)")
    finally:
        gallery.close()
    print("done")


if __name__ == "__main__":
    main()
