from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.src.infra.local_config import camera_specs_from_manifest, load_store_manifest


PIPELINE_DESCRIPTIONS = {
    "counter_cash_theft_v1": "Counter cash theft detection when no customer is at checkout.",
    "cold_storage_intrusion_v1": "Cold storage intrusion detection with identity resolution.",
    "storage_intrusion_v1": "Storage intrusion detection with identity resolution.",
    "whole_store_tracking_v1": "Whole-store customer tracking baseline.",
}


def ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def apply_schema(conn: sqlite3.Connection, schema_path: Path):
    conn.executescript(schema_path.read_text())
    conn.commit()


def seed_manifest(conn: sqlite3.Connection, manifest_path: Path):
    data = load_store_manifest(manifest_path)
    specs = camera_specs_from_manifest(manifest_path)

    conn.execute(
        """
        INSERT INTO stores (store_id, store_code, display_name, timezone, metadata_json)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(store_id) DO UPDATE SET
            store_code=excluded.store_code,
            display_name=excluded.display_name,
            timezone=excluded.timezone,
            metadata_json=excluded.metadata_json
        """,
        (
            data["store_id"],
            data["store_id"],
            data["display_name"],
            data["timezone"],
            json.dumps(
                {
                    "mode": data.get("mode"),
                    "runtime": data.get("runtime", {}),
                    "policies": data.get("policies", {}),
                }
            ),
        ),
    )

    seen_templates = set()
    for spec in specs:
        conn.execute(
            """
            INSERT INTO cameras
            (camera_id, store_id, camera_code, display_name, camera_type, source_type, source_uri, is_active, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(camera_id) DO UPDATE SET
                store_id=excluded.store_id,
                camera_code=excluded.camera_code,
                display_name=excluded.display_name,
                camera_type=excluded.camera_type,
                source_type=excluded.source_type,
                source_uri=excluded.source_uri,
                is_active=excluded.is_active,
                metadata_json=excluded.metadata_json
            """,
            (
                spec.camera_id,
                spec.store_id,
                spec.camera_id,
                spec.display_name,
                spec.camera_type,
                spec.source_kind,
                spec.source_uri,
                json.dumps(spec.raw_config),
            ),
        )

        for binding in spec.pipeline_bindings:
            pipeline_id = binding.pipeline_id
            if pipeline_id not in seen_templates:
                conn.execute(
                    """
                    INSERT INTO pipeline_templates
                    (pipeline_template_id, pipeline_type, version, description)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(pipeline_template_id) DO UPDATE SET
                        pipeline_type=excluded.pipeline_type,
                        version=excluded.version,
                        description=excluded.description
                    """,
                    (
                        pipeline_id,
                        pipeline_id,
                        "v1",
                        PIPELINE_DESCRIPTIONS.get(pipeline_id, pipeline_id),
                    ),
                )
                seen_templates.add(pipeline_id)

            binding_id = f"{spec.camera_id}:{pipeline_id}"
            binding_cfg = {
                "target_fps": spec.target_fps,
                "target_duration_s": spec.target_duration_s,
                "source_strategy": spec.strategy,
                "source_start_s": spec.start_s,
                "runtime": spec.runtime_profile,
                "perception": spec.perception_profile,
                "simulation": spec.simulation_profile,
                "pipeline_params": binding.params,
            }
            conn.execute(
                """
                INSERT INTO camera_pipeline_bindings
                (binding_id, camera_id, pipeline_template_id, is_active, config_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(binding_id) DO UPDATE SET
                    camera_id=excluded.camera_id,
                    pipeline_template_id=excluded.pipeline_template_id,
                    is_active=excluded.is_active,
                    config_json=excluded.config_json
                """,
                (
                    binding_id,
                    spec.camera_id,
                    pipeline_id,
                    1 if binding.enabled else 0,
                    json.dumps(binding_cfg),
                ),
            )

    conn.commit()


def main():
    p = argparse.ArgumentParser(description=__doc__ or "Initialize local dev SQLite DB")
    p.add_argument(
        "--db-path",
        default=str(ROOT / "backend/data/local_dev.sqlite"),
    )
    p.add_argument(
        "--schema",
        default=str(ROOT / "backend/sql/local_dev_schema.sql"),
    )
    p.add_argument(
        "--manifest",
        default=str(ROOT / "backend/config/stores/local_dev_store.json"),
    )
    a = p.parse_args()

    db_path = Path(a.db_path)
    ensure_parent(db_path)
    conn = sqlite3.connect(db_path)
    apply_schema(conn, Path(a.schema))
    seed_manifest(conn, Path(a.manifest))
    conn.close()
    print(f"initialized sqlite db: {db_path}")


if __name__ == "__main__":
    main()
