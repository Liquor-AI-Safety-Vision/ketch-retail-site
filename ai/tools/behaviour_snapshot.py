"""Run a store's real pipelines over its footage once and dump every alert.

A refactor of the pipelines or the tiers must not change what the AI decides.
This runs the split pipeline exactly as production does (same tiers, same
models, same manifest), except that each camera reads its video file once,
unpaced, so every frame is processed and the result is repeatable. The alerts
it raised are written as sorted JSON; two snapshots are compared with --compare.

    python ai/tools/behaviour_snapshot.py --manifest ai/config/stores/store_test_01.json \\
        --out outputs/behaviour/before
    python ai/tools/behaviour_snapshot.py --compare outputs/behaviour/before outputs/behaviour/after

Fields that differ between two runs of the same code (ids, wall-clock times,
file paths) are left out of the snapshot.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.src.app import run_split_pipeline as split  # noqa: E402  (caps native threads first)

#: metadata keys that change between identical runs
VOLATILE_METADATA = {"run_id", "clip_path", "snapshot_path", "opened_at", "wall_time"}


def _single_pass_manifest(manifest_path: Path, out_dir: Path, duration_s: float) -> Path:
    """The manifest with every camera reading its main video once, unpaced,
    stopping at duration_s or the end of the file, whichever comes first."""
    data = json.loads(manifest_path.read_text())
    cameras = []
    for camera in data["cameras"]:
        # Every other video in the camera's playlist runs as a copy of the
        # camera, so footage that only plays later in the loop is covered too.
        stream = camera["stream"]
        uris = [stream["uri"]] + [e["uri"] for e in stream.pop("playlist", None) or []
                                  if e["uri"] != stream["uri"]]
        for k, uri in enumerate(dict.fromkeys(uris)):
            copy = json.loads(json.dumps(camera))
            if k:
                copy["camera_id"] = f"{camera['camera_id']}__{Path(uri).stem}"
            copy["stream"].pop("recording", None)
            copy["stream"].update({
                "uri": uri, "input_mode": "video_file",
                "simulation": {"strategy": "trim", "target_duration_s": duration_s}})
            cameras.append(copy)
    data["cameras"] = cameras
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(data, indent=1))
    return path


def _dump_alerts(db_path: Path) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT camera_id, pipeline_type, event_type, severity, identity_person_id, "
        "identity_label, identity_score, reason_codes_json, metadata_json FROM alerts").fetchall()
    media = con.execute(
        "SELECT a.camera_id, m.kind, count(*) FROM alert_media m JOIN alerts a USING (alert_id) "
        "GROUP BY a.camera_id, m.kind").fetchall()
    con.close()
    alerts = []
    for row in rows:
        item = dict(row)
        item["reason_codes"] = json.loads(item.pop("reason_codes_json") or "[]")
        metadata = json.loads(item.pop("metadata_json") or "{}")
        item["metadata"] = {k: v for k, v in sorted(metadata.items()) if k not in VOLATILE_METADATA}
        if item["identity_score"] is not None:
            item["identity_score"] = round(float(item["identity_score"]), 4)
        alerts.append(item)
    alerts.sort(key=lambda a: json.dumps(a, sort_keys=True))
    return [{"media": sorted([list(m) for m in media])}, *alerts]


def snapshot(manifest: Path, out_dir: Path, args) -> Path:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    from ai.src.infra.local_config import camera_specs_from_manifests

    specs = camera_specs_from_manifests(
        [_single_pass_manifest(manifest, out_dir, args.duration_s)])
    db_path = out_dir / "alerts.sqlite"
    biz_stats, _all, _load_s = split.run(
        specs, decoders=min(args.decoders, len(specs)), inferers=args.inferers,
        infer_threads=args.infer_threads, businesses=min(args.business, len(specs)),
        pose_model=split.DEFAULT_POSE_MODEL, reid_model=split.DEFAULT_REID_MODEL,
        till_model=split.DEFAULT_TILL_MODEL, till_imgsz=640,
        face_model=split.DEFAULT_FACE_MODEL, arcface_model=split.DEFAULT_ARCFACE_MODEL,
        db_path=str(db_path), media_root=str(out_dir / "media"),
        gallery_db_path=args.gallery_db or str(db_path), slots_per_camera=8,
        storage_mode="local-db")
    frames = {s.camera_id: s.frames_processed for s in biz_stats}
    alerts = _dump_alerts(db_path)
    result = {"frames": dict(sorted(frames.items())), "alerts": alerts}
    path = out_dir / "snapshot.json"
    path.write_text(json.dumps(result, indent=1, sort_keys=True, default=str))
    per_camera: dict[str, int] = {}
    for a in alerts[1:]:
        per_camera[a["camera_id"]] = per_camera.get(a["camera_id"], 0) + 1
    print(f"frames per camera: {result['frames']}")
    print(f"alerts per camera: {dict(sorted(per_camera.items()))}  -> {path}")
    return path


def compare(a: Path, b: Path) -> int:
    left = json.loads((a / "snapshot.json").read_text())
    right = json.loads((b / "snapshot.json").read_text())
    if left == right:
        print(f"identical: {len(left['alerts']) - 1} alerts, frames {left['frames']}")
        return 0
    if left["frames"] != right["frames"]:
        print(f"frames differ:\n  {a}: {left['frames']}\n  {b}: {right['frames']}")
    la = {json.dumps(x, sort_keys=True) for x in left["alerts"]}
    ra = {json.dumps(x, sort_keys=True) for x in right["alerts"]}
    for x in sorted(la - ra):
        print(f"- only in {a.name}: {x}")
    for x in sorted(ra - la):
        print(f"+ only in {b.name}: {x}")
    return 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--manifest", type=Path, default=ROOT / "ai/config/stores/store_test_01.json")
    p.add_argument("--out", type=Path)
    p.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    p.add_argument("--gallery-db", default=None,
                   help="face gallery to identify against: a SQLite path, or 'neon' for the "
                        "DATABASE_URL in ai/.env (read only). Default: the run's own empty db")
    p.add_argument("--duration-s", type=float, default=300.0,
                   help="seconds of footage per camera (less if the file is shorter)")
    p.add_argument("--decoders", type=int, default=4)
    p.add_argument("--inferers", type=int, default=1)
    p.add_argument("--infer-threads", type=int, default=3)
    p.add_argument("--business", type=int, default=2)
    args = p.parse_args()
    if args.compare:
        return compare(*args.compare)
    if args.out is None:
        p.error("--out is required unless --compare is given")
    if args.gallery_db == "neon":
        import os

        from ai.src.common.env_file import load_ai_env

        load_ai_env()
        args.gallery_db = os.environ["DATABASE_URL"]
    snapshot(args.manifest, args.out, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
