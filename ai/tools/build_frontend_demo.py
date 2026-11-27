"""Build the public demo dashboard's static data from this project's footage.

The landing page's "View demo" opens a read-only dashboard with no backend:
frontend/src/security-dashboard/lib/demoApi.ts answers every call from
frontend/src/security-dashboard/lib/demoData.ts and the files under
frontend/public/demo-dashboard/. This script writes both.

Where each piece comes from -- nothing is drawn or composited:

  cameras, pipelines, zones   store_test_01's manifest, through the same
                              camera_metadata() the AI seeder writes into
                              cameras.metadata_json
  camera loops                15 s of each camera's own playlist video
  unknown_entry,              clips and snapshots the AI service itself cut
  staff_outside_schedule      and uploaded to R2 (read-only; needs ai/.env)
  suspected_cash_concealment  the counter clip in the test playlist; the
                              counter FSM raised the alert at t=4.0 s on it
                              (frontend/public/demo-source/.../events.jsonl)
  watchlist_match             the entrance visit the entrance pipeline
                              confirmed at t=57.0 s (dev/outputs/entrance_smoke);
                              the watchlist identity is the demo's own entry
                              ("Regular shoplifter", as seed_cloud_store.py
                              registers it), not a real person's record

No weapon_detected alert: there is no weapon footage in the project and the
weapon module does not exist yet.

Usage: python ai/tools/build_frontend_demo.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.src.common.env_file import load_ai_env  # noqa: E402
from ai.src.infra.camera_metadata import camera_metadata  # noqa: E402
from ai.src.infra.local_config import camera_specs_from_manifest  # noqa: E402

MANIFEST = ROOT / "ai/config/stores/store_test_01.json"
VIDEOS = ROOT / "data/videos"
PUBLIC = ROOT / "frontend/public/demo-dashboard"
DATA_TS = ROOT / "frontend/src/security-dashboard/lib/demoData.ts"
WEB = "/demo-dashboard"

# 15 s of each camera's playlist video, chosen where people are in frame.
CAMERA_LOOPS = {
    "st01_counter": ("cctv_counter/cash_till_open_2.mp4", 20),
    "st01_storage": ("cctv_storage/storage_2.mp4", 30),
    "st01_cold_storage": ("cctv_storage/storage_1.mp4", 60),
    "st01_entrance": ("entrance/entrance.mp4", 50),
    "st01_whole_store": ("whole_store/whole_store_long.mp4", 100),
    "st01_whole_store_corner": ("whole_store/whole_store_corner.mp4", 100),
    "st01_cold_drinks": ("whole_store/cold_drinks.mp4", 100),
}
# The AI's clips are pre_s=15 + post_s=15 around the event; keep the middle.
R2_TRIM = (8.0, 14.0)  # start, duration

SCALE = "scale='min(960,iw)':'min(720,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def encode_clip(src: Path, dst: Path, start: float, duration: float) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg("-ss", str(start), "-t", str(duration), "-i", str(src), "-an", "-vf", SCALE,
           "-c:v", "libx264", "-preset", "slow", "-crf", "30", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(dst))


def poster(src: Path, dst: Path, at: float | None) -> None:
    """A frame of a video at `at` seconds, or a still image (at=None)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    seek = [] if at is None else ["-ss", str(at)]  # -ss on a still silently writes nothing
    ffmpeg(*seek, "-i", str(src), "-frames:v", "1", "-vf", SCALE, "-q:v", "5", str(dst))
    if not dst.is_file() or dst.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg wrote no frame from {src} at {at}")


def size(path: Path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(path)],
                         check=True, capture_output=True, text=True).stdout.strip()
    w, h = out.split(",")
    return int(w), int(h)


def r2_alerts() -> list[dict]:
    """Four real storage alerts with clip and snapshot: one of each event type
    per storage camera."""
    import boto3
    import psycopg2

    load_ai_env()
    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=15)
    conn.set_session(readonly=True)
    s3 = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"],
                      aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                      aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto")
    picked = []
    with conn.cursor() as cur:
        for event, camera in (("unknown_entry", "st01_cold_storage"), ("unknown_entry", "st01_storage"),
                              ("staff_outside_schedule", "st01_storage"),
                              ("staff_outside_schedule", "st01_cold_storage")):
            cur.execute(
                """SELECT a.alert_id, a.identity_label, a.identity_score, a.reason_codes_json,
                          (SELECT path FROM alert_media WHERE alert_id=a.alert_id AND kind='clip' LIMIT 1),
                          (SELECT path FROM alert_media WHERE alert_id=a.alert_id AND kind='snapshot' LIMIT 1)
                   FROM alerts a WHERE a.event_type=%s AND a.camera_id=%s
                     AND EXISTS (SELECT 1 FROM alert_media WHERE alert_id=a.alert_id AND kind='clip')
                     AND EXISTS (SELECT 1 FROM alert_media WHERE alert_id=a.alert_id AND kind='snapshot')
                   ORDER BY a.opened_at LIMIT 1 OFFSET 1""", (event, camera))
            row = cur.fetchone()
            if not row:
                raise SystemExit(f"no AI alert with media for {event} on {camera}")
            alert_id, label, score, reasons, clip_key, snap_key = row
            tmp = Path(tempfile.mkdtemp())
            clip, snap = tmp / "clip.mp4", tmp / "snapshot.jpg"
            s3.download_file(os.environ["R2_BUCKET"], clip_key, str(clip))
            s3.download_file(os.environ["R2_BUCKET"], snap_key, str(snap))
            picked.append({"event": event, "camera": camera, "label": label, "score": score,
                           "reasons": json.loads(reasons), "clip": clip, "snapshot": snap,
                           "source": alert_id})
    conn.close()
    return picked


def main() -> None:
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    specs = {spec.camera_id: spec for spec in camera_specs_from_manifest(MANIFEST)}

    cameras, zones = [], {}
    for camera_id, (video, start) in CAMERA_LOOPS.items():
        spec = specs[camera_id]
        meta = camera_metadata(spec)
        src = VIDEOS / video
        clip = PUBLIC / "cameras" / f"{camera_id}.mp4"
        encode_clip(src, clip, start, 15)
        poster(clip, clip.with_suffix(".jpg"), 1.0)
        width, height = size(clip)
        cameras.append({
            "id": camera_id, "storeId": "public-demo-store", "name": spec.display_name,
            "cameraType": spec.camera_type, "sourceType": "rtsp", "isActive": True,
            "pipelines": meta["pipelines"],
            "feed": {"videoUrl": f"{WEB}/cameras/{camera_id}.mp4", "posterUrl": f"{WEB}/cameras/{camera_id}.jpg",
                     "width": width, "height": height},
        })
        zones[camera_id] = [{"pipelineId": z["pipeline_id"], "name": z["name"], "type": z["type"],
                             "pointsNorm": z["points_norm"]} for z in meta["zones"]]

    alerts = []

    def add_alert(key, camera_id, event, severity, status, minutes_ago, label, score, reasons,
                  clip_src, clip_start, clip_len, snapshot_src, snapshot_at=None, reviewed=None):
        base = PUBLIC / "alerts" / key
        encode_clip(clip_src, base / "clip.mp4", clip_start, clip_len)
        poster(snapshot_src, base / "snapshot.jpg", snapshot_at)
        pipeline = specs[camera_id].pipeline_bindings[0].pipeline_id
        alerts.append({
            "id": f"demo-{key}", "cameraId": camera_id, "pipelineType": pipeline, "eventType": event,
            "severity": severity, "status": status, "minutesAgo": minutes_ago,
            "identityLabel": label, "identityScore": score, "reasonCodes": reasons,
            "reviewedByName": reviewed[0] if reviewed else None,
            "reviewNote": reviewed[1] if reviewed else None,
            "clipUrl": f"{WEB}/alerts/{key}/clip.mp4", "snapshotUrl": f"{WEB}/alerts/{key}/snapshot.jpg",
            "clipSeconds": clip_len,
        })

    counter = VIDEOS / "counter-generated/Right/cvlp_h3_00013_.mp4"
    add_alert("cash-concealment", "st01_counter", "suspected_cash_concealment", "high", "open", 4,
              "track_1", None, ["DRAWER_OPEN", "HAND_IN_DRAWER", "HAND_TO_BODY", "NO_CUSTOMER_TRANSACTION"],
              counter, 0, 8, counter, snapshot_at=4.0)
    entrance = VIDEOS / "entrance/entrance.mp4"
    add_alert("watchlist-entrance", "st01_entrance", "watchlist_match", "warning", "open", 11,
              "Regular shoplifter", 0.71, ["ZONE_ENTRY_CONFIRMED", "WATCHLIST_FACE_MATCHED"],
              entrance, 51.0, 12, entrance, snapshot_at=57.0)

    statuses = [("open", 2, None), ("confirmed", 95, ("Store owner", "Owner review: confirmed as a real incident.")),
                ("acknowledged", 26, None), ("confirmed", 180, ("Store manager", "Owner review: confirmed as a real incident."))]
    for (status, ago, reviewed), real in zip(statuses, r2_alerts()):
        key = f"{real['event'].replace('_', '-')}-{real['camera'].removeprefix('st01_').replace('_', '-')}"
        add_alert(key, real["camera"], real["event"], "warning", status, ago, real["label"], real["score"],
                  real["reasons"], real["clip"], R2_TRIM[0], R2_TRIM[1], real["snapshot"], reviewed=reviewed)
        shutil.rmtree(real["clip"].parent, ignore_errors=True)

    data = {"cameras": cameras, "zones": zones, "alerts": alerts}
    DATA_TS.write_text(
        "// Generated by ai/tools/build_frontend_demo.py -- do not edit by hand.\n"
        "// Public demo data: the store_test_01 cameras, zones and pipelines, and\n"
        "// alerts cut from this project's own footage (see that script).\n"
        "import type { AlertSeverity, AlertStatus, Camera, CameraZone } from './types';\n\n"
        "export interface DemoCamera extends Camera {\n"
        "  feed: { videoUrl: string; posterUrl: string; width: number; height: number };\n"
        "}\n\n"
        "export interface DemoAlert {\n"
        "  id: string; cameraId: string; pipelineType: string; eventType: string;\n"
        "  severity: AlertSeverity; status: AlertStatus; minutesAgo: number;\n"
        "  identityLabel: string | null; identityScore: number | null; reasonCodes: string[];\n"
        "  reviewedByName: string | null; reviewNote: string | null;\n"
        "  clipUrl: string; snapshotUrl: string; clipSeconds: number;\n"
        "}\n\n"
        "export const demoData: { cameras: DemoCamera[]; zones: Record<string, CameraZone[]>; alerts: DemoAlert[] } = "
        f"{json.dumps(data, indent=2, ensure_ascii=False)};\n")
    total = sum(p.stat().st_size for p in PUBLIC.rglob("*") if p.is_file())
    print(f"{len(cameras)} cameras, {len(alerts)} alerts, {total / 1e6:.1f} MB in {PUBLIC.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
