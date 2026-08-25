"""The remote path has to give the same answer as the local one.

This is the property that makes the transport change safe: a pipeline calling
det.detect(frame, conf=...) must not be able to tell whether the models live
in its own process or another one. If that holds, moving them is an
optimisation; if it does not, it is a behaviour change wearing an
optimisation's clothes, and the alert set would drift without anyone
noticing.

Needs the real models and a GPU, so it skips where they are absent.

    python -m pytest backend/tests/test_inference_server.py -q -s
"""
from __future__ import annotations

import multiprocessing as mp
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
SAMPLE_VIDEO = ROOT / "videos/cctv_storage/storage_1.mp4"

pytestmark = pytest.mark.skipif(
    not (POSE_MODEL.exists() and REID_MODEL.exists() and SAMPLE_VIDEO.exists()),
    reason="needs the real models and sample footage",
)


def _sample_frames(n: int = 3) -> list[np.ndarray]:
    import cv2

    cap = cv2.VideoCapture(str(SAMPLE_VIDEO))
    # Well past the opening, where the room is more likely to have someone in
    # it: a test that only ever sees zero detections proves very little.
    cap.set(cv2.CAP_PROP_POS_MSEC, 60_000)
    frames = []
    for _ in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(np.ascontiguousarray(frame))
    cap.release()
    assert frames, "could not read sample footage"
    return frames


def _local_results(frames):
    from backend.src.runtime.shared_inference import SharedInferenceRegistry

    registry = SharedInferenceRegistry(cpu_only=False, config={
        "pose_num_workers": 1, "pose_max_batch": 1,
        "pose_batch_wait_s": 0.005, "pose_pad_batch": True})
    try:
        import stream_track as S

        pose = registry.get_pose(str(POSE_MODEL))

        def _build():
            S.REID_MODEL = str(REID_MODEL)
            S.ORT_PROVIDERS = None
            return S.Embedder()

        reid = registry.get_reid_embedder(str(REID_MODEL), build=_build)
        out = []
        for frame in frames:
            result = pose.detect(frame, conf=0.35)
            feats = reid(frame, result.boxes)
            out.append((result.boxes, result.kpts, feats))
        return out
    finally:
        registry.close()


def test_remote_matches_local():
    from backend.src.runtime.frame_ring import FrameRing
    from backend.src.runtime.inference_server import (
        ClientChannel, InferenceClient, ServerConfig, serve,
    )

    frames = _sample_frames()
    local = _local_results(frames)

    ctx = mp.get_context("spawn")
    ring, free_q = FrameRing.create(ctx, slots=4, frame_shape=frames[0].shape)
    channel = ClientChannel(camera_id="cam_under_test", ring_spec=ring.spec,
                            reply_q=ctx.Queue(), lane=0)
    request_q = ctx.Queue()
    ready_q, stats_q = ctx.Queue(), ctx.Queue()
    go_evt = ctx.Event()

    config = ServerConfig(pose_model=str(POSE_MODEL), reid_model=str(REID_MODEL),
                          cpu_only=False)
    proc = ctx.Process(target=serve,
                       args=(config, [channel], [request_q], ready_q, go_evt, stats_q))
    proc.start()
    try:
        assert ready_q.get(timeout=300)[0] == "inference"
        go_evt.set()

        client = InferenceClient(channel, free_q, request_q)
        try:
            pose = client.get_pose(str(POSE_MODEL))
            reid = client.get_reid_embedder(str(REID_MODEL))
            for (want_boxes, want_kpts, want_feats), frame in zip(local, frames):
                got = pose.detect(frame, conf=0.35)
                np.testing.assert_allclose(got.boxes, want_boxes, rtol=0, atol=0)
                assert len(got.kpts) == len(want_kpts)
                for a, b in zip(got.kpts, want_kpts):
                    np.testing.assert_allclose(a, b, rtol=0, atol=0)
                got_feats = reid(frame, got.boxes)
                np.testing.assert_allclose(got_feats, want_feats, rtol=0, atol=0)
        finally:
            client.close()
            request_q.put(None)
    finally:
        proc.join(timeout=120)
        if proc.is_alive():
            proc.terminate()
        ring.close()


def test_server_failure_reaches_the_caller():
    """A caller blocked on a reply must get an exception, not hang forever."""
    from backend.src.runtime.frame_ring import FrameRing
    from backend.src.runtime.inference_server import ClientChannel, InferenceClient

    ctx = mp.get_context("spawn")
    ring, free_q = FrameRing.create(ctx, slots=2, frame_shape=(16, 16, 3))
    channel = ClientChannel(camera_id="cam", ring_spec=ring.spec,
                            reply_q=ctx.Queue(), lane=0)
    request_q = ctx.Queue()
    try:
        client = InferenceClient(channel, free_q, request_q)
        channel.reply_q.put((False, "boom on the server"))
        with pytest.raises(RuntimeError, match="boom on the server"):
            client.get_pose("unused").detect(np.zeros((16, 16, 3), np.uint8), conf=0.3)
        # The slot has to come back even when the call failed, or a few
        # errors would starve the ring and wedge the camera.
        assert free_q.get(timeout=1) in (0, 1)
        client.close()
    finally:
        ring.close()
