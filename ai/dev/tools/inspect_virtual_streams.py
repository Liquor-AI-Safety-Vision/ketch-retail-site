
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[3]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import sys
from pathlib import Path

ROOT = _Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from ai.src.infra.local_config import camera_specs_from_manifest
from ai.src.runtime.virtual_video_stream import VirtualVideoStreamSource
from ai.src.common.paths import ai_root


def main():
    p = argparse.ArgumentParser(description=__doc__ or "Inspect local virtual stream plans")
    p.add_argument(
        "--manifest",
        default=str(ai_root() / "config/stores/local_dev_store.json"),
    )
    p.add_argument(
        "--sample-frames",
        type=int,
        default=3,
        help="Dry-run this many frames per source without real-time pacing.",
    )
    a = p.parse_args()

    specs = camera_specs_from_manifest(a.manifest)
    for spec in specs:
        src = VirtualVideoStreamSource(spec)
        target_frames = int(round(spec.target_duration_s * spec.target_fps))
        print(
            f"{spec.camera_id}: type={spec.camera_type} "
            f"source={spec.source_kind} "
            f"native={src.info.native_fps:.3f}fps/{src.info.duration_s:.1f}s "
            f"target={spec.target_fps:.1f}fps/{spec.target_duration_s:.1f}s "
            f"strategy={spec.strategy} target_frames={target_frames}"
        )
        if spec.simulation_profile:
            print(f"  simulation={spec.simulation_profile}")
        print(f"  pipelines={spec.pipeline_ids}")
        for i, frame in enumerate(src.iter_frames(pace_realtime=False)):
            print(
                f"  sample[{i}] emitted={frame.frame_idx} "
                f"source_frame={frame.source_frame_idx} "
                f"source_t={frame.source_time_s:.2f}s loop={frame.loop_index}"
            )
            if i + 1 >= a.sample_frames:
                break


if __name__ == "__main__":
    main()
