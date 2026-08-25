from __future__ import annotations

import queue
import sys
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

import cv2
import numpy as np
import onnxruntime as ort

from backend.src.common.paths import repo_root

ROOT = repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from face_align import align  # noqa: E402
from face_identity import FaceGallery, FaceObservation, MatchDecision  # noqa: E402


ReqT = TypeVar("ReqT")
ResT = TypeVar("ResT")


def _preferred_providers(cpu_only: bool) -> list[str]:
    if cpu_only:
        return ["CPUExecutionProvider"]
    avail = ort.get_available_providers()
    out = []
    if sys.platform == "darwin" and "CoreMLExecutionProvider" in avail:
        out.append("CoreMLExecutionProvider")
    if sys.platform != "darwin" and "CUDAExecutionProvider" in avail:
        out.append("CUDAExecutionProvider")
    if "CPUExecutionProvider" in avail:
        out.append("CPUExecutionProvider")
    return out or ["CPUExecutionProvider"]


def _session_options(config: dict | None = None):
    """Session options with the intra-op pool sized deliberately.

    onnxruntime defaults its intra-op thread pool to the core count and does
    not read OMP_NUM_THREADS, so cap_native_threads has no effect on it. With
    several sessions in one process that is a few dozen native threads
    competing for a 23-core quota, and the cost shows up as latency on every
    call rather than as an obviously busy CPU.
    """
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.log_severity_level = 3
    cfg = config or {}
    threads = int(cfg.get("ort_intra_threads", 0) or 0)
    if threads > 0:
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
    if not cfg.get("ort_allow_spinning", True):
        # onnxruntime's pools spin before sleeping, which trades CPU for a
        # little latency on the next call. In a process that also has to
        # answer requests from other processes, that trade is the wrong way
        # round: the spinning delays every reply, including ones no model is
        # involved in.
        so.add_session_config_entry("session.intra_op.allow_spinning", "0")
        so.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return so


def _letterbox(im: np.ndarray, size: int) -> tuple[np.ndarray, float, int, int]:
    h, w = im.shape[:2]
    r = min(size / h, size / w)
    nh, nw = round(h * r), round(w * r)
    pad_t, pad_l = (size - nh) // 2, (size - nw) // 2
    out = np.full((size, size, 3), 114, np.uint8)
    out[pad_t:pad_t + nh, pad_l:pad_l + nw] = cv2.resize(im, (nw, nh))
    return out, r, pad_l, pad_t


@dataclass
class PoseResult:
    boxes: np.ndarray
    kpts: list[np.ndarray]
    profile: dict[str, float | int | str]


@dataclass
class FaceDetectRequest:
    frame_bgr: np.ndarray
    person_box_xyxy: np.ndarray
    face_conf: float
    iou: float
    crop_expand: float
    min_face_score: float


@dataclass
class FaceDetectResult:
    chip_bgr: np.ndarray | None
    detector_score: float | None
    face_box_xyxy: np.ndarray | None
    landmarks_xy: np.ndarray | None
    person_box_xyxy: np.ndarray
    crop_box_xyxy: np.ndarray | None
    profile: dict[str, float | int | str]


@dataclass
class ArcFaceResult:
    embedding: np.ndarray
    profile: dict[str, float | int | str]


@dataclass
class TillResult:
    detections: list[tuple[str, float, np.ndarray]]
    profile: dict[str, float | int | str]


@dataclass
class _QueuedRequest(Generic[ReqT, ResT]):
    request: ReqT
    future: Future[ResT]
    submitted_at: float
    queue_depth_on_submit: int


class _BatchRunner(Generic[ReqT, ResT]):
    def __init__(
        self,
        *,
        name: str,
        max_batch: int,
        batch_wait_s: float,
        num_workers: int,
        infer_batch: Callable[[list[ReqT]], list[ResT]],
    ):
        self.name = name
        self.max_batch = max_batch
        self.batch_wait_s = max(0.0, batch_wait_s)
        self.num_workers = max(1, int(num_workers))
        self.infer_batch = infer_batch
        self._queue: queue.Queue[_QueuedRequest[ReqT, ResT] | None] = queue.Queue()
        self._stop = threading.Event()
        self._threads = [
            threading.Thread(
                target=self._run,
                name=f"{name}-batch-runner-{idx}",
                daemon=True,
            )
            for idx in range(self.num_workers)
        ]
        for thread in self._threads:
            thread.start()

    def submit(self, request: ReqT) -> ResT:
        future: Future[ResT] = Future()
        self._queue.put(
            _QueuedRequest(
                request=request,
                future=future,
                submitted_at=time.perf_counter(),
                queue_depth_on_submit=self._queue.qsize(),
            )
        )
        return future.result()

    def close(self):
        self._stop.set()
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            thread.join(timeout=5.0)

    def _run(self):
        while not self._stop.is_set():
            first = self._queue.get()
            if first is None:
                continue
            batch = [first]
            collect_started_at = time.perf_counter()
            collect_deadline = collect_started_at + self.batch_wait_s
            while len(batch) < self.max_batch:
                try:
                    if self.batch_wait_s <= 0.0:
                        nxt = self._queue.get_nowait()
                    else:
                        remaining = collect_deadline - time.perf_counter()
                        if remaining <= 0.0:
                            break
                        nxt = self._queue.get(timeout=remaining)
                    if nxt is None:
                        continue
                    batch.append(nxt)
                except queue.Empty:
                    break
            try:
                dispatched_at = time.perf_counter()
                results = self.infer_batch([item.request for item in batch])
                if len(results) != len(batch):
                    raise RuntimeError(
                        f"{self.name} returned {len(results)} results for {len(batch)} requests"
                    )
                batch_collect_wait_s = max(0.0, dispatched_at - collect_started_at)
                batch_size = len(batch)
                for item, result in zip(batch, results):
                    profile = getattr(result, "profile", None)
                    if isinstance(profile, dict):
                        profile["runner_name"] = self.name
                        profile["batch_size"] = int(batch_size)
                        profile["batch_wait_s"] = float(batch_collect_wait_s)
                        profile["queue_wait_s"] = float(dispatched_at - item.submitted_at)
                        profile["queue_depth_on_submit"] = int(item.queue_depth_on_submit)
                        profile["queue_depth_on_dispatch"] = int(self._queue.qsize())
                        profile["worker_name"] = threading.current_thread().name
                    item.future.set_result(result)
            except Exception as exc:  # pragma: no cover
                for item in batch:
                    item.future.set_exception(exc)


class SharedPoseService:
    def __init__(
        self,
        model_path: str,
        *,
        cpu_only: bool,
        session_config: dict[str, Any] | None = None,
        imgsz: int = 640,
        max_batch: int = 8,
        batch_wait_s: float = 0.005,
        num_workers: int = 2,
        pad_batch: bool = True,
    ):
        self.model_path = model_path
        self.imgsz = imgsz
        self.providers = _preferred_providers(cpu_only)
        so = _session_options(session_config)
        self.sess = ort.InferenceSession(model_path, so, providers=self.providers)
        meta = self.sess.get_inputs()[0]
        self.inp = meta.name
        self.input_dtype = np.float16 if meta.type == "tensor(float16)" else np.float32
        self.provider = self.sess.get_providers()[0]
        # The CUDA EP re-does per-shape setup on every batch-size change (~150ms on
        # L40S), even for shapes it has already seen. Padding every run to max_batch
        # keeps the input shape constant, which is worth far more than the wasted
        # slots: measured 154ms/call for varying shapes vs 13ms for a fixed batch of 4.
        self.pad_batch = pad_batch
        self.runner = _BatchRunner(
            name=f"pose-{Path(model_path).stem}",
            max_batch=max_batch,
            batch_wait_s=batch_wait_s,
            num_workers=num_workers,
            infer_batch=self._infer_batch,
        )

    def preprocess(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
        """Letterbox and normalise, without submitting anything.

        Exists so a CUDA-free decoder process can do this work and hand the
        inference tier a finished blob, instead of the CUDA process doing its
        own preprocessing and losing the whole point of splitting decode out:
        that CPU work should scale with decoder count, not sit inside the one
        or two processes holding a GPU context.
        """
        tl0 = time.perf_counter()
        lb, r, dx, dy = _letterbox(frame_bgr, self.imgsz)
        tl1 = time.perf_counter()
        blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
        blob = np.ascontiguousarray(blob, self.input_dtype) / self.input_dtype(255.0)
        tl2 = time.perf_counter()
        meta = {
            "frame_shape": frame_bgr.shape[:2], "ratio": r, "dx": dx, "dy": dy,
            "letterbox_s": tl1 - tl0, "preprocess_s": tl2 - tl1,
        }
        return blob, meta

    def detect(self, frame_bgr: np.ndarray, *, conf: float) -> PoseResult:
        # Letterbox and normalise on the calling camera thread. This used to run
        # inside the batch-runner thread, which funnelled ~8ms of per-frame CPU
        # work through num_workers threads while the camera threads sat idle
        # waiting on submit(), and showed up as queue_wait being nearly twice the
        # actual inference time.
        blob, meta = self.preprocess(frame_bgr)
        return self.detect_preprocessed(blob, conf=conf, **meta)

    def detect_preprocessed(self, blob: np.ndarray, *, conf: float, frame_shape,
                            ratio: float, dx: int, dy: int,
                            letterbox_s: float = 0.0, preprocess_s: float = 0.0) -> PoseResult:
        """Submit a blob someone else already built. Same result either way:
        this is the tail end of detect(), factored out so a decoder process's
        preprocessing and an in-process caller's preprocessing land in the
        exact same inference and box-decode arithmetic."""
        return self.runner.submit(
            {
                "blob": blob,
                "frame_shape": tuple(frame_shape),
                "ratio": ratio,
                "dx": dx,
                "dy": dy,
                "conf": float(conf),
                "letterbox_s": letterbox_s,
                "preprocess_s": preprocess_s,
            }
        )

    def close(self):
        self.runner.close()

    def _infer_batch(self, requests: list[dict[str, Any]]) -> list[PoseResult]:
        metas = [
            {
                "frame_shape": req["frame_shape"],
                "ratio": req["ratio"],
                "dx": req["dx"],
                "dy": req["dy"],
                "conf": req["conf"],
                "letterbox_s": req["letterbox_s"],
                "preprocess_s": req["preprocess_s"],
            }
            for req in requests
        ]
        blobs = [req["blob"] for req in requests]
        batch = np.stack(blobs, axis=0)
        real_n = len(blobs)
        if self.pad_batch and real_n < self.runner.max_batch:
            pad = np.repeat(batch[-1:], self.runner.max_batch - real_n, axis=0)
            batch = np.concatenate([batch, pad], axis=0)
        ti0 = time.perf_counter()
        raw = self.sess.run(None, {self.inp: batch})[0]
        infer_s = time.perf_counter() - ti0
        if raw.ndim == 2:
            raw = raw[None, ...]
        raw = raw[:real_n]
        td0 = time.perf_counter()
        results = []
        batch_size = max(1, len(requests))
        per_item_infer = infer_s / max(1, len(requests))
        for idx, (out, meta) in enumerate(zip(raw, metas)):
            h, w = meta["frame_shape"]
            selected = out[out[:, 4] >= meta["conf"]]
            if len(selected):
                boxes = selected[:, :4].astype(np.float32, copy=True)
                boxes[:, [0, 2]] = (boxes[:, [0, 2]] - meta["dx"]) / meta["ratio"]
                boxes[:, [1, 3]] = (boxes[:, [1, 3]] - meta["dy"]) / meta["ratio"]
                boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0.0, w - 1.0)
                boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0.0, h - 1.0)
                keep = ((boxes[:, 2] - boxes[:, 0]) >= 8.0) & ((boxes[:, 3] - boxes[:, 1]) >= 16.0)
                boxes = boxes[keep]
                scores = selected[keep, 4:5].astype(np.float32, copy=False)
                k_arr = selected[keep, 6:].reshape(-1, 17, 3).astype(np.float32, copy=True)
                k_arr[:, :, 0] = (k_arr[:, :, 0] - meta["dx"]) / meta["ratio"]
                k_arr[:, :, 1] = (k_arr[:, :, 1] - meta["dy"]) / meta["ratio"]
                kpts = [k for k in k_arr]
                boxes = np.concatenate([boxes, scores], axis=1)
            else:
                boxes = np.empty((0, 5), np.float32)
                kpts = []
            decode_s = 0.0
            if idx == len(raw) - 1:
                decode_s = time.perf_counter() - td0
            results.append(
                PoseResult(
                    boxes=boxes,
                    kpts=kpts,
                    profile={
                        "provider": self.provider,
                        "letterbox_s": float(meta["letterbox_s"]),
                        "preprocess_s": float(meta["preprocess_s"]),
                        "infer_s": float(per_item_infer),
                        "decode_s": float(decode_s / max(1, len(raw))),
                        "total_s": float(
                            meta["letterbox_s"] + meta["preprocess_s"] + per_item_infer
                        ),
                        "detections": int(len(boxes)),
                        "batch_size": int(batch_size),
                    },
                )
            )
        return results


class SharedFaceDetectorService:
    def __init__(
        self,
        model_path: str,
        *,
        cpu_only: bool,
        session_config: dict[str, Any] | None = None,
        imgsz: int = 640,
        max_batch: int = 8,
        batch_wait_s: float = 0.005,
        num_workers: int = 1,
    ):
        self.model_path = model_path
        self.imgsz = imgsz
        self.providers = _preferred_providers(cpu_only)
        so = _session_options(session_config)
        self.sess = ort.InferenceSession(model_path, so, providers=self.providers)
        self.inp = self.sess.get_inputs()[0].name
        self.provider = self.sess.get_providers()[0]
        self.runner = _BatchRunner(
            name=f"face-det-{Path(model_path).stem}",
            max_batch=max_batch,
            batch_wait_s=batch_wait_s,
            num_workers=num_workers,
            infer_batch=self._infer_batch,
        )

    def detect_face(self, request: FaceDetectRequest) -> FaceDetectResult:
        return self.runner.submit(request)

    def close(self):
        self.runner.close()

    @staticmethod
    def _clip_box(box: np.ndarray, width: int, height: int) -> np.ndarray:
        x1, y1, x2, y2 = map(float, box)
        x1 = max(0.0, min(x1, width - 1.0))
        y1 = max(0.0, min(y1, height - 1.0))
        x2 = max(0.0, min(x2, width - 1.0))
        y2 = max(0.0, min(y2, height - 1.0))
        return np.array([x1, y1, x2, y2], np.float32)

    def _infer_batch(self, requests: list[FaceDetectRequest]) -> list[FaceDetectResult]:
        prefilled: list[FaceDetectResult | None] = [None] * len(requests)
        valid_meta = []
        blobs = []
        t0 = time.perf_counter()
        for idx, req in enumerate(requests):
            height, width = req.frame_bgr.shape[:2]
            person_box = np.asarray(req.person_box_xyxy, np.float32)
            x1, y1, x2, y2 = map(float, person_box)
            box_w = max(1.0, x2 - x1)
            box_h = max(1.0, y2 - y1)
            pad_x = req.crop_expand * box_w
            pad_y = req.crop_expand * box_h
            crop_box = self._clip_box(
                np.array([x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y], np.float32),
                width,
                height,
            )
            cx1, cy1, cx2, cy2 = crop_box.astype(int)
            if cx2 <= cx1 or cy2 <= cy1:
                prefilled[idx] = FaceDetectResult(
                    chip_bgr=None,
                    detector_score=None,
                    face_box_xyxy=None,
                    landmarks_xy=None,
                    person_box_xyxy=person_box,
                    crop_box_xyxy=None,
                    profile={"provider": self.provider, "infer_s": 0.0, "detections": 0},
                )
                continue
            crop = req.frame_bgr[cy1:cy2, cx1:cx2]
            lb, r, dx, dy = _letterbox(crop, self.imgsz)
            blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
            blob = np.ascontiguousarray(blob, np.float32) / 255.0
            blobs.append(blob)
            valid_meta.append(
                {
                    "index": idx,
                    "crop_origin": (cx1, cy1),
                    "ratio": r,
                    "dx": dx,
                    "dy": dy,
                    "crop_box": crop_box,
                    "person_box": person_box,
                    "req": req,
                }
            )
        if not blobs:
            return [item for item in prefilled if item is not None]
        ti0 = time.perf_counter()
        raw = self.sess.run(None, {self.inp: np.stack(blobs, axis=0)})[0]
        infer_s = time.perf_counter() - ti0
        if raw.ndim == 2:
            raw = raw[None, ...]
        per_item_infer = infer_s / max(1, len(valid_meta))
        for out, meta in zip(raw, valid_meta):
            req = meta["req"]
            score = out[:, 15]
            keep = score >= req.face_conf
            out = out[keep]
            score = score[keep]
            best_chip = None
            best_score = None
            best_box = None
            best_lm = None
            if len(out):
                cx, cy, bw, bh = out[:, 0], out[:, 1], out[:, 2], out[:, 3]
                boxes_xywh = np.stack([cx - bw / 2, cy - bh / 2, bw, bh], axis=1)
                idxs = cv2.dnn.NMSBoxes(
                    boxes_xywh.tolist(),
                    score.tolist(),
                    req.face_conf,
                    req.iou,
                )
                if len(idxs):
                    for i in np.array(idxs).reshape(-1):
                        if float(score[i]) < req.min_face_score:
                            continue
                        x1 = cx[i] - bw[i] / 2
                        y1 = cy[i] - bh[i] / 2
                        x2 = cx[i] + bw[i] / 2
                        y2 = cy[i] + bh[i] / 2
                        face_box = (np.array([x1, y1, x2, y2], np.float32) - [meta["dx"], meta["dy"], meta["dx"], meta["dy"]]) / meta["ratio"]
                        lm = out[i, 4:14].reshape(5, 2).astype(np.float32, copy=True)
                        lm = (lm - [meta["dx"], meta["dy"]]) / meta["ratio"]
                        area = max(0.0, float(face_box[2] - face_box[0])) * max(0.0, float(face_box[3] - face_box[1]))
                        key = (float(score[i]), area)
                        if best_score is None or key > (best_score, max(0.0, float(best_box[2] - best_box[0])) * max(0.0, float(best_box[3] - best_box[1]))):
                            origin = np.array([meta["crop_origin"][0], meta["crop_origin"][1], meta["crop_origin"][0], meta["crop_origin"][1]], np.float32)
                            best_box = face_box + origin
                            best_lm = lm + np.array([meta["crop_origin"][0], meta["crop_origin"][1]], np.float32)
                            best_score = float(score[i])
                    if best_lm is not None:
                        best_chip = align(req.frame_bgr, best_lm, size=112)
            prefilled[meta["index"]] = FaceDetectResult(
                chip_bgr=best_chip,
                detector_score=best_score,
                face_box_xyxy=None if best_box is None else best_box.astype(np.float32),
                landmarks_xy=None if best_lm is None else best_lm.astype(np.float32),
                person_box_xyxy=meta["person_box"],
                crop_box_xyxy=meta["crop_box"].astype(np.float32),
                profile={
                    "provider": self.provider,
                    "infer_s": float(per_item_infer),
                    "detections": 0 if best_box is None else 1,
                    "batch_size": len(valid_meta),
                    "elapsed_s": float(time.perf_counter() - t0),
                },
            )
        return [item for item in prefilled if item is not None]


class SharedArcFaceService:
    def __init__(
        self,
        model_path: str,
        *,
        cpu_only: bool,
        session_config: dict[str, Any] | None = None,
        max_batch: int = 16,
        batch_wait_s: float = 0.005,
        num_workers: int = 1,
    ):
        self.model_path = model_path
        self.providers = _preferred_providers(cpu_only)
        so = _session_options(session_config)
        self.sess = ort.InferenceSession(model_path, so, providers=self.providers)
        self.inp = self.sess.get_inputs()[0].name
        self.provider = self.sess.get_providers()[0]
        self.runner = _BatchRunner(
            name=f"arcface-{Path(model_path).stem}",
            max_batch=max_batch,
            batch_wait_s=batch_wait_s,
            num_workers=num_workers,
            infer_batch=self._infer_batch,
        )

    def embed(self, chip_bgr: np.ndarray) -> ArcFaceResult:
        return self.runner.submit(chip_bgr)

    def close(self):
        self.runner.close()

    def _infer_batch(self, chips: list[np.ndarray]) -> list[ArcFaceResult]:
        blobs = []
        for chip in chips:
            if chip.shape[:2] != (112, 112):
                chip = cv2.resize(chip, (112, 112))
            x = cv2.cvtColor(chip, cv2.COLOR_BGR2RGB).astype(np.float32)
            x = (x - 127.5) / 127.5
            blobs.append(np.transpose(x, (2, 0, 1)))
        batch = np.ascontiguousarray(np.stack(blobs, axis=0))
        ti0 = time.perf_counter()
        feats = self.sess.run(None, {self.inp: batch})[0]
        infer_s = time.perf_counter() - ti0
        results = []
        per_item_infer = infer_s / max(1, len(chips))
        for feat in feats:
            emb = feat.astype(np.float32)
            emb = emb / (np.linalg.norm(emb) + 1e-9)
            results.append(
                ArcFaceResult(
                    embedding=emb,
                    profile={
                        "provider": self.provider,
                        "infer_s": float(per_item_infer),
                        "batch_size": len(chips),
                    },
                )
            )
        return results


class SharedTillService:
    def __init__(
        self,
        model_path: str,
        *,
        cpu_only: bool,
        session_config: dict[str, Any] | None = None,
        imgsz: int = 640,
        names: tuple[str, ...] = ("Close", "Open"),
        max_batch: int = 8,
        batch_wait_s: float = 0.005,
        num_workers: int = 1,
    ):
        self.model_path = model_path
        self.imgsz = imgsz
        self.providers = _preferred_providers(cpu_only)
        so = _session_options(session_config)
        self.sess = ort.InferenceSession(model_path, so, providers=self.providers)
        meta = self.sess.get_inputs()[0]
        self.inp = meta.name
        self.input_dtype = np.float16 if meta.type == "tensor(float16)" else np.float32
        self.provider = self.sess.get_providers()[0]
        self.names = {idx: name for idx, name in enumerate(names)}
        self.runner = _BatchRunner(
            name=f"till-{Path(model_path).stem}",
            max_batch=max_batch,
            batch_wait_s=batch_wait_s,
            num_workers=num_workers,
            infer_batch=self._infer_batch,
        )

    def detect(self, frame_bgr: np.ndarray, *, conf: float) -> TillResult:
        return self.runner.submit({"frame_bgr": frame_bgr, "conf": conf})

    def close(self):
        self.runner.close()

    def _infer_batch(self, requests: list[dict[str, Any]]) -> list[TillResult]:
        metas = []
        blobs = []
        letterbox_total_s = 0.0
        preprocess_total_s = 0.0
        for req in requests:
            frame = req["frame_bgr"]
            tl0 = time.perf_counter()
            lb, r, dx, dy = _letterbox(frame, self.imgsz)
            tl1 = time.perf_counter()
            blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
            blob = np.ascontiguousarray(blob, self.input_dtype) / self.input_dtype(255.0)
            tl2 = time.perf_counter()
            letterbox_total_s += tl1 - tl0
            preprocess_total_s += tl2 - tl1
            metas.append(
                {
                    "frame_shape": frame.shape[:2],
                    "ratio": r,
                    "dx": dx,
                    "dy": dy,
                    "conf": float(req["conf"]),
                }
            )
            blobs.append(blob)
        batch = np.stack(blobs, axis=0)
        ti0 = time.perf_counter()
        det_batch, proto_batch = self.sess.run(None, {self.inp: batch})
        infer_s = time.perf_counter() - ti0
        if det_batch.ndim == 2:
            det_batch = det_batch[None, ...]
        if proto_batch.ndim == 3:
            proto_batch = proto_batch[None, ...]
        td0 = time.perf_counter()
        results = []
        batch_size = max(1, len(requests))
        per_item_letterbox = letterbox_total_s / batch_size
        per_item_preprocess = preprocess_total_s / batch_size
        per_item_infer = infer_s / batch_size
        for det, proto, meta in zip(det_batch, proto_batch, metas):
            g = proto.shape[-1]
            stride = self.imgsz / g
            flat = proto.reshape(proto.shape[0], -1)
            cand = det[det[:, 4] >= meta["conf"]]
            before_nms = int(len(cand))
            survivors = 0
            if len(cand):
                boxes_xywh = np.stack(
                    [
                        cand[:, 0] - cand[:, 2] / 2,
                        cand[:, 1] - cand[:, 3] / 2,
                        cand[:, 2],
                        cand[:, 3],
                    ],
                    axis=1,
                )
                classes = cand[:, 5].astype(np.int32)
                keep_idx = []
                for cls_id in np.unique(classes):
                    cls_mask = classes == cls_id
                    cls_boxes = boxes_xywh[cls_mask]
                    cls_scores = cand[cls_mask, 4]
                    idx = cv2.dnn.NMSBoxes(
                        cls_boxes.tolist(),
                        cls_scores.tolist(),
                        meta["conf"],
                        0.5,
                    )
                    if len(idx) == 0:
                        continue
                    cls_pos = np.flatnonzero(cls_mask)
                    keep_idx.extend(cls_pos[np.array(idx).reshape(-1)].tolist())
                if keep_idx:
                    cand = cand[np.array(sorted(set(keep_idx)), dtype=np.int32)]
                else:
                    cand = cand[:0]
            out = []
            for d in cand:
                survivors += 1
                logits = np.asarray(d[6:] @ flat, dtype=np.float32).reshape(g, g)
                m = 1.0 / (1.0 + np.exp(-logits))
                x1, y1 = max(0, int(d[0])), max(0, int(d[1]))
                x2 = min(self.imgsz, int(np.ceil(d[2])))
                y2 = min(self.imgsz, int(np.ceil(d[3])))
                if x2 <= x1 or y2 <= y1:
                    continue
                gx1 = max(0, min(g - 1, int(np.floor(x1 / stride))))
                gy1 = max(0, min(g - 1, int(np.floor(y1 / stride))))
                gx2 = max(gx1 + 1, min(g, int(np.ceil(x2 / stride))))
                gy2 = max(gy1 + 1, min(g, int(np.ceil(y2 / stride))))
                roi = m[gy1:gy2, gx1:gx2]
                if roi.size == 0:
                    continue
                binm = cv2.resize(roi, (x2 - x1, y2 - y1), interpolation=cv2.INTER_LINEAR)
                binm = (binm > 0.5).astype(np.uint8)
                cnts, _ = cv2.findContours(binm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if not cnts:
                    continue
                poly = max(cnts, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)
                poly += np.array([x1, y1], dtype=np.float32)
                poly = (poly - [meta["dx"], meta["dy"]]) / meta["ratio"]
                out.append((self.names[int(d[5])], float(d[4]), poly.astype(np.int32)))
            results.append(
                TillResult(
                    detections=out,
                    profile={
                        "provider": self.provider,
                        "letterbox_s": float(per_item_letterbox),
                        "preprocess_s": float(per_item_preprocess),
                        "infer_s": float(per_item_infer),
                        "decode_masks_s": float((time.perf_counter() - td0) / batch_size),
                        "total_s": float(per_item_letterbox + per_item_preprocess + per_item_infer),
                        "num_candidates_before_nms": before_nms,
                        "num_candidates_after_nms": survivors,
                        "num_outputs": len(out),
                        "batch_size": batch_size,
                    },
                )
            )
        return results


class SharedFaceIdentifier:
    def __init__(
        self,
        *,
        face_detector: SharedFaceDetectorService,
        arcface: SharedArcFaceService,
        gallery_db_path: str,
        match_threshold: float,
        match_margin: float,
        face_conf: float = 0.20,
        crop_expand: float = 0.12,
        iou: float = 0.45,
    ):
        self.face_detector = face_detector
        self.arcface = arcface
        self.gallery = FaceGallery(gallery_db_path)
        self.match_threshold = match_threshold
        self.match_margin = match_margin
        self.face_conf = face_conf
        self.crop_expand = crop_expand
        self.iou = iou

    def close(self):
        self.gallery.close()

    def identify(
        self,
        frame_bgr: np.ndarray,
        person_box_xyxy: np.ndarray,
        *,
        store_id: str | None,
        company_id: str | None,
    ) -> MatchDecision:
        det = self.face_detector.detect_face(
            FaceDetectRequest(
                frame_bgr=frame_bgr,
                person_box_xyxy=np.asarray(person_box_xyxy, np.float32),
                face_conf=self.face_conf,
                iou=self.iou,
                crop_expand=self.crop_expand,
                min_face_score=self.face_conf,
            )
        )
        if det.chip_bgr is None or det.face_box_xyxy is None or det.landmarks_xy is None:
            return MatchDecision(
                observation=None,
                person=None,
                top1=None,
                top2=None,
                threshold=self.match_threshold,
                margin=self.match_margin,
                reason="no_face_detected",
            )
        emb = self.arcface.embed(det.chip_bgr)
        obs = FaceObservation(
            chip_bgr=det.chip_bgr,
            embedding=emb.embedding,
            detector_score=float(det.detector_score or 0.0),
            face_box_xyxy=det.face_box_xyxy,
            landmarks_xy=det.landmarks_xy,
            person_box_xyxy=np.asarray(person_box_xyxy, np.float32),
            crop_box_xyxy=np.asarray(det.crop_box_xyxy, np.float32),
        )
        person, top1, top2, reason = self.gallery.match(
            obs.embedding,
            threshold=self.match_threshold,
            margin=self.match_margin,
            store_id=store_id,
            company_id=company_id,
        )
        return MatchDecision(
            observation=obs,
            person=person,
            top1=top1,
            top2=top2,
            threshold=self.match_threshold,
            margin=self.match_margin,
            reason=reason,
        )


class SharedInferenceRegistry:
    def __init__(self, *, cpu_only: bool, config: dict[str, Any] | None = None):
        self.cpu_only = cpu_only
        self.config = dict(config or {})
        self._lock = threading.Lock()
        self._key_locks: dict[Any, threading.Lock] = {}
        self._pose: dict[str, SharedPoseService] = {}
        self._face_det: dict[str, SharedFaceDetectorService] = {}
        self._arcface: dict[str, SharedArcFaceService] = {}
        self._till: dict[tuple[str, int, tuple[str, ...]], SharedTillService] = {}
        self._reid: dict[str, Any] = {}

    def _get_or_create(self, store: dict, key: Any, build: Callable[[], Any]) -> Any:
        """Build one service per key without serialising unrelated model loads.

        Creating a CUDA session takes seconds, so holding the registry lock across
        the build made every camera in a shard queue behind the first one — and
        behind unrelated models too. Only the per-key lock is held while building.
        """
        service = store.get(key)
        if service is not None:
            return service
        with self._lock:
            key_lock = self._key_locks.get(key)
            if key_lock is None:
                key_lock = threading.Lock()
                self._key_locks[key] = key_lock
        with key_lock:
            service = store.get(key)
            if service is None:
                service = build()
                store[key] = service
            return service

    def get_pose(self, model_path: str) -> SharedPoseService:
        return self._get_or_create(
            self._pose,
            model_path,
            lambda: SharedPoseService(
                model_path,
                cpu_only=self.cpu_only,
                session_config=self.config,
                num_workers=int(self.config.get("pose_num_workers", 2)),
                max_batch=int(self.config.get("pose_max_batch", 8)),
                batch_wait_s=float(self.config.get("pose_batch_wait_s", 0.005)),
                pad_batch=bool(self.config.get("pose_pad_batch", True)),
            ),
        )

    def get_face_detector(self, model_path: str) -> SharedFaceDetectorService:
        return self._get_or_create(
            self._face_det,
            model_path,
            lambda: SharedFaceDetectorService(
                model_path,
                cpu_only=self.cpu_only,
                session_config=self.config,
                num_workers=int(self.config.get("face_det_num_workers", 1)),
                max_batch=int(self.config.get("face_det_max_batch", 8)),
                batch_wait_s=float(self.config.get("face_det_batch_wait_s", 0.005)),
            ),
        )

    def get_arcface(self, model_path: str) -> SharedArcFaceService:
        return self._get_or_create(
            self._arcface,
            model_path,
            lambda: SharedArcFaceService(
                model_path,
                cpu_only=self.cpu_only,
                session_config=self.config,
                num_workers=int(self.config.get("arcface_num_workers", 1)),
                max_batch=int(self.config.get("arcface_max_batch", 16)),
                batch_wait_s=float(self.config.get("arcface_batch_wait_s", 0.005)),
            ),
        )

    # Attributes Embedder.__init__ sets. sess/inp/input_dtype/dim/provider are
    # read-only after construction and safe to share; last_profile and _pad_batch
    # are mutated on every call and must stay per-camera.
    _REID_SHARED_ATTRS = ("sess", "inp", "input_dtype", "dim", "provider")
    _REID_PRIVATE_ATTRS = ("last_profile", "_pad_batch")

    def get_reid_embedder(self, model_path: str, *, build: Callable[[], Any]) -> Any:
        """Return an embedder that reuses one ONNX session per process.

        Every camera used to build its own OSNet session behind BOOTSTRAP_LOCK,
        which measured 8.5s each and 33% of total bootstrap on a 16-camera run.
        The session is loaded once; each caller still gets its own scratch buffer
        and profile dict, so concurrent calls do not share mutable state.
        """
        template = self._get_or_create(self._reid, model_path, build)
        missing = [
            attr
            for attr in (*self._REID_SHARED_ATTRS, *self._REID_PRIVATE_ATTRS)
            if not hasattr(template, attr)
        ]
        if missing:
            # Embedder's shape changed underneath us; a private session is slower
            # but always correct.
            return build()
        clone = template.__class__.__new__(template.__class__)
        for attr in self._REID_SHARED_ATTRS:
            setattr(clone, attr, getattr(template, attr))
        clone.last_profile = {}
        clone._pad_batch = np.zeros_like(template._pad_batch)
        return clone

    def get_till(
        self,
        model_path: str,
        *,
        imgsz: int = 640,
        names: tuple[str, ...] = ("Close", "Open"),
    ) -> SharedTillService:
        key = (model_path, imgsz, names)
        return self._get_or_create(
            self._till,
            key,
            lambda: SharedTillService(
                model_path,
                cpu_only=self.cpu_only,
                session_config=self.config,
                imgsz=imgsz,
                names=names,
                num_workers=int(self.config.get("till_num_workers", 1)),
                max_batch=int(self.config.get("till_max_batch", 8)),
                batch_wait_s=float(self.config.get("till_batch_wait_s", 0.005)),
            ),
        )

    def close(self):
        for services in (self._pose, self._face_det, self._arcface, self._till):
            for service in services.values():
                service.close()
            services.clear()
        self._reid.clear()  # plain Embedders, nothing to shut down
