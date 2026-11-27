"""YOLO26n-pose ONNX inference, pure onnxruntime + OpenCV (no ultralytics).

Output layout: (1, 300, 57) — NMS-free, already sorted by score.
  [0:4]  box xyxy (letterboxed 640x640 space)
  [4]    score
  [5]    class id (person = 0)
  [6:57] 17 keypoints * (x, y, visibility)
"""
import cv2
import numpy as np
import onnxruntime as ort

MODEL = "yolo26n-pose.onnx"
IMGSZ = 640
CONF = 0.25

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


def main(path="bus.jpg"):
    img = cv2.imread(path)
    lb, r, dx, dy = letterbox(img)

    blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
    blob = np.ascontiguousarray(blob, np.float32)[None] / 255.0

    sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    out = sess.run(None, {sess.get_inputs()[0].name: blob})[0][0]  # (300, 57)

    dets = out[out[:, 4] >= CONF]
    print(f"{len(dets)} detections")

    for d in dets:
        # undo letterbox back to original image coords
        x1, y1, x2, y2 = (d[:4] - [dx, dy, dx, dy]) / r
        kpts = d[6:].reshape(17, 3).copy()
        kpts[:, 0] = (kpts[:, 0] - dx) / r
        kpts[:, 1] = (kpts[:, 1] - dy) / r

        cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
        cv2.putText(img, f"{d[4]:.2f}", (int(x1), int(y1) - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        for x, y, v in kpts:
            if v > 0.5:
                cv2.circle(img, (int(x), int(y)), 3, (0, 0, 255), -1)
        for a, b in SKELETON:
            if kpts[a, 2] > 0.5 and kpts[b, 2] > 0.5:
                cv2.line(img, tuple(kpts[a, :2].astype(int)),
                         tuple(kpts[b, :2].astype(int)), (255, 128, 0), 2)

    cv2.imwrite("out_onnx.jpg", img)
    print("saved out_onnx.jpg")


if __name__ == "__main__":
    main()
