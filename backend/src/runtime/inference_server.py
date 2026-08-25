"""Run the GPU models in their own process, behind the API they already have.

The point of the split is that CPU work should be able to scale without
adding CUDA contexts, because pose latency scales with how many of those
exist: 19ms at two contexts, 45ms at four, 153ms at eight on one L40S. That
means the models have to live in a small fixed number of processes while
everything else spreads out.

This is the transport change that makes it possible, and it is deliberately
only a transport change. SharedPoseService and SharedTillService already
express their work as a request handed to a queue and a result awaited, so
the pipelines calling `det.detect(frame, conf=...)` need not know whether the
answer came from a thread in this process or a thread in another one. The
proxies below keep those signatures exactly.

Frames do not travel on the queue. They are written into a FrameRing and only
the slot index is sent, because pickling a 1080p array costs 35.6ms and 399%
of a core at the frame rate this system runs at, against 0.85ms for a slot
index. See backend/src/runtime/frame_ring.py.

Two rules the server obeys that are easy to lose in a refactor:

  * Each lane owns a private Embedder. Embedder.__call__ writes into a shared
    self._pad_batch, so lanes sharing one instance corrupt each other's
    features. That bug has already been made once here; it showed up as two
    architectures disagreeing on how many tracks they created.

  * A camera is pinned to one lane. A queue with a single consumer preserves
    the order things were written in, and that ordering is load-bearing:
    till_confirm needs consecutive samples, so a frame arriving out of order
    silently breaks the debounce. Pinning is what lets us skip the re-sorting
    stage the reference implementation needs.
"""
from __future__ import annotations

import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from backend.src.runtime.frame_ring import FrameRing, RingSpec

# Ops are plain constants rather than an Enum so the request tuples stay
# cheap to pickle; they cross a process boundary on every single call.
OP_POSE = "pose"
OP_REID = "reid"
OP_TILL = "till"
OP_FACE = "face"
# Does no work at all. Exists so the transport can be priced on its own,
# separately from whatever model sits behind it.
OP_PING = "ping"
OP_SHUTDOWN = "shutdown"


@dataclass
class ClientChannel:
    """One camera's private path to the server.

    Built by the parent before anything starts, because multiprocessing
    queues can be handed to a child at spawn but cannot themselves be sent
    through a queue. Everything a camera needs to talk to the server is
    therefore decided up front, which suits the preload discipline anyway.
    """

    camera_id: str
    ring_spec: RingSpec
    reply_q: Any
    lane: int


@dataclass
class ServerConfig:
    pose_model: str
    reid_model: str
    till_model: str | None = None
    till_imgsz: int = 640
    face_model: str | None = None
    arcface_model: str | None = None
    gallery_db_path: str | None = None
    face_params: dict = field(default_factory=dict)
    cpu_only: bool = False
    # 0 leaves onnxruntime's default, which is one thread per core per session.
    ort_intra_threads: int = 0
    # Diagnostics only: stand the transport up with nothing behind it, to tell
    # the cost of the boundary apart from the cost of the process being busy.
    load_models: bool = True
    ort_allow_spinning: bool = True


class _Lane:
    """One server thread: its own queue, its own embedder, shared sessions.

    Threads matter here more than they look. A single-threaded inference tier
    runs its own CPU work -- decoding pose output, cropping for ReID -- in
    series behind its GPU calls, so the GPU idles through all of it; measured,
    that lost outright to the architecture it was meant to replace, 99.94 FPS
    against 118.35. With two lanes one thread's CPU overlaps another's GPU
    call. Four was worse than two: past that they contend for the same GIL.
    """

    def __init__(self, index: int, request_q, registry, config: ServerConfig,
                 rings: dict, reply_qs: dict):
        self.index = index
        self.request_q = request_q
        self.registry = registry
        self.config = config
        self.rings = rings
        # Reply queues are looked up by camera, never carried in the request.
        # A multiprocessing queue can be handed to a child at spawn but cannot
        # be sent through another queue, so routing has to be by name.
        self.reply_qs = reply_qs
        self.served = 0
        self.busy_s = 0.0

        if config.load_models:
            self.pose = registry.get_pose(config.pose_model)
            self.reid = registry.get_reid_embedder(config.reid_model, build=self._build_reid)
            self.till = (
                registry.get_till(config.till_model, imgsz=config.till_imgsz)
                if config.till_model else None
            )
            self.face = self._build_face()
        else:
            self.pose = self.reid = self.till = self.face = None

    def _build_reid(self):
        import stream_track as S

        S.REID_MODEL = self.config.reid_model
        S.ORT_PROVIDERS = ["CPUExecutionProvider"] if self.config.cpu_only else None
        return S.Embedder()

    def _build_face(self):
        if not (self.config.face_model and self.config.arcface_model
                and self.config.gallery_db_path):
            return None
        from backend.src.runtime.shared_inference import SharedFaceIdentifier

        params = self.config.face_params
        return SharedFaceIdentifier(
            face_detector=self.registry.get_face_detector(self.config.face_model),
            arcface=self.registry.get_arcface(self.config.arcface_model),
            gallery_db_path=self.config.gallery_db_path,
            face_conf=float(params.get("face_conf", 0.20)),
            crop_expand=float(params.get("crop_expand", 0.12)),
            iou=float(params.get("iou_thres", 0.45)),
            match_threshold=float(params.get("match_threshold", 0.30)),
            match_margin=float(params.get("match_margin", 0.10)),
        )

    def run(self) -> None:
        while True:
            item = self.request_q.get()
            if item is None:
                return
            op, camera_id, slot, shape, args = item
            if op == OP_SHUTDOWN:
                return
            reply_q = self.reply_qs[camera_id]
            started = time.perf_counter()
            try:
                reply_q.put((True, self._dispatch(op, camera_id, slot, shape, args)))
            except BaseException:
                # A failure here must reach the caller, which is blocked on
                # this reply. Dying quietly would hang the whole camera.
                reply_q.put((False, traceback.format_exc()))
            finally:
                self.busy_s += time.perf_counter() - started
                self.served += 1

    def _frame(self, camera_id: str, slot: int, shape) -> np.ndarray:
        return self.rings[camera_id].frame_slot(slot, shape)

    def _dispatch(self, op, camera_id, slot, shape, args):
        if op == OP_PING:
            # Touch the frame so the cost of reaching into shared memory is
            # included, but do nothing with it.
            return int(self._frame(camera_id, slot, shape)[0, 0, 0])
        if op == OP_POSE:
            frame = self._frame(camera_id, slot, shape)
            result = self.pose.detect(frame, conf=args["conf"])
            return result.boxes, result.kpts, result.profile
        if op == OP_REID:
            frame = self._frame(camera_id, slot, shape)
            feats = self.reid(frame, args["boxes"])
            return feats, dict(self.reid.last_profile)
        if op == OP_TILL:
            frame = self._frame(camera_id, slot, shape)
            result = self.till.detect(frame, conf=args["conf"])
            return result.detections, result.profile
        if op == OP_FACE:
            frame = self._frame(camera_id, slot, shape)
            return self.face.identify(
                frame, args["person_box"],
                store_id=args["store_id"], company_id=args["company_id"])
        raise ValueError(f"unknown op {op!r}")


def serve(config: ServerConfig, channels: list[ClientChannel], request_qs: list,
          ready_q, go_evt, stats_q) -> None:
    """Entry point for the inference process.

    Loads every model, reports ready, and does not touch a frame until every
    other process has reported ready too. Model loading has to sit outside
    the measured window or the slowest loader sets the rate for everyone.
    """
    from backend.src.runtime.perf import cap_native_threads
    from backend.src.runtime.shared_inference import SharedInferenceRegistry

    cap_native_threads(1)
    registry = SharedInferenceRegistry(
        cpu_only=config.cpu_only,
        config={"pose_num_workers": 1, "pose_max_batch": 1,
                "pose_batch_wait_s": 0.005, "pose_pad_batch": True,
                "ort_intra_threads": config.ort_intra_threads,
                "ort_allow_spinning": config.ort_allow_spinning},
    )
    rings = {ch.camera_id: FrameRing(ch.ring_spec) for ch in channels}
    reply_qs = {ch.camera_id: ch.reply_q for ch in channels}
    lanes = [_Lane(i, q, registry, config, rings, reply_qs)
             for i, q in enumerate(request_qs)]

    ready_q.put(("inference", len(lanes)))
    go_evt.wait()

    threads = [threading.Thread(target=lane.run, name=f"lane-{i}", daemon=False)
               for i, lane in enumerate(lanes)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stats_q.put({
        "served": sum(l.served for l in lanes),
        "busy_s": sum(l.busy_s for l in lanes),
        "per_lane": [(l.index, l.served, round(l.busy_s, 2)) for l in lanes],
    })
    for ring in rings.values():
        ring.close()
    registry.close()


# --------------------------------------------------------------------------
# client side: the same API the pipelines already call
# --------------------------------------------------------------------------

class _Caller:
    """Shared plumbing: put the frame in a slot, send the index, wait.

    Blocking is intentional. The caller is one camera's thread among many, so
    while it waits the others keep working, which is exactly how the existing
    in-process queue behaves. What must not happen is the frame travelling
    with the request.
    """

    def __init__(self, channel: ClientChannel, ring: FrameRing, free_q, request_q):
        self.channel = channel
        self.ring = ring
        self.free_q = free_q
        self.request_q = request_q
        self.last_wait_s = 0.0

    def call(self, op: str, frame_bgr: np.ndarray, args: dict):
        slot = self.free_q.get()
        try:
            shape = frame_bgr.shape
            dst = self.ring.frame_slot(slot, shape)
            np.copyto(dst, frame_bgr)
            started = time.perf_counter()
            self.request_q.put((op, self.channel.camera_id, slot, shape, args))
            ok, payload = self.channel.reply_q.get()
            self.last_wait_s = time.perf_counter() - started
        finally:
            # Released only after the reply, never before: the server is
            # reading this slot until it answers.
            self.free_q.put(slot)
        if not ok:
            raise RuntimeError(f"inference server failed on {op}:\n{payload}")
        return payload


class PoseProxy:
    def __init__(self, caller: _Caller):
        self._caller = caller
        self.provider = "remote"

    def detect(self, frame_bgr: np.ndarray, *, conf: float):
        from backend.src.runtime.shared_inference import PoseResult

        boxes, kpts, profile = self._caller.call(OP_POSE, frame_bgr, {"conf": float(conf)})
        profile = dict(profile)
        profile["transport_s"] = self._caller.last_wait_s - float(profile.get("total_s", 0.0))
        return PoseResult(boxes=boxes, kpts=kpts, profile=profile)


class ReidProxy:
    def __init__(self, caller: _Caller):
        self._caller = caller
        self.provider = "remote"
        self.last_profile: dict = {}

    def __call__(self, frame_bgr: np.ndarray, boxes):
        if not len(boxes):
            self.last_profile = {"provider": self.provider, "infer_s": 0.0, "total_s": 0.0}
            return np.empty((0, 512), np.float32)
        feats, profile = self._caller.call(OP_REID, frame_bgr, {"boxes": boxes})
        self.last_profile = profile
        return feats


class TillProxy:
    def __init__(self, caller: _Caller):
        self._caller = caller
        self.provider = "remote"

    def detect(self, frame_bgr: np.ndarray, *, conf: float):
        from backend.src.runtime.shared_inference import TillResult

        detections, profile = self._caller.call(OP_TILL, frame_bgr, {"conf": float(conf)})
        return TillResult(detections=detections, profile=profile)


class FaceProxy:
    def __init__(self, caller: _Caller):
        self._caller = caller

    def identify(self, frame_bgr, person_box_xyxy, *, store_id, company_id):
        return self._caller.call(OP_FACE, frame_bgr, {
            "person_box": np.asarray(person_box_xyxy, np.float32),
            "store_id": store_id, "company_id": company_id,
        })


class PingProxy:
    """Transport with no model behind it, for pricing the boundary itself."""

    def __init__(self, caller: _Caller):
        self._caller = caller

    def __call__(self, frame_bgr: np.ndarray):
        return self._caller.call(OP_PING, frame_bgr, {})


class InferenceClient:
    """What a camera holds instead of its own models.

    Mirrors SharedInferenceRegistry's factory methods so a pipeline can take
    either one without noticing. The `build` argument on get_reid_embedder is
    accepted and ignored: locally it constructs a session, here the session
    already exists in another process.
    """

    def __init__(self, channel: ClientChannel, free_q, request_q):
        self.channel = channel
        self.ring = FrameRing(channel.ring_spec)
        self._caller = _Caller(channel, self.ring, free_q, request_q)

    def get_pose(self, model_path: str):
        return PoseProxy(self._caller)

    def get_reid_embedder(self, model_path: str, *, build=None):
        return ReidProxy(self._caller)

    def get_till(self, model_path: str, *, imgsz: int = 640, names=None):
        return TillProxy(self._caller)

    def get_ping(self):
        return PingProxy(self._caller)

    def get_face_identifier(self):
        return FaceProxy(self._caller)

    def close(self):
        self.ring.close()
