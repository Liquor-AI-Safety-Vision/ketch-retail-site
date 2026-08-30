"""The one-way pipeline has to produce the same tracks the current shape does.

This is the property that makes the rewrite safe to keep. If decode -> infer
-> business, running the real TrackingBaselinePipeline, produces the same
tracker state as calling det.detect() / emb() / tracker.update() directly in
one process, then splitting the tiers is purely an optimisation. If it does
not, alerts drift and nobody notices until the numbers are already wrong.

This drives the real four tiers through run_split_pipeline.run() -- decode,
inference, business (running the actual pipeline classes, not a bare
tracker), and the face tier when a camera needs it -- using the same
VirtualVideoStreamSource the manifest-driven runtime uses. An earlier
version of this test decoded frames itself and fed them to the queues one at
a time before reading any result back, and it deadlocked: with more frames
than ring slots, the producer blocked on a free slot nothing was releasing
yet. That was a bug in the test imitating a decoder badly, not in the
pipeline. Driving the real decoder tier avoids it by construction, since
decode and business run concurrently there exactly as the production shape
requires.

Needs the real models and a GPU, so it skips where they are absent.

    python -m pytest backend/tests/test_split_pipeline.py -q -s
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pytest
from backend.src.common.paths import backend_root, model_path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

POSE_MODEL = Path(model_path("yolo26m-pose.onnx"))
REID_MODEL = Path(model_path("osnet_x0_25_msmt17.onnx"))
MANIFEST = backend_root() / "config/stores/local_dev_store_16cams.json"
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
    """Pose -> ReID -> Tracker, called directly against the real video stream.

    Deliberately bare -- pose, ReID and tracker.update() only, matching what
    the split side's tracker actually sees. storage_intrusion's own
    min_box_h filter, zone monitor and entry windows sit on top of this in
    both architectures identically (same handle_perceived code either way),
    so they are not what this test is checking; the split's own zone/entry
    logic isn't a second implementation to drift against.
    """
    from backend.src.perception import stream_track as S
    from backend.src.perception.uae_tracker import Detection, Tracker

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
            conf = float(spec.perception_profile.get("person_detector", {}).get("conf_thres", 0.45))
            result = pose.detect(frame.image_bgr, conf=conf)
            feats = reid(frame.image_bgr, result.boxes)
            dets = [Detection(result.boxes[i, :4], result.boxes[i, 4], feats[i], result.kpts[i])
                   for i in range(len(result.boxes))]
            live, _removed = tracker.update(dets, frame.frame_idx)
            per_frame.append(len(live))
        return per_frame, int(getattr(tracker, "_next_id", 0))
    finally:
        registry.close()


def _split_run(spec, tmp_path):
    """The same camera, through the real four-tier split pipeline."""
    from backend.src.app.run_split_pipeline import run

    biz_stats, _all_stats, _load_s = run(
        [spec], decoders=1, inferers=1, infer_threads=1, businesses=1,
        pose_model=str(POSE_MODEL), reid_model=str(REID_MODEL),
        till_model=model_path("till_seg_v4_640.onnx"), till_imgsz=640,
        face_model=model_path("yolov6s_face.onnx"),
        arcface_model=model_path("ms1mv3_arcface_r18.onnx"),
        db_path=str(tmp_path / "test_split.sqlite"),
        media_root=str(tmp_path / "alerts"),
        gallery_db_path=str(backend_root() / "data/local_dev.sqlite"),
        slots_per_camera=8)
    assert len(biz_stats) == 1
    return biz_stats[0]


def test_split_matches_local_tracking(tmp_path):
    spec = _camera_spec()
    local_per_frame, local_tracks = _local_tracks(spec)
    stats = _split_run(spec, tmp_path)

    pipeline_id = spec.pipeline_bindings[0].pipeline_id
    pm = stats.pipeline_metrics[pipeline_id]

    assert stats.frames_processed == len(local_per_frame), (
        f"split processed {stats.frames_processed} frames, local processed "
        f"{len(local_per_frame)} -- the two runs did not even see the same "
        f"number of frames from the same camera")
    local_live_total = sum(local_per_frame)
    assert pm["track_frames_total"] == local_live_total, (
        "per-frame live-track counts diverged between the split pipeline "
        "and a direct in-process call")
    assert pm["tracks_created"] == local_tracks, (
        f"split created {pm['tracks_created']} tracks, local created "
        f"{local_tracks} -- identical detections should never produce "
        "different tracker outcomes")
