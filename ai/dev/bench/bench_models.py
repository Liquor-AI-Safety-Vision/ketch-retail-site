"""Benchmark public pose models on this store's top-view footage.

Two export formats appear here and are decoded separately:

    yolo26   (1, 300, 57)    NMS-free, already sorted, xyxy
    yolo11   (1, 56, 8400)   dense anchors, cxcywh, needs NMS

Timing is measured through onnxruntime on the CoreML EP - the same runtime the
pipeline deploys on - so the numbers are comparable to the 100 ms budget that a
10 FPS stream allows, not to a PyTorch benchmark.
"""
import time
import warnings

import cv2
import numpy as np
import onnxruntime as ort

warnings.filterwarnings("ignore")

CONF = 0.35
IMGSZ = 640
FRAMES = [0, 500, 1200, 2000, 3000, 3600, 4400, 5100]
# people with a substantially visible torso, counted by hand; the left edge of
# this scene is heavily occluded by shelving so these carry roughly +-1
TRUTH = {0: 3, 500: 3, 1200: 2, 2000: 1, 3000: 2, 3600: 2, 4400: 4, 5100: 3}

MODELS = [
    ("yolo26n", "models/yolo26n-pose.onnx"),
    ("yolo26s", "models/yolo26s-pose.onnx"),
    ("yolo26m", "models/yolo26m-pose.onnx"),
    ("yolo26l", "models/yolo26l-pose.onnx"),
    ("yolo26x", "models/yolo26x-pose.onnx"),
    ("yolo11n", "models/yolo11n-pose.onnx"),
    ("yolo11m", "models/yolo11m-pose.onnx"),
    ("yolo11x", "models/yolo11x-pose.onnx"),
]


def letterbox(im, size=IMGSZ):
    h, w = im.shape[:2]
    r = min(size / h, size / w)
    nh, nw = round(h * r), round(w * r)
    pt, pl = (size - nh) // 2, (size - nw) // 2
    out = np.full((size, size, 3), 114, np.uint8)
    out[pt:pt + nh, pl:pl + nw] = cv2.resize(im, (nw, nh))
    return out, r, pl, pt


def nms(boxes, scores, thr=0.5):
    idx = scores.argsort()[::-1]
    keep = []
    while len(idx):
        i = idx[0]
        keep.append(i)
        if len(idx) == 1:
            break
        b, r = boxes[i], boxes[idx[1:]]
        x1 = np.maximum(b[0], r[:, 0]); y1 = np.maximum(b[1], r[:, 1])
        x2 = np.minimum(b[2], r[:, 2]); y2 = np.minimum(b[3], r[:, 3])
        inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
        area_b = (b[2] - b[0]) * (b[3] - b[1])
        area_r = (r[:, 2] - r[:, 0]) * (r[:, 3] - r[:, 1])
        iou = inter / (area_b + area_r - inter + 1e-9)
        idx = idx[1:][iou <= thr]
    return keep


def decode(out, r, pl, pt, conf=CONF):
    """Return (boxes_xyxy_orig, scores, kpts) for either export layout."""
    o = out[0]
    if o.shape[-1] == 57:                       # yolo26: (300, 57), NMS-free
        o = o[o[:, 4] >= conf]
        boxes, scores, kp = o[:, :4].copy(), o[:, 4].copy(), o[:, 6:].reshape(-1, 17, 3)
    else:                                       # yolo11: (56, 8400), needs NMS
        o = o.T
        scores = o[:, 4]
        m = scores >= conf
        o, scores = o[m], scores[m]
        if not len(o):
            return np.zeros((0, 4)), np.zeros(0), np.zeros((0, 17, 3))
        cx, cy, w, h = o[:, 0], o[:, 1], o[:, 2], o[:, 3]
        boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1)
        kp = o[:, 5:].reshape(-1, 17, 3)
        k = nms(boxes, scores)
        boxes, scores, kp = boxes[k], scores[k], kp[k]
    if len(boxes):
        boxes = (boxes - [pl, pt, pl, pt]) / r
        kp = kp.copy()
        kp[..., 0] = (kp[..., 0] - pl) / r
        kp[..., 1] = (kp[..., 1] - pt) / r
    return boxes, scores, kp


def main():
    imgs = {f: cv2.imread(f"bench/f{f:05d}.jpg") for f in FRAMES}
    prep = {}
    for f, im in imgs.items():
        lb, r, pl, pt = letterbox(im)
        blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
        prep[f] = (np.ascontiguousarray(blob, np.float32)[None] / 255.0, r, pl, pt)

    hdr = "model    " + "".join(f"{f:>6}" for f in FRAMES) + "   sum   ms   fps"
    print(hdr)
    print("truth    " + "".join(f"{TRUTH[f]:>6}" for f in FRAMES)
          + f"{sum(TRUTH.values()):>6}")
    print("-" * len(hdr))

    results = {}
    for tag, path in MODELS:
        sess = ort.InferenceSession(
            path, providers=["CoreMLExecutionProvider", "CPUExecutionProvider"])
        name = sess.get_inputs()[0].name
        counts, ts, dets = [], [], {}
        for f in FRAMES:
            blob, r, pl, pt = prep[f]
            for _ in range(3):                  # warm the EP before timing
                out = sess.run(None, {name: blob})
            t0 = time.perf_counter()
            for _ in range(5):
                out = sess.run(None, {name: blob})
            ts.append((time.perf_counter() - t0) / 5 * 1000)
            b, s, k = decode(out[0], r, pl, pt)
            counts.append(len(b))
            dets[f] = (b, s, k)
        ms = float(np.mean(ts))
        results[tag] = (counts, ms, dets)
        print(f"{tag:<9}" + "".join(f"{c:>6}" for c in counts)
              + f"{sum(counts):>6}{ms:>6.1f}{1000 / ms:>6.1f}")

    np.save("bench/results.npy",
            {t: (c, m) for t, (c, m, _) in results.items()}, allow_pickle=True)

    # render the strongest model's boxes so false positives are visible
    best = max(results, key=lambda t: sum(results[t][0]))
    print(f"\nrendering {best} for visual check -> bench/best_{best}.jpg")
    tiles = []
    for f in [0, 500, 4400, 5100]:
        im = imgs[f].copy()
        b, s, k = results[best][2][f]
        for j in range(len(b)):
            x1, y1, x2, y2 = b[j].astype(int)
            cv2.rectangle(im, (x1, y1), (x2, y2), (0, 255, 0), 3)
            cv2.putText(im, f"{s[j]:.2f}", (x1, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 0), 3)
        cv2.putText(im, f"{best}  f{f}  n={len(b)} (truth {TRUTH[f]})", (20, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 255), 4)
        tiles.append(cv2.resize(im, (960, 540)))
    cv2.imwrite(f"bench/best_{best}.jpg",
                np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])]))


if __name__ == "__main__":
    main()
