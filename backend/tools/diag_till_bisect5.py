"""Fifth bisect: time every section inside _infer_batch, line by line.

Three hypotheses have now been tested and rejected -- thread-pool handoff
(G/K: 10.8 vs 6.5ms), fresh-vs-reused input buffers (P'/R': 6.58 vs 6.73ms),
and the batch runner itself (L, calling _infer_batch directly on the main
thread, was already 56ms). Meanwhile the same session times 6.5ms and the
same preprocessing times 4ms, so ~45ms of the 56 is unaccounted for and it
is somewhere in this function that no measurement has looked at yet.

The service's own profile only reports letterbox, preprocess, infer and
decode_masks. Everything between them -- the candidate filter, the per-class
NMS with its .tolist() conversions, the proto matmul setup -- is invisible to
it. This re-implements the body with a timer around every section, which is
the only way to see the parts the profile never reported.

    python backend/tools/diag_till_bisect5.py
"""
from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "scripts", ROOT / "backend/tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

TILL_MODEL = str(ROOT / "models/till_seg_v4_640.onnx")
REPS = 20


def main() -> None:
    import cv2

    from diag_infer_modules import _real_frames

    from backend.src.runtime.shared_inference import SharedInferenceRegistry, _letterbox

    frames = _real_frames(8)
    cfg = {"till_num_workers": 1, "till_max_batch": 1, "till_batch_wait_s": 0.005}
    registry = SharedInferenceRegistry(cpu_only=False, config=cfg)
    acc: dict[str, float] = defaultdict(float)
    shapes = {}
    try:
        till = registry.get_till(TILL_MODEL, imgsz=640)
        imgsz, conf = till.imgsz, 0.7

        for _ in range(3):
            till._infer_batch([{"frame_bgr": frames[0], "conf": conf}])

        wall0 = time.perf_counter()
        for rep in range(REPS):
            frame = frames[rep % len(frames)]

            t = time.perf_counter()
            lb, ratio, dx, dy = _letterbox(frame, imgsz)
            acc["letterbox"] += time.perf_counter() - t

            t = time.perf_counter()
            blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
            blob = np.ascontiguousarray(blob, till.input_dtype) / till.input_dtype(255.0)
            batch = np.stack([blob], axis=0)
            acc["preprocess"] += time.perf_counter() - t

            t = time.perf_counter()
            det_batch, proto_batch = till.sess.run(None, {till.inp: batch})
            acc["sess.run"] += time.perf_counter() - t

            if det_batch.ndim == 2:
                det_batch = det_batch[None, ...]
            if proto_batch.ndim == 3:
                proto_batch = proto_batch[None, ...]
            det, proto = det_batch[0], proto_batch[0]
            shapes["det"] = det.shape
            shapes["proto"] = proto.shape

            t = time.perf_counter()
            g = proto.shape[-1]
            stride = imgsz / g
            flat = proto.reshape(proto.shape[0], -1)
            cand = det[det[:, 4] >= conf]
            acc["filter"] += time.perf_counter() - t
            shapes["cand"] = cand.shape

            t = time.perf_counter()
            if len(cand):
                boxes_xywh = np.stack([cand[:, 0] - cand[:, 2] / 2, cand[:, 1] - cand[:, 3] / 2,
                                       cand[:, 2], cand[:, 3]], axis=1)
                classes = cand[:, 5].astype(np.int32)
                keep_idx = []
                for cls_id in np.unique(classes):
                    cls_mask = classes == cls_id
                    idx = cv2.dnn.NMSBoxes(boxes_xywh[cls_mask].tolist(),
                                           cand[cls_mask, 4].tolist(), conf, 0.5)
                    if len(idx) == 0:
                        continue
                    cls_pos = np.flatnonzero(cls_mask)
                    keep_idx.extend(cls_pos[np.array(idx).reshape(-1)].tolist())
                cand = cand[np.array(sorted(set(keep_idx)), dtype=np.int32)] if keep_idx else cand[:0]
            acc["nms"] += time.perf_counter() - t

            t = time.perf_counter()
            for d in cand:
                logits = np.asarray(d[6:] @ flat, dtype=np.float32).reshape(g, g)
                m = 1.0 / (1.0 + np.exp(-logits))
                x1, y1 = max(0, int(d[0])), max(0, int(d[1]))
                x2, y2 = min(imgsz, int(np.ceil(d[2]))), min(imgsz, int(np.ceil(d[3])))
                if x2 <= x1 or y2 <= y1:
                    continue
                gx1 = max(0, min(g - 1, int(np.floor(x1 / stride))))
                gy1 = max(0, min(g - 1, int(np.floor(y1 / stride))))
                gx2 = max(gx1 + 1, min(g, int(np.ceil(x2 / stride))))
                gy2 = max(gy1 + 1, min(g, int(np.ceil(y2 / stride))))
                roi = m[gy1:gy2, gx1:gx2]
                if roi.size == 0:
                    continue
                binm = (cv2.resize(roi, (x2 - x1, y2 - y1), cv2.INTER_LINEAR) > 0.5).astype(np.uint8)
                cnts, _ = cv2.findContours(binm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if cnts:
                    max(cnts, key=cv2.contourArea)
            acc["masks"] += time.perf_counter() - t
        wall = (time.perf_counter() - wall0) / REPS

        print(f"  shapes: det={shapes['det']} proto={shapes['proto']} "
              f"candidates={shapes['cand']}", file=sys.stderr)
        print(f"  reimplemented body   {wall * 1e3:8.2f} ms/call", file=sys.stderr)
        for k, v in sorted(acc.items(), key=lambda kv: -kv[1]):
            print(f"    {k:<12} {v / REPS * 1e3:8.2f} ms  {v / REPS / wall * 100:5.1f}%",
                  file=sys.stderr)

        t0 = time.perf_counter()
        for rep in range(REPS):
            till._infer_batch([{"frame_bgr": frames[rep % len(frames)], "conf": conf}])
        real = (time.perf_counter() - t0) / REPS
        print(f"  the real _infer_batch {real * 1e3:8.2f} ms/call", file=sys.stderr)
    finally:
        registry.close()


if __name__ == "__main__":
    main()
