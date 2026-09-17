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

import logging
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any

import numpy as np

from backend.src.common.stream_uri import redact_stream_credentials
from backend.src.domain.models import FrameMeta
from backend.src.runtime.frame_ring import FrameRing, RingSpec

logger = logging.getLogger(__name__)

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
    frame_meta: FrameMeta
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
    frame_meta: FrameMeta
    boxes: np.ndarray             # (N, 5): x1 y1 x2 y2 conf
    kpts: list
    feats: np.ndarray
    till_detections: list | None
    pose_profile: dict
    reid_profile: dict
    till_profile: dict


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


# How generously to over-crop a face patch before shipping it, as a fraction
# of the person box's own width/height on each side. SharedFaceIdentifier
# internally re-crops around the box by its own configured crop_expand
# (typically ~0.12) to find the face; as long as that lands inside what was
# already shipped, the answer is bit-identical to running on the full frame.
# 1.0 (100% padding) comfortably covers any crop_expand a real deployment
# would use, without needing the face tier's config threaded back to
# wherever the patch is cut.
FACE_PATCH_PAD_FRAC = 1.0


def crop_face_patch(frame_bgr: np.ndarray, box_xyxy, pad_frac: float = FACE_PATCH_PAD_FRAC):
    """A person crop generous enough that face detection on it matches the full frame.

    Returns (patch, box_in_patch): the patch to ship, and the same person
    box translated into the patch's own coordinates, ready to pass straight
    to SharedFaceIdentifier.identify() unchanged.
    """
    x1, y1, x2, y2 = (float(v) for v in box_xyxy)
    bw, bh = x2 - x1, y2 - y1
    px, py = bw * pad_frac, bh * pad_frac
    h, w = frame_bgr.shape[:2]
    cx1 = max(0, int(x1 - px))
    cy1 = max(0, int(y1 - py))
    cx2 = min(w, int(x2 + px) + 1)
    cy2 = min(h, int(y2 + py) + 1)
    # .copy(), not ascontiguousarray(): a full-width crop near the frame's
    # left/right edge can already be C-contiguous, and ascontiguousarray
    # returns the input unchanged whenever it is -- silently shipping a view
    # into the ring slot instead of a copy. multiprocessing.Queue.put()
    # pickles on a background feeder thread, not synchronously, so the
    # caller releasing the slot right after put() can race a decoder
    # rewriting it before the patch is actually serialised. A real copy has
    # no such window.
    patch = frame_bgr[cy1:cy2, cx1:cx2].copy()
    box_in_patch = np.array([x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1], np.float32)
    return patch, box_in_patch


class FaceLane:
    """One thread: reads FaceRequest patches, answers with FaceAnswer.

    Runs in its own process (see run_face_process), not folded into a pose
    lane, so its shutdown does not get tangled with the pose lanes' -- they
    stop as soon as decode finishes, while this has to keep answering until
    every business worker is done sending requests, which is well after
    decode ends. Keeping it a separate process makes that a simple "stop
    last" instead of a lifecycle two lane types would otherwise have to
    share.

    One SharedFaceIdentifier per camera, matching what the current
    architecture already does per-camera (each opens its own FaceGallery
    connection); this lane just means those live in one process instead of
    one per camera.
    """

    def __init__(self, face_q, identifiers: dict, business_qs: dict):
        self.face_q = face_q
        self.identifiers = identifiers
        self.business_qs = business_qs
        self.served = 0
        self.busy_s = 0.0

    def run(self) -> None:
        while True:
            req = self.face_q.get()
            if req is None:
                return
            # Every request gets an answer, including a failed one. The
            # business worker counts its outstanding requests and will not
            # finish until that count reaches zero, so a request dropped here
            # -- an unknown camera, or an exception inside identify() --
            # leaves that worker blocked on its queue forever, and the parent
            # blocked collecting its stats. The whole run then hangs with
            # nothing in the log but a stack trace nobody is waiting on.
            # decision=None means "no identification happened"; the pipeline
            # side ignores those exactly as it already ignores a window that
            # closed before its answer came back.
            decision = None
            identifier = self.identifiers.get(req.camera_id)
            if identifier is not None:
                started = time.perf_counter()
                try:
                    decision = identifier.identify(
                        req.patch_bgr, req.person_box_xyxy,
                        store_id=req.store_id, company_id=req.company_id)
                except BaseException:
                    traceback.print_exc()
                finally:
                    self.busy_s += time.perf_counter() - started
                    self.served += 1
            else:
                logger.warning("face lane: no identifier for camera %s; answering empty "
                               "so the business worker can finish", req.camera_id)
            # Looked up, not indexed. A camera with no identifier is by
            # definition one this lane does not know, so indexing
            # business_qs with that same id raises KeyError and kills the
            # lane -- taking down the very tier every waiting business
            # worker depends on. Handling the unknown camera above and then
            # indexing with it here was a contradiction in the same block.
            business_q = self.business_qs.get(req.camera_id)
            if business_q is None:
                logger.error("face lane: no business queue for camera %s; its answer cannot "
                             "be delivered and a worker may be waiting on it", req.camera_id)
                continue
            business_q.put(
                FaceAnswer(camera_id=req.camera_id, track_id=req.track_id, decision=decision))


@dataclass
class FaceCameraConfig:
    face_conf: float = 0.20
    crop_expand: float = 0.12
    iou_thres: float = 0.45
    match_threshold: float = 0.30
    match_margin: float = 0.10
    #: Gallery role to match against: True staff, False watchlist, None both.
    match_is_staff: bool | None = None


def run_face_process(face_model: str, arcface_model: str, gallery_db_path: str,
                     camera_configs: dict[str, FaceCameraConfig], face_q, business_qs: dict,
                     ready_q, go_evt, stats_q, cpu_only: bool = False) -> None:
    """Entry point for the face-identity process: its own CUDA context.

    A real, small third context alongside the pose/ReID processes'. Worth
    it for the shutdown-sequencing simplicity a separate process buys
    (see FaceLane), and face traffic is light -- entries only, not every
    frame -- so its marginal cost is far below another full pose-serving
    context.
    """
    from backend.src.runtime.perf import cap_native_threads
    from backend.src.runtime.shared_inference import SharedFaceIdentifier, SharedInferenceRegistry
    from backend.src.runtime.tier_status import tier_guard

    with tier_guard("face", ready_q, stats_q):
        _run_face_process(face_model, arcface_model, gallery_db_path, camera_configs,
                          face_q, business_qs, ready_q, go_evt, stats_q, cpu_only,
                          cap_native_threads, SharedFaceIdentifier, SharedInferenceRegistry)


def _run_face_process(face_model, arcface_model, gallery_db_path, camera_configs,
                      face_q, business_qs, ready_q, go_evt, stats_q, cpu_only,
                      cap_native_threads, SharedFaceIdentifier, SharedInferenceRegistry) -> None:
    cap_native_threads(1)
    registry = SharedInferenceRegistry(cpu_only=cpu_only, config={})
    identifiers = {}
    for camera_id, cfg in camera_configs.items():
        identifiers[camera_id] = SharedFaceIdentifier(
            face_detector=registry.get_face_detector(face_model),
            arcface=registry.get_arcface(arcface_model),
            gallery_db_path=gallery_db_path,
            face_conf=cfg.face_conf, crop_expand=cfg.crop_expand, iou=cfg.iou_thres,
            match_threshold=cfg.match_threshold, match_margin=cfg.match_margin,
            match_is_staff=cfg.match_is_staff,
        )
        # Same silent-failure class this already caught once: an
        # identity_gallery_scope mismatch yields an empty gallery with no
        # error, and every match then reports gallery_empty forever.
        summary = identifiers[camera_id].gallery.summarize(
            store_id=None, company_id=None,  # store_id/company_id filtered per-call, not here
            is_staff=cfg.match_is_staff)
        if summary["embeddings"] == 0:
            # Redacted: under cloud-db this is a DSN with the password in it.
            logger.warning("face process: gallery has no embeddings at all (%s); every "
                           "identification will report gallery_empty",
                           redact_stream_credentials(gallery_db_path))

    # Warm the shared detector and ArcFace once, for the same reason the pose
    # lanes are warmed: the first CUDA call is the slow one. The gallery roster
    # was already loaded by summarize() above.
    if identifiers:
        from backend.src.runtime.shared_inference import FaceDetectRequest

        started = time.perf_counter()
        first = next(iter(identifiers.values()))
        patch = np.zeros((600, 400, 3), np.uint8)
        for _ in range(3):
            first.face_detector.detect_face(FaceDetectRequest(
                frame_bgr=patch, person_box_xyxy=np.array([50, 50, 350, 550], np.float32),
                face_conf=0.99, iou=0.45, crop_expand=0.12, min_face_score=0.99))
            first.arcface.embed(np.zeros((112, 112, 3), np.uint8))
        logger.info("face process: detector and ArcFace warmed up in %.1fs",
                    time.perf_counter() - started)

    try:
        ready_q.put(("face", 1))
        go_evt.wait()

        lane = FaceLane(face_q, identifiers, business_qs)
        lane.run()

        stats_q.put({"served": lane.served, "busy_s": lane.busy_s})
    finally:
        for identifier in identifiers.values():
            identifier.close()
        registry.close()


@dataclass
class LaneStats:
    lane: int
    frames: int
    busy_s: float
    pose_s: float
    reid_s: float
    till_s: float
    # How much of pose_s was spent queued rather than computing.
    #
    # pose runs on a shared batch runner with one worker, so every lane in
    # this process submits into the same queue: with 8 lanes, 7 of them are
    # waiting whenever one is running. Without this split, that waiting is
    # indistinguishable from model cost, and pose reads as ~80% of inference
    # time -- which points optimisation at the model when the cost is
    # actually serialisation in front of it.
    pose_queue_wait_s: float = 0.0


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
                 rings: dict | None = None, cpu_only: bool = False,
                 reid_shared: bool = False):
        self.index = index
        self.decode_q = decode_q
        self.business_qs = business_qs or {}
        self.rings = rings or {}
        self.till = till
        self.stats = LaneStats(index, 0, 0.0, 0.0, 0.0, 0.0)

        self.pose = registry.get_pose(pose_model)
        # reid_shared routes every lane's call through one worker thread
        # (SharedReidService) instead of each lane calling sess.run whenever
        # its own frame is ready -- a measurement, not the default, see
        # SharedReidService's docstring for what it isolates.
        self.reid = (registry.get_reid_shared(reid_model) if reid_shared else
                    registry.get_reid_embedder(
                        reid_model, build=lambda: self._build_reid(reid_model, cpu_only)))
        # The decoder wrote blobs assuming a dtype; if the model actually
        # wants a different one, every inference silently reads garbage
        # rather than raising, since the bytes are still a valid array of
        # the wrong type. Checked here, against the ring the decoder writes
        # into, rather than per frame inside _process(): run()'s own
        # except-BaseException would catch a per-frame raise and forward an
        # empty PerceptionResult instead, so the run would complete, report a
        # frame count, and find nothing at all on any camera. A fp16 pose
        # model is not hypothetical here -- models_fp16/ exists.
        self._expected_blob_dtype = self.pose.input_dtype
        for camera_id, ring in self.rings.items():
            if ring.spec.blob_shape is None:
                continue
            got = np.dtype(ring.spec.blob_dtype)
            if got != self._expected_blob_dtype:
                raise TypeError(
                    f"lane {index}: {camera_id}'s decoder writes {got} blobs but "
                    f"{pose_model} expects {np.dtype(self._expected_blob_dtype)}; every "
                    f"inference would read the wrong bytes as the wrong type and return "
                    f"nothing, without ever raising")

    def warm_up(self, rounds: int = 3) -> float:
        """Run every model this lane uses on synthetic input before the clock starts.

        Loading a session is not the same as it being ready: the first CUDA
        run allocates the arena and picks kernels, and measured on the box that
        first frame per camera came back ~0.8-1.1 s late while the steady state
        was ~50 ms. Paying it here keeps it out of the first real frames.
        Returns the seconds spent.
        """
        started = time.perf_counter()
        blob_shape = next((r.spec.blob_shape for r in self.rings.values() if r.spec.blob_shape),
                          (3, 640, 640))
        blob = np.zeros(blob_shape, dtype=self._expected_blob_dtype)
        frame = np.zeros((1080, 1920, 3), np.uint8)
        boxes = np.array([[100, 100, 300, 600, 0.9], [900, 200, 1100, 700, 0.9]], np.float32)
        for _ in range(rounds):
            self.pose.detect_preprocessed(blob, conf=0.99, frame_shape=(1080, 1920),
                                          ratio=640 / 1920, dx=0, dy=140)
            self.reid(frame, boxes)
            if self.till is not None:
                self.till.detect(frame, conf=0.99)
        return time.perf_counter() - started

    @staticmethod
    def _build_reid(reid_model: str, cpu_only: bool):
        from backend.src.perception import stream_track as S

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
                    frame_idx=msg.frame_idx, frame_meta=msg.frame_meta,
                    boxes=np.empty((0, 5), np.float32), kpts=[],
                    feats=np.empty((0, 512), np.float32), till_detections=None,
                    pose_profile={}, reid_profile={}, till_profile={}))
            finally:
                self.stats.busy_s += time.perf_counter() - started
                self.stats.frames += 1

    def _process(self, msg: DecodeMessage) -> None:
        ring = self.rings[msg.camera_id]
        frame = ring.frame_slot(msg.slot, msg.frame_shape)
        blob = ring.blob_slot(msg.slot)  # dtype already verified in __init__

        t0 = time.perf_counter()
        # The box-decode math wants exactly (h, w); msg.frame_shape carries
        # the full (h, w, c) because that is what ring.frame_slot() and
        # cropping need, so it is sliced here rather than at every call site.
        pose_result = self.pose.detect_preprocessed(
            blob, conf=msg.conf, frame_shape=msg.frame_shape[:2], ratio=msg.ratio,
            dx=msg.dx, dy=msg.dy, letterbox_s=msg.letterbox_s, preprocess_s=msg.preprocess_s)
        self.stats.pose_s += time.perf_counter() - t0
        self.stats.pose_queue_wait_s += float(pose_result.profile.get("queue_wait_s", 0.0))

        t0 = time.perf_counter()
        feats = self.reid(frame, pose_result.boxes)
        self.stats.reid_s += time.perf_counter() - t0

        till_detections = None
        till_profile: dict = {}
        if self.till is not None and msg.till_conf is not None:
            t0 = time.perf_counter()
            till_result = self.till.detect(frame, conf=msg.till_conf)
            till_detections = till_result.detections
            till_profile = till_result.profile
            self.stats.till_s += time.perf_counter() - t0

        self.business_qs[msg.camera_id].put(PerceptionResult(
            camera_id=msg.camera_id, slot=msg.slot, frame_shape=msg.frame_shape,
            frame_idx=msg.frame_idx, frame_meta=msg.frame_meta,
            boxes=pose_result.boxes, kpts=pose_result.kpts,
            feats=feats, till_detections=till_detections,
            pose_profile=pose_result.profile, reid_profile=dict(self.reid.last_profile),
            till_profile=till_profile,
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
    # Pose runner shape. Exposed because measurement showed it is the single
    # largest cost in the system: with one worker serving every lane in a
    # process, 81% of a lane's pose time is spent queued behind other lanes,
    # which is 64% of all inference time. Whether widening it helps is a
    # measurement -- pose_pad_batch keeps the input shape constant, so unlike
    # till a batch here does not force the CUDA EP to re-plan.
    pose_num_workers: int = 1
    pose_max_batch: int = 1
    # See SharedReidService's docstring. Default False: unmeasured until this
    # flag exists to compare against, so it does not become the shape a run
    # silently gets.
    reid_shared: bool = False
    reid_num_workers: int = 1


def run_inference_process(config: InferenceConfig, lane_decode_qs: list,
                          business_qs: dict, ring_specs: dict[str, RingSpec],
                          ready_q, go_evt, stats_q, needs_till: bool = True) -> None:
    """Entry point for one inference process: some number of lanes, one CUDA context.

    Loads everything and reports ready before touching a frame, so model
    loading never lands inside a measured window -- the same discipline the
    barrier-based preload already used, now crossing a process boundary
    instead of just threads.

    The whole body runs inside tier_guard: model load, the ring mappings and
    the lanes' own dtype check all happen before ready_q.put, which is
    precisely where the parent is blocked. Without the guard, a mismatched
    model here is not an error, it is a run that never starts and never says
    why.
    """
    from backend.src.runtime.perf import cap_native_threads
    from backend.src.runtime.shared_inference import SharedInferenceRegistry
    from backend.src.runtime.tier_status import tier_guard

    with tier_guard("inference", ready_q, stats_q):
        _run_inference_process(config, lane_decode_qs, business_qs, ring_specs,
                               ready_q, go_evt, stats_q, needs_till,
                               cap_native_threads, SharedInferenceRegistry)


def _run_inference_process(config, lane_decode_qs, business_qs, ring_specs,
                           ready_q, go_evt, stats_q, needs_till,
                           cap_native_threads, SharedInferenceRegistry) -> None:
    cap_native_threads(1)
    registry = SharedInferenceRegistry(
        cpu_only=config.cpu_only,
        config={"pose_num_workers": config.pose_num_workers,
                "pose_max_batch": config.pose_max_batch,
                "pose_batch_wait_s": 0.005,
                "pose_pad_batch": True,
                # Till, unlike pose, has no pad_batch: a batch of 2 really is
                # a different input shape than a batch of 1, and the CUDA EP
                # re-plans on every shape change. With lanes submitting
                # concurrently the batch size flaps between 1 and lane count,
                # so it re-plans constantly -- measured 43.28 calls/s at
                # max_batch=8 against 70.79 at max_batch=1. Pinning it to one
                # keeps the shape constant, which is worth more here than
                # batching would have been.
                "till_num_workers": 1, "till_max_batch": 1, "till_batch_wait_s": 0.005,
                "reid_num_workers": config.reid_num_workers,
                "ort_intra_threads": config.ort_intra_threads,
                "ort_allow_spinning": config.ort_allow_spinning},
    )
    rings = {cam_id: FrameRing(spec) for cam_id, spec in ring_specs.items()}
    # Only load till if a camera in this run actually samples a drawer.
    # Loading it regardless costs startup time and a GPU-resident model on
    # every storage-only run, for a branch no frame will ever take.
    till = (registry.get_till(config.till_model, imgsz=config.till_imgsz)
            if (config.till_model and needs_till) else None)

    try:
        lanes = [
            InferenceLane(i, decode_q, registry, config.pose_model, config.reid_model,
                          till=till, business_qs=business_qs, rings=rings,
                          cpu_only=config.cpu_only, reid_shared=config.reid_shared)
            for i, decode_q in enumerate(lane_decode_qs)
        ]
        warm_s = sum(lane.warm_up() for lane in lanes)
        logger.info("inference: %d lanes warmed up in %.1fs", len(lanes), warm_s)

        ready_q.put(("inference", len(lanes)))
        go_evt.wait()

        threads = [threading.Thread(target=lane.run, name=f"lane-{i}")
                   for i, lane in enumerate(lanes)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats_q.put([lane.stats for lane in lanes])
    finally:
        for ring in rings.values():
            ring.close()
        registry.close()
