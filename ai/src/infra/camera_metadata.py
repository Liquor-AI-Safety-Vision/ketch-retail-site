"""What a camera is configured to detect, written into cameras.metadata_json.

The dashboard API (backend/, Go) shows each camera's pipelines and zones but
has no access to this directory's config files -- it may not even run on the
same machine. So the seeders that already write the cameras row from the
manifest at every pipeline start also write this:

    {"pipelines": ["counter_cash_theft_v1"],
     "zones": [{"pipeline_id": "counter_cash_theft_v1", "name": "checkout_left",
                "type": "polygon", "points_norm": [[x, y], ...]}, ...]}

points_norm are fractions of frame width/height, exactly as the zone files
store them, so the dashboard can draw them over any rendition of the stream.

The zone files are read here the same way the pipelines read them at
bootstrap -- zones_path for the counter, zone_config_path[zone_asset_key]
["zone_norm"] for storage and entrance -- and the defaults are the pipelines'
own. Display only: nothing in detection reads this column.

A zone file that cannot be read does not stop seeding. The pipeline that needs
it fails at its own bootstrap exactly as it always has; this records why the
dashboard has no zones for that camera instead of hiding it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai.src.common.paths import zones_dir
from ai.src.pipelines.registry import COUNTER_CASH_THEFT, ENTRANCE_FACE_CAPTURE, INTRUSION_IDS


def _counter_zones(params: dict, pipeline_id: str) -> list[dict]:
    path = params.get("zones_path", str(zones_dir() / "top_side_view_zones.json"))
    data = json.loads(Path(path).read_text())
    out = []
    for zone in data.get("zones", []):
        if "points_norm" not in zone:
            continue
        out.append({"pipeline_id": pipeline_id, "name": zone.get("name", ""),
                    "type": zone.get("type", "polygon"), "points_norm": zone["points_norm"]})
    return out


def _area_zone(params: dict, pipeline_id: str, default_key: str | None) -> list[dict]:
    key = params.get("zone_asset_key", default_key)
    data = json.loads(Path(params["zone_config_path"]).read_text())
    return [{"pipeline_id": pipeline_id, "name": key, "type": "polygon",
             "points_norm": data[key]["zone_norm"]}]


def camera_metadata(spec) -> dict[str, Any]:
    pipelines: list[str] = []
    zones: list[dict] = []
    errors: list[str] = []
    for binding in spec.pipeline_bindings:
        if not binding.enabled:
            continue
        pid = binding.pipeline_id
        pipelines.append(pid)
        try:
            if pid == COUNTER_CASH_THEFT:
                zones += _counter_zones(binding.params, pid)
            elif pid in INTRUSION_IDS:
                zones += _area_zone(binding.params, pid, None)
            elif pid == ENTRANCE_FACE_CAPTURE:
                zones += _area_zone(binding.params, pid, "entrance")
        except (OSError, KeyError, ValueError, TypeError) as e:
            errors.append(f"{pid}: {type(e).__name__}: {e}")
    meta: dict[str, Any] = {"pipelines": pipelines, "zones": zones}
    if errors:
        meta["zone_errors"] = errors
    return meta


def camera_metadata_json(spec) -> str:
    return json.dumps(camera_metadata(spec), ensure_ascii=True, sort_keys=True)
