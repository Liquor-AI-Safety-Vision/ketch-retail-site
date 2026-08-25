"""Run the GPU models in their own process, as a one-way pipeline.

The first version of this module was a request/reply proxy: a camera thread
called det.detect(frame), blocked on a reply, and the pipelines needed no
changes at all. It was measured and rejected. A ping with no model behind it
cost 2.30ms; the same ping against a process that merely had models loaded,
untouched, cost 9.35ms. Three explanations were tested and ruled out --
onnxruntime's thread count, its spin-waiting, the caller's own CUDA context --
which leaves the shape: blocking request/reply asks the process least able to
answer quickly two or three times per frame, and a frame's real cost is only
about 25ms to begin with.

This version does not ask. Each frame crosses each tier boundary exactly
once, in one direction:

    decode (no CUDA) --frame+meta--> inference (the only CUDA) --result--> business (no CUDA)

Nobody blocks waiting for a reply from the inference tier; a decoder that
cannot get a slot blocks on the free list instead, which is ordinary
backpressure, not a round trip. This is the shape the reference
implementation uses, and it is also what the 16-camera spike measured before
this module existed: 174.81 FPS against the current architecture's 136.30,
+28.2%, with 460 tracks created identically on both sides. That spike used
throwaway inline code; this module is the production version of the same
shape, built on FrameRing and the real Tracker.

Three rules that were each the subject of a bug already made once here:

  * A camera is pinned to one inference lane and one business worker. A queue
    with a single consumer preserves write order, and till_confirm needs
    consecutive samples -- a frame arriving out of order silently breaks the
    debounce. Pinning removes the need for a re-sorting stage.

  * Each inference lane owns a private Embedder. Its __call__ writes into a
    shared self._pad_batch; lanes sharing one instance corrupt each other's
    features. Caught once already as two architectures disagreeing on tracks
    created, 134 against 132.

  * Slots are released by whichever tier is last to touch the frame -- the
    business tier, after tracking and any clip or face work that reads pixels
    -- never by the inference tier that merely forwarded a result onward.

Till and face are folded into this flow rather than added as separate round
trips. Till has no dependency on people at all, so the inference lane runs it
alongside pose on the same frame and forwards the result in the same message
-- no extra crossing. Face is different: it depends on which track a
detection joined, which is only known after tracking, so it cannot run in
the inference tier. It also cannot be a request the business tier waits on,
by the same argument that killed the proxy shape. So a face request carries a
small cropped patch (order 60KB, not the frame) rather than a slot index, and
the answer arrives on its own queue whenever it arrives -- which is exactly
what EntryFaceWindow.consider() already expects, since it was built to
tolerate a decision landing after the fact.
"""
from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any

import numpy as np

from backend.src.runtime.frame_ring import FrameRing, RingSpec

# Sentinel put on a queue to end its consumer's loop. Distinguished from a
# real message by identity, not by shape, since a real message may itself be
# malformed.
STOP = object()


@dataclass
class DecodeMessage:
    """What a decoder hands to its inference lane. Small: the pixels stay in the ring.

    Carries the pose blob and its letterbox metadata, computed by the
    decoder, not the pixels themselves for pose. That is what keeps
    preprocessing CPU work out of the CUDA-holding process: a decoder that
    only forwarded a slot index would push its own letterbox work back onto
    the inference tier, exactly the coupling this split exists to remove.
    Till and cropping still need the raw frame, so that stays in the ring
    under the same slot.
    """

    camera_id: str
    slot: int
    frame_shape: tuple[int, int, int]     # raw frame shape, for cropping/till
    frame_idx: int
    ratio: float
    dx: int
    dy: int
    letterbox_s: float
    preprocess_s: float
    conf: float
    till_conf: float | None       # None -> skip till this frame


@dataclass
class PerceptionResult:
    """What an inference lane hands to the business tier for one frame.

    The frame's slot is still held here: business needs the pixels for
    cropping (clip buffer, face patches) and is the one that finally releases
    it. Carrying boxes/kpts/feats as plain arrays rather than PoseResult
    objects keeps this cheap to pickle across the process boundary -- it
    still goes through a real multiprocessing.Queue, just once, not twice.
    """

    camera_id: str
    slot: int
    frame_shape: tuple[int, int, int]
    frame_idx: int
    boxes: np.ndarray             # (N, 5): x1 y1 x2 y2 conf
    kpts: list
    feats: np.ndarray
    till_detections: list | None
    pose_profile: dict
    reid_profile: dict


@dataclass
class FaceRequest:
    """A cropped patch, not a frame reference.

    Fire-and-forget only works if the thing being forwarded outlives the slot
    it was cut from. A slot index would not: by the time an async answer
    comes back the slot may already be several frames further along. So this
    carries the pixels it needs, at person-crop size (tens of KB), not a
    pointer into shared memory.
    """

    camera_id: str
    track_id: int
    patch_bgr: np.ndarray
    person_box_xyxy: np.ndarray
    store_id: str | None
    company_id: str | None


@dataclass
class FaceAnswer:
    camera_id: str
    track_id: int
    decision: Any                 # MatchDecision, kept opaque here to avoid an import cycle


@dataclass
class LaneStats:
    lane: int
    frames: int
    busy_s: float
    pose_s: float
    reid_s: float
    till_s: float


class InferenceLane:
    """One thread inside the inference process: one queue in, business queues out.

    Threaded for the reason measured in the spike: a single-threaded
    inference tier runs its own CPU work -- decoding pose output, cropping
    for ReID -- in series behind its GPU calls, so the GPU idles through all
    of it (99.94 FPS against the then-current 118.35). Two threads let one
    thread's CPU overlap another's GPU call (138.81 against 106.87). Four was
    worse than two, +13.8% against +28.2%: beyond two they contend for the
    same GIL inside one process and hand back what they gained.
    """

    def __init__(self, index: int, decode_q, registry, pose_model: str, reid_model: str,
                 till=None, business_qs: dict | None = None,
                 rings: dict | None = None, cpu_only: bool = False):
        self.index = index
        self.decode_q = decode_q
        self.business_qs = business_qs or {}
        self.rings = rings or {}
        self.till = till
        self.stats = LaneStats(index, 0, 0.0, 0.0, 0.0, 0.0)

        self.pose = registry.get_pose(pose_model)
        self.reid = registry.get_reid_embedder(
            reid_model, build=lambda: self._build_reid(reid_model, cpu_only))
        # The decoder wrote blobs assuming a dtype; if the model actually
        # wants a different one, every inference silently reads garbage
        # rather than raising, since the bytes are still a valid array of
        # the wrong type. Failing at startup beats debugging that.
        self._expected_blob_dtype = self.pose.input_dtype

    @staticmethod
    def _build_reid(reid_model: str, cpu_only: bool):
        import stream_track as S

        S.REID_MODEL = reid_model
        S.ORT_PROVIDERS = ["CPUExecutionProvider"] if cpu_only else None
        return S.Embedder()

    def run(self) -> None:
        while True:
            msg = self.decode_q.get()
            if msg is STOP or msg is None:
                return
            started = time.perf_counter()
            try:
                self._process(msg)
            except BaseException:
                # A frame that fails to perceive must not vanish silently --
                # forward an empty result so the business tier still releases
                # the slot and the camera's frame count stays accurate.
                traceback.print_exc()
                self.business_qs[msg.camera_id].put(PerceptionResult(
                    camera_id=msg.camera_id, slot=msg.slot, frame_shape=msg.frame_shape,
                    frame_idx=msg.frame_idx, boxes=np.empty((0, 5), np.float32), kpts=[],
                    feats=np.empty((0, 512), np.float32), till_detections=None,
                    pose_profile={}, reid_profile={}))
            finally:
                self.stats.busy_s += time.perf_counter() - started
                self.stats.frames += 1

    def _process(self, msg: DecodeMessage) -> None:
        ring = self.rings[msg.camera_id]
        frame = ring.frame_slot(msg.slot, msg.frame_shape)
        blob = ring.blob_slot(msg.slot)
        if blob.dtype != self._expected_blob_dtype:
            raise TypeError(
                f"decoder wrote {blob.dtype} blobs but {self.pose.model_path} wants "
                f"{self._expected_blob_dtype}; every inference so far read the wrong "
                f"bytes as the wrong type")

        t0 = time.perf_counter()
        # The box-decode math wants exactly (h, w); msg.frame_shape carries
        # the full (h, w, c) because that is what ring.frame_slot() and
        # cropping need, so it is sliced here rather than at every call site.
        pose_result = self.pose.detect_preprocessed(
            blob, conf=msg.conf, frame_shape=msg.frame_shape[:2], ratio=msg.ratio,
            dx=msg.dx, dy=msg.dy, letterbox_s=msg.letterbox_s, preprocess_s=msg.preprocess_s)
        self.stats.pose_s += time.perf_counter() - t0

        t0 = time.perf_counter()
        feats = self.reid(frame, pose_result.boxes)
        self.stats.reid_s += time.perf_counter() - t0

        till_detections = None
        if self.till is not None and msg.till_conf is not None:
            t0 = time.perf_counter()
            till_detections = self.till.detect(frame, conf=msg.till_conf).detections
            self.stats.till_s += time.perf_counter() - t0

        self.business_qs[msg.camera_id].put(PerceptionResult(
            camera_id=msg.camera_id, slot=msg.slot, frame_shape=msg.frame_shape,
            frame_idx=msg.frame_idx, boxes=pose_result.boxes, kpts=pose_result.kpts,
            feats=feats, till_detections=till_detections,
            pose_profile=pose_result.profile, reid_profile=dict(self.reid.last_profile),
        ))


@dataclass
class InferenceConfig:
    pose_model: str
    reid_model: str
    till_model: str | None = None
    till_imgsz: int = 640
    cpu_only: bool = False
    ort_intra_threads: int = 0
    ort_allow_spinning: bool = True


def run_inference_process(config: InferenceConfig, lane_decode_qs: list,
                          business_qs: dict, ring_specs: dict[str, RingSpec],
                          ready_q, go_evt, stats_q) -> None:
    """Entry point for one inference process: some number of lanes, one CUDA context.

    Loads everything and reports ready before touching a frame, so model
    loading never lands inside a measured window -- the same discipline the
    barrier-based preload already used, now crossing a process boundary
    instead of just threads.
    """
    from backend.src.runtime.perf import cap_native_threads
    from backend.src.runtime.shared_inference import SharedInferenceRegistry

    cap_native_threads(1)
    registry = SharedInferenceRegistry(
        cpu_only=config.cpu_only,
        config={"pose_num_workers": 1, "pose_max_batch": 1, "pose_batch_wait_s": 0.005,
                "pose_pad_batch": True, "ort_intra_threads": config.ort_intra_threads,
                "ort_allow_spinning": config.ort_allow_spinning},
    )
    rings = {cam_id: FrameRing(spec) for cam_id, spec in ring_specs.items()}
    till = (registry.get_till(config.till_model, imgsz=config.till_imgsz)
            if config.till_model else None)

    lanes = [
        InferenceLane(i, decode_q, registry, config.pose_model, config.reid_model,
                      till=till, business_qs=business_qs, rings=rings, cpu_only=config.cpu_only)
        for i, decode_q in enumerate(lane_decode_qs)
    ]

    ready_q.put(("inference", len(lanes)))
    go_evt.wait()

    threads = [threading.Thread(target=lane.run, name=f"lane-{i}") for i, lane in enumerate(lanes)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stats_q.put([lane.stats for lane in lanes])
    for ring in rings.values():
        ring.close()
    registry.close()
