"""YOLO26n-pose ONNX inference on video, downsampled to a target FPS.

Source is 13.093 fps; at TARGET_FPS=10 we keep ~76% of frames using an
accumulator so the drop pattern stays even (no drift over 111s).
"""
import time

import cv2
import numpy as np
import onnxruntime as ort

MODEL = "yolo26n-pose.onnx"
VIDEO = "cctv_test.mp4"
OUT = "cctv_out.mp4"
IMGSZ = 640
CONF = 0.25
TARGET_FPS = 10.0

SKELETON = [(15, 13), (13, 11), (16, 14), (14, 12), (11, 12), (5, 11), (6, 12),
            (5, 6), (5, 7), (6, 8), (7, 9), (8, 10), (1, 2), (0, 1), (0, 2),
            (1, 3), (2, 4), (3, 5), (4, 6)]


def letterbox(im, size=IMGSZ):
    h, w = im.shape[:2]
    r = min(size / h, size / w)
    nh, nw = round(h * r), round(w * r)
    pad_t, pad_l = (size - nh) // 2, (size - nw) // 2
    out = np.full((size, size, 3), 114, np.uint8)
    out[pad_t:pad_t + nh, pad_l:pad_l + nw] = cv2.resize(im, (nw, nh))
    return out, r, pad_l, pad_t


def draw(img, dets, r, dx, dy):
    for d in dets:
        x1, y1, x2, y2 = (d[:4] - [dx, dy, dx, dy]) / r
        kpts = d[6:].reshape(17, 3).copy()
        kpts[:, 0] = (kpts[:, 0] - dx) / r
        kpts[:, 1] = (kpts[:, 1] - dy) / r

        cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
        cv2.putText(img, f"{d[4]:.2f}", (int(x1), int(y1) - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        for x, y, v in kpts:
            if v > 0.5:
                cv2.circle(img, (int(x), int(y)), 3, (0, 0, 255), -1)
        for a, b in SKELETON:
            if kpts[a, 2] > 0.5 and kpts[b, 2] > 0.5:
                cv2.line(img, tuple(kpts[a, :2].astype(int)),
                         tuple(kpts[b, :2].astype(int)), (255, 128, 0), 2)


def main():
    cap = cv2.VideoCapture(VIDEO)
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"source: {w}x{h} @ {src_fps:.3f} fps, {total} frames")

    sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0].name

    writer = cv2.VideoWriter(OUT, cv2.VideoWriter_fourcc(*"mp4v"), TARGET_FPS, (w, h))

    step = TARGET_FPS / src_fps  # frames to emit per source frame
    acc = 0.0
    n_read = n_proc = n_det = 0
    t_infer = 0.0
    t0 = time.perf_counter()

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        n_read += 1
        acc += step
        if acc < 1.0:
            continue  # drop this frame to hit target fps
        acc -= 1.0

        lb, r, dx, dy = letterbox(frame)
        blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
        blob = np.ascontiguousarray(blob, np.float32)[None] / 255.0

        ti = time.perf_counter()
        out = sess.run(None, {inp: blob})[0][0]
        t_infer += time.perf_counter() - ti

        dets = out[out[:, 4] >= CONF]
        n_det += len(dets)
        draw(frame, dets, r, dx, dy)
        writer.write(frame)
        n_proc += 1

        if n_proc % 100 == 0:
            print(f"  {n_read}/{total} read, {n_proc} processed")

    cap.release()
    writer.release()

    wall = time.perf_counter() - t0
    print(f"\nread {n_read} frames, processed {n_proc} "
          f"({n_proc / (n_read / src_fps):.2f} fps effective)")
    print(f"detections: {n_det} total, {n_det / n_proc:.2f} avg/frame")
    print(f"inference: {t_infer / n_proc * 1000:.1f} ms/frame "
          f"= {n_proc / t_infer:.1f} fps model throughput")
    print(f"wall clock: {wall:.1f}s for {n_read / src_fps:.1f}s of video "
          f"({(n_read / src_fps) / wall:.2f}x realtime)")
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
