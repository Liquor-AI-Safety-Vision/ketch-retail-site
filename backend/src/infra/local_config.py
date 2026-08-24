from __future__ import annotations

import json
from pathlib import Path

from backend.src.common.paths import resolve_repo_path
from backend.src.domain.models import (
    CameraPipelineBindingSpec,
    CameraStreamSpec,
    StaffSeedEntry,
)


def load_store_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def _resolve_optional_path(value: str | None) -> str | None:
    if not value:
        return value
    return resolve_repo_path(value)


def _parse_pipeline_bindings(camera: dict) -> list[CameraPipelineBindingSpec]:
    bindings: list[CameraPipelineBindingSpec] = []
    for item in camera.get("pipelines", []):
        if isinstance(item, str):
            bindings.append(
                CameraPipelineBindingSpec(
                    pipeline_id=item,
                    enabled=True,
                    params={},
                )
            )
            continue

        bindings.append(
            CameraPipelineBindingSpec(
                pipeline_id=item["pipeline_id"],
                enabled=bool(item.get("enabled", True)),
                params=dict(item.get("params", {})),
            )
        )
    return bindings


def camera_specs_from_manifest(path: str | Path) -> list[CameraStreamSpec]:
    data = load_store_manifest(path)
    runtime_defaults = dict(data.get("runtime", {}))
    default_target_fps = float(runtime_defaults.get("default_target_fps", data.get("target_fps", 5.0)))
    default_target_duration_s = float(
        runtime_defaults.get("target_duration_s", data.get("target_runtime_s", 0.0))
    )
    default_simulation_profile = dict(runtime_defaults.get("simulation_clock", {}))
    specs: list[CameraStreamSpec] = []
    for camera in data["cameras"]:
        stream = dict(camera.get("stream", camera.get("source", {})))
        simulation = dict(stream.get("simulation", {}))
        runtime_profile = dict(camera.get("runtime", {}))
        perception_profile = dict(camera.get("perception", {}))
        pipeline_bindings = _parse_pipeline_bindings(camera)
        source_kind = stream.get("input_mode", stream.get("kind"))
        source_uri = stream.get("uri", stream.get("path"))
        resolved_source_uri = _resolve_optional_path(source_uri)
        target_duration_s = float(
            simulation.get("target_duration_s", default_target_duration_s)
        )
        for binding in pipeline_bindings:
            params = dict(binding.params)
            for key in ("zone_config_path", "zones_path", "till_model_path"):
                if key in params:
                    params[key] = resolve_repo_path(params[key])
            binding.params = params
        specs.append(
            CameraStreamSpec(
                company_id=data.get("company_id"),
                store_id=data["store_id"],
                store_timezone=data["timezone"],
                camera_id=camera["camera_id"],
                display_name=camera["display_name"],
                camera_type=camera["camera_type"],
                source_kind=source_kind,
                source_uri=resolved_source_uri,
                source_path=resolved_source_uri,
                strategy=simulation.get("strategy", stream.get("strategy", "trim")),
                start_s=float(simulation.get("start_s", stream.get("start_s", 0.0))),
                target_duration_s=target_duration_s,
                target_fps=float(runtime_profile.get("target_fps", default_target_fps)),
                runtime_profile=runtime_profile,
                perception_profile=perception_profile,
                simulation_profile={
                    **default_simulation_profile,
                    **simulation,
                },
                pipeline_bindings=pipeline_bindings,
                raw_config=camera,
            )
        )
    return specs


def load_staff_seed_entries(path: str | Path) -> tuple[str, str, list[StaffSeedEntry]]:
    data = json.loads(Path(path).read_text())
    entries = []
    for item in data["entries"]:
        entries.append(
            StaffSeedEntry(
                person_id=item["person_id"],
                display_name=item["display_name"],
                is_staff=bool(item.get("is_staff", True)),
                company_id=item.get("company_id", data.get("company_id")),
                store_id=item.get("store_id", data.get("store_id")),
                video_path=resolve_repo_path(item["video_path"]),
                timestamp_s=float(item["timestamp_s"]),
                search_window_s=float(item.get("search_window_s", 0.0)),
                crop_box_xyxy=tuple(item["crop_box_xyxy"]),
                metadata=dict(item.get("metadata", {})),
            )
        )
    return resolve_repo_path(data["db_path"]), resolve_repo_path(data["artifacts_dir"]), entries
