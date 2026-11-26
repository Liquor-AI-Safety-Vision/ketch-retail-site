from __future__ import annotations

import json
import logging
from pathlib import Path

from ai.src.common.paths import resolve_repo_path
from ai.src.common.stream_uri import resolve_source_uri
from ai.src.domain.models import (
    CameraPipelineBindingSpec,
    CameraStreamSpec,
    StaffSeedEntry,
)
from ai.src.infra.manifest_schema import validate_manifest


logger = logging.getLogger(__name__)

#: Inert-key warnings already reported by this process.
_WARNED: set[str] = set()


def load_store_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def _resolve_optional_path(value: str | None) -> str | None:
    """Resolve a camera source, which may be a file path or an rtsp:// URI.

    Goes through resolve_source_uri rather than resolve_repo_path: the latter
    treats everything as a filesystem path and turns "rtsp://host/path" into
    "<repo>/rtsp:/host/path", which fails to open with no hint as to why.
    """
    if not value:
        return value
    return resolve_source_uri(value)


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
    # Validated before anything is read out of it. Every access below is a
    # .get() with a fallback, so an unrecognised key used to reach the running
    # system as silence -- the camera came up, used the default, and reported
    # nothing unusual.
    for warning in validate_manifest(data, path=str(path)):
        # Once per process, not once per manifest. A four-store run loads four
        # files that share the same dead keys, and a warning repeated four
        # times reads as four problems.
        if warning in _WARNED:
            continue
        _WARNED.add(warning)
        logger.warning("manifest: %s", warning)
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
        playlist = [
            {"path": _resolve_optional_path(entry["uri"]), "repeat": int(entry.get("repeat", 1))}
            for entry in stream.get("playlist", []) or []
        ]
        target_duration_s = float(
            simulation.get("target_duration_s", default_target_duration_s)
        )
        for binding in pipeline_bindings:
            params = dict(binding.params)
            for key in ("zone_config_path", "zones_path"):
                if key in params:
                    params[key] = resolve_repo_path(params[key])
            binding.params = params
        # Model overrides live under perception, not params, and were not
        # being resolved -- so a camera pointing at ${AI_ROOT}/models/...
        # would hand onnxruntime that literal string and fail at bootstrap,
        # on a tier process, long after the manifest was read.
        for block, keys in (("person_detector", ("model_path",)),
                            ("person_tracker", ("reid_model_path",)),
                            ("face_identity", ("model_path", "arcface_model_path"))):
            section = perception_profile.get(block)
            if not isinstance(section, dict):
                continue
            section = dict(section)
            for key in keys:
                if key in section:
                    section[key] = resolve_repo_path(section[key])
            perception_profile[block] = section
        specs.append(
            CameraStreamSpec(
                company_id=data.get("company_id"),
                store_id=data["store_id"],
                store_display_name=data.get("display_name") or data["store_id"],
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
                playlist=playlist,
            )
        )
    return specs


def camera_specs_from_manifests(paths: list[str | Path]) -> list[CameraStreamSpec]:
    """One manifest per store, merged into the flat list every tier consumes.

    The split pipeline's tiers were never written to know what a "store" is
    -- decode/inference/business shard a plain list of CameraStreamSpec, and
    each spec already carries its own store_id (local_config.py copies it per
    camera, falling back to the manifest's default). So running several
    stores together needs nothing from the tiers themselves; it only needs
    their specs concatenated before ring/queue allocation.

    The one thing that would corrupt silently if left unchecked: rings, decode
    queues and business queues are all keyed by camera_id. A camera_id reused
    across two manifests -- whether by copy-paste, or by two stores each
    independently numbering their own cameras "camera_1" -- would have the
    second spec's ring allocation overwrite the first's dict entry, so one
    store's frames would silently land in another store's ring. Checked here
    once, at merge time, rather than trusted to whoever authored the files.
    """
    specs: list[CameraStreamSpec] = []
    seen: dict[str, str] = {}
    for path in paths:
        for spec in camera_specs_from_manifest(path):
            prior_path = seen.get(spec.camera_id)
            if prior_path is not None:
                raise ValueError(
                    f"camera_id {spec.camera_id!r} appears in both {prior_path} and "
                    f"{path}; every camera_id must be unique across every manifest "
                    f"merged into one run, since rings and queues are keyed by it")
            seen[spec.camera_id] = str(path)
            specs.append(spec)
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
