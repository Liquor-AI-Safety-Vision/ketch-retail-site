"""The one-way pipeline has to produce the same tracks the current shape does.

This is the property that makes the rewrite safe to keep. If decode -> infer
-> business produces the same boxes, features and, downstream, the same
tracker state as calling det.detect() / emb() / tracker.update() directly in
one process, then splitting the tiers is purely an optimisation. If it does
not, alerts drift and nobody notices until the numbers are already wrong.

This drives the real three tiers through run_split_pipeline.run(), using the
same VirtualVideoStreamSource the manifest-driven runtime uses -- not a
hand-rolled decode loop. An earlier version of this test decoded frames
itself and fed them to the queues one at a time before reading any result
back, and it deadlocked: with more frames than ring slots, the producer
blocked on a free slot nothing was releasing yet. That was a bug in the test
imitating a decoder badly, not in the pipeline. Driving the real decoder
tier avoids it by construction, since decode and business run concurrently
there exactly as the production shape requires.

Needs the real models and a GPU, so it skips where they are absent.

    python -m pytest backend/tests/test_split_pipeline.py -q -s
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

POSE_MODEL = ROOT / "models/yolo26m-pose.onnx"
REID_MODEL = ROOT / "models/osnet_x0_25_msmt17.onnx"
MANIFEST = ROOT / "backend/config/stores/local_dev_store_16cams.json"
CAMERA_ID = "storage_1"

pytestmark = pytest.mark.skipif(
    not (POSE_MODEL.exists() and REID_MODEL.exists() and MANIFEST.exists()),
    reason="needs the real models and the manifest's sample footage",
)

# Short enough to run in seconds; long enough to see more than an empty room.
DURATION_S = 6.0


def _camera_spec():
    """The real storage_1 camera, trimmed to a short window for the test."""
    from backend.src.infra.local_config import camera_specs_from_manifest

    specs = camera_specs_from_manifest(str(MANIFEST))
    spec = next(s for s in specs if s.camera_id == CAMERA_ID)
    spec = copy.deepcopy(spec)
    spec.target_duration_s = DURATION_S
    return spec


def _local_tracks(spec):
    """Pose -> ReID -> Tracker, called directly against the real video stream."""
    import stream_track as S
    from uae_tracker import Detection, Tracker

    from backend.src.runtime.shared_inference import SharedInferenceRegistry
    from backend.src.runtime.virtual_video_stream import VirtualVideoStreamSource

    registry = SharedInferenceRegistry(cpu_only=False, config={
        "pose_num_workers": 1, "pose_max_batch": 1,
        "pose_batch_wait_s": 0.005, "pose_pad_batch": True})
    try:
        pose = registry.get_pose(str(POSE_MODEL))

        def _build():
            S.REID_MODEL = str(REID_MODEL)
            S.ORT_PROVIDERS = None
            return S.Embedder()

        reid = registry.get_reid_embedder(str(REID_MODEL), build=_build)
        tracker = None
        per_frame = []
        for frame in VirtualVideoStreamSource(spec).iter_frames(pace_realtime=False):
            if tracker is None:
                h, w = frame.image_bgr.shape[:2]
                tracker = Tracker(img_h=h, img_w=w, fps=spec.target_fps)
            result = pose.detect(frame.image_bgr, conf=0.35)
            feats = reid(frame.image_bgr, result.boxes)
            dets = [Detection(result.boxes[i, :4], result.boxes[i, 4], feats[i], result.kpts[i])
                   for i in range(len(result.boxes))]
            live, _removed = tracker.update(dets, frame.frame_idx)
            per_frame.append((len(result.boxes), len(live)))
        return per_frame, int(getattr(tracker, "_next_id", 0))
    finally:
        registry.close()


def _split_tracks(spec):
    """The same camera, through the real decode -> infer -> business tiers."""
    from backend.src.app.run_split_pipeline import run

    stats, _load_s = run(
        [spec], decoders=1, inferers=1, infer_threads=1, businesses=1,
        pose_model=str(POSE_MODEL), reid_model=str(REID_MODEL),
        till_model=None, till_imgsz=640, conf=0.35, till_conf=None,
        till_every_n=1, slots_per_camera=6)
    biz_stats = [s for group in stats for s in group if hasattr(s, "tracks_created")]
    assert len(biz_stats) == 1
    return biz_stats[0]


def test_split_matches_local_tracking():
    spec = _camera_spec()
    local_per_frame, local_tracks = _local_tracks(spec)
    biz = _split_tracks(spec)

    assert biz.frames == len(local_per_frame), (
        f"split processed {biz.frames} frames, local processed "
        f"{len(local_per_frame)} -- the two runs did not even see the same "
        f"number of frames from the same camera")
    local_live_total = sum(live for _dets, live in local_per_frame)
    assert biz.track_frames_total == local_live_total, (
        "per-frame live-track counts diverged between the split pipeline "
        "and a direct in-process call")
    assert biz.tracks_created == local_tracks, (
        f"split created {biz.tracks_created} tracks, local created "
        f"{local_tracks} -- identical detections should never produce "
        "different tracker outcomes")
