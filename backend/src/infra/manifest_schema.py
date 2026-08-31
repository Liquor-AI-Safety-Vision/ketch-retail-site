"""Reject a manifest that says something the system will not do.

A manifest used to be parsed with .get() and nothing else, so a key nobody
reads was indistinguishable from a key that works. Three failures, all silent,
all measured on the shipped manifests:

    "match_treshold": 0.9      typo -- sits beside the real key, never read.
                               The operator believes the threshold is 0.9.
                               It is 0.25, and nothing anywhere says so.

    "target_fps": "5"          a string where a number belongs. float("5")
                               happens to work, so this one survives until a
                               value like "5 fps" reaches the same line.

    "emit_customer_tracks"     a key no code reads at all, present in six
                               manifests, describing behaviour that does not
                               exist.

So this module holds the set of keys the code actually consumes, and refuses
anything outside it.

The distinction that matters: a key can be UNKNOWN (a typo, or an invented
setting -- an error, because the author expected an effect they will not get)
or INERT (spelled correctly, shipped in the manifests, but read by nobody --
a warning naming it, because deleting them is a separate decision from
detecting them). Both were invisible before; only one is an error.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

NUMBER = (int, float)


@dataclass(frozen=True)
class Section:
    """One nesting level of the manifest."""

    keys: dict[str, type | tuple[type, ...]]
    required: frozenset[str] = frozenset()
    #: Groups where at least one member must be present. Exists because
    #: local_config genuinely reads legacy aliases -- stream.get("input_mode",
    #: stream.get("kind")) -- so demanding the modern spelling would reject
    #: manifests the code loads correctly today.
    required_any: tuple[frozenset[str], ...] = ()
    #: Correctly spelled, present in shipped manifests, consumed by nothing.
    #: Listed rather than removed so the validator can say so instead of
    #: either silently accepting them or breaking every manifest at once.
    inert: frozenset[str] = frozenset()
    #: Sections nested inside this one.
    children: dict[str, "Section"] = field(default_factory=dict)
    #: Keys whose values are free-form (per-pipeline params validated apart).
    opaque: frozenset[str] = frozenset()


_ALERT_CLIP = Section(keys={"pre_s": NUMBER, "post_s": NUMBER})

_SIMULATION_CLOCK = Section(
    keys={"enabled": bool, "started_at_local": str, "playback_speed": NUMBER}
)

_ACCESS_WINDOW = Section(
    keys={"days": list, "start": str, "end": str},
    required=frozenset({"days", "start", "end"}),
)

# Per-pipeline params. Anything not listed for the pipeline a camera binds is
# a typo -- that is the whole point of keying this by pipeline_id.
PIPELINE_PARAMS: dict[str, Section] = {
    "counter_cash_theft_v1": Section(
        keys={
            "suppress_if_customer_recent": bool,
            "customer_recent_window_s": NUMBER,
            "zones_path": str,
            "till_conf": NUMBER,
            "till_confirm_frames": int,
            "till_fps": NUMBER,
            "till_imgsz": int,
            "till_model_path": str,
            "any_hand": bool,
            "wrist_conf_th": NUMBER,
            "alert_clip": dict,
        },
        children={"alert_clip": _ALERT_CLIP},
    ),
    "storage_intrusion_v1": Section(
        keys={
            "entry_confirm_seconds": NUMBER,
            "zone_config_path": str,
            "zone_asset_key": str,
            "face_capture_window": dict,
            "access_schedule": dict,
            "alert_clip": dict,
        },
        required=frozenset({"zone_config_path", "zone_asset_key"}),
        children={
            "alert_clip": _ALERT_CLIP,
            "face_capture_window": Section(
                keys={"end_after_inside_s": NUMBER, "start_status": str},
                inert=frozenset({"start_status"}),
            ),
            "access_schedule": Section(keys={"allowed_windows": list}),
        },
    ),
    "whole_store_tracking_v1": Section(
        keys={"emit_customer_tracks": bool},
        inert=frozenset({"emit_customer_tracks"}),
    ),
}
PIPELINE_PARAMS["cold_storage_intrusion_v1"] = PIPELINE_PARAMS["storage_intrusion_v1"]

CAMERA = Section(
    keys={
        "camera_id": str, "display_name": str, "camera_type": str,
        "stream": dict, "runtime": dict, "perception": dict, "pipelines": list,
        "source": dict,   # legacy alias for "stream"
    },
    required=frozenset({"camera_id", "display_name", "camera_type", "pipelines"}),
    required_any=(frozenset({"stream", "source"}),),
    children={
        "stream": Section(
            keys={
                "input_mode": str, "uri": str, "simulation": dict, "rtsp": dict,
                # Legacy spellings local_config still falls back to.
                "kind": str, "path": str, "strategy": str, "start_s": NUMBER,
            },
            required_any=(frozenset({"input_mode", "kind"}),
                          frozenset({"uri", "path"})),
            children={
                "simulation": Section(
                    keys={"strategy": str, "start_s": NUMBER, "target_duration_s": NUMBER}
                ),
                "rtsp": Section(
                    keys={
                        "transport": str, "open_timeout_ms": int, "read_timeout_ms": int,
                        "max_reconnects": int, "reconnect_backoff_s": NUMBER,
                        "reconnect_backoff_max_s": NUMBER,
                    }
                ),
            },
        ),
        "runtime": Section(keys={"target_fps": NUMBER, "device": str},
                           inert=frozenset({"device"})),
        "perception": Section(
            keys={"person_detector": dict, "person_tracker": dict, "face_identity": dict},
            children={
                "person_detector": Section(
                    keys={"device": str, "conf_thres": NUMBER, "nms_thres": NUMBER},
                    inert=frozenset({"device", "nms_thres"}),
                ),
                "person_tracker": Section(
                    keys={"device": str, "match_thres": NUMBER},
                    inert=frozenset({"device", "match_thres"}),
                ),
                "face_identity": Section(
                    keys={
                        "enabled": bool, "device": str, "match_threshold": NUMBER,
                        "match_margin": NUMBER, "face_conf": NUMBER,
                        "crop_expand": NUMBER, "iou_thres": NUMBER,
                    },
                    inert=frozenset({"device"}),
                ),
            },
        ),
    },
)

# "source" is validated by the same schema as "stream": local_config reads
# camera.get("stream", camera.get("source", {})), so both spellings reach the
# identical parsing path and must face the identical rules.
CAMERA.children["source"] = CAMERA.children["stream"]


MANIFEST = Section(
    keys={
        "company_id": str, "store_id": str, "display_name": str, "timezone": str,
        "mode": str, "runtime": dict, "policies": dict, "cameras": list,
        # Legacy spellings read as fallbacks by local_config.
        "target_fps": NUMBER, "target_runtime_s": NUMBER,
    },
    required=frozenset({"store_id", "timezone", "cameras"}),
    inert=frozenset({"mode"}),
    children={
        "runtime": Section(
            keys={
                "default_target_fps": NUMBER, "target_duration_s": NUMBER,
                "drop_if_lagging": bool, "simulation_clock": dict,
            },
            children={"simulation_clock": _SIMULATION_CLOCK},
        ),
        "policies": Section(
            keys={"identity_gallery_scope": str, "default_alert_clip": dict},
            children={"default_alert_clip": _ALERT_CLIP},
        ),
    },
)


class ManifestError(ValueError):
    """A manifest asks for something the system does not implement."""


def _type_name(expected) -> str:
    if isinstance(expected, tuple):
        return " or ".join(t.__name__ for t in expected)
    return expected.__name__


def _schema_path(where: str, name: str) -> str:
    """Drop the camera identity so the same key in 16 cameras is one finding."""
    trimmed = re.sub(r"^camera [^.]+\.?", "", where)
    trimmed = re.sub(r"^manifest\.?", "", trimmed)
    return f"{trimmed}.{name}".lstrip(".")


def _check(node: Any, section: Section, where: str,
           errors: list[str], inert_hits: dict[str, int]) -> None:
    if not isinstance(node, dict):
        errors.append(f"{where}: expected an object, got {type(node).__name__}")
        return

    for name in sorted(section.required - node.keys()):
        errors.append(f"{where}.{name}: required, missing")
    for group in section.required_any:
        if not (group & node.keys()):
            errors.append(
                f"{where}: one of {', '.join(sorted(group))} is required, none present")

    for name, value in node.items():
        if name not in section.keys:
            near = _closest(name, section.keys)
            hint = f" -- did you mean {near!r}?" if near else ""
            errors.append(f"{where}.{name}: unknown key{hint}")
            continue
        if name in section.inert:
            # Keyed by the schema path, not the camera, then counted once at
            # the end. Reported per camera this was 5 keys x 16 cameras = 80
            # identical lines per run, which is how a real warning gets missed.
            inert_hits[_schema_path(where, name)] = inert_hits.get(
                _schema_path(where, name), 0) + 1
        expected = section.keys[name]
        # bool is a subclass of int; a bool where a number belongs is a mistake
        # worth catching, and a number where a bool belongs certainly is.
        ok = isinstance(value, expected) and not (
            expected is not bool and isinstance(value, bool))
        if not ok:
            errors.append(
                f"{where}.{name}: expected {_type_name(expected)}, "
                f"got {type(value).__name__} ({value!r})")
            continue
        child = section.children.get(name)
        if child is not None:
            _check(value, child, f"{where}.{name}", errors, inert_hits)


def _closest(name: str, candidates) -> str | None:
    """Nearest known key, so a typo names its own fix.

    A plain edit distance would suggest 'device' for 'devise' but nothing for
    'match_treshold' vs 'match_threshold' if the cutoff were tight, so the
    cutoff is loose and the suggestion is offered, never asserted.
    """
    import difflib

    hits = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.75)
    return hits[0] if hits else None


def validate_manifest(data: dict, *, path: str = "<manifest>") -> list[str]:
    """Raise ManifestError on anything unusable. Return warnings.

    Errors are collected across the whole file rather than raised at the first
    one: fixing manifests one message per run is how a five-typo file takes
    five runs to load.
    """
    from backend.src.pipelines.registry import KNOWN_PIPELINE_IDS

    errors: list[str] = []
    inert_hits: dict[str, int] = {}
    _check(data, MANIFEST, "manifest", errors, inert_hits)

    for idx, camera in enumerate(data.get("cameras", []) or []):
        cam_id = (camera or {}).get("camera_id", f"[{idx}]") if isinstance(camera, dict) else f"[{idx}]"
        where = f"camera {cam_id}"
        _check(camera, CAMERA, where, errors, inert_hits)
        if not isinstance(camera, dict):
            continue

        for binding in camera.get("pipelines", []) or []:
            if not isinstance(binding, dict):
                errors.append(f"{where}.pipelines: expected objects")
                continue
            pid = binding.get("pipeline_id")
            for name in binding:
                if name not in {"pipeline_id", "enabled", "params"}:
                    errors.append(f"{where}.pipelines.{name}: unknown key")
            if pid is None:
                errors.append(f"{where}.pipelines: pipeline_id is required")
                continue
            if pid not in KNOWN_PIPELINE_IDS:
                errors.append(
                    f"{where}.pipelines.pipeline_id: unknown pipeline {pid!r}. "
                    f"Known: {', '.join(sorted(KNOWN_PIPELINE_IDS))}")
                continue
            schema = PIPELINE_PARAMS.get(pid)
            params = binding.get("params", {})
            if schema is not None and params:
                _check(params, schema, f"{where}.params[{pid}]", errors, inert_hits)
            # allowed_windows entries are a list of objects, checked apart
            sched = (params or {}).get("access_schedule", {})
            if isinstance(sched, dict):
                for w_idx, window in enumerate(sched.get("allowed_windows", []) or []):
                    _check(window, _ACCESS_WINDOW,
                           f"{where}.params[{pid}].access_schedule.allowed_windows[{w_idx}]",
                           errors, inert_hits)

    if errors:
        raise ManifestError(
            f"{path}: {len(errors)} problem(s)\n  " + "\n  ".join(errors))

    return [
        f"{key}: configures nothing -- no code reads it "
        f"({count} camera(s) set it)" if count > 1 else
        f"{key}: configures nothing -- no code reads it"
        for key, count in sorted(inert_hits.items())
    ]
