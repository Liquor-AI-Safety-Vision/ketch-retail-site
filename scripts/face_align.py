"""YOLOv6s-face (ONNX) detection + 5-point landmark alignment.

(1, 8400, 16) = box_xywh(4) + landmarks(10, 5 points x,y) + cls_conf(1) +
obj_conf(1), boxes and landmarks already decoded to letterboxed pixel space
by the graph itself (checked against the exported nodes: the last layers are
Add/Mul/Slice grid-decode ops, not raw head output). Column order verified
empirically on this export, not assumed from a repo: cls_conf (index 14)
saturates at 1.0 on every anchor of every test frame - a single-class model
whose classification branch has nothing to discriminate - so index 15,
which actually varies, is the real objectness score. This head is not
NMS-free like the pose/till models in this repo, so NMS runs here.

Alignment warps the 5 points onto the fixed ArcFace 112x112 reference
template with a similarity transform, the standard way to make a face crop
comparable across pose/scale before any embedding step - here it's just what
makes the corner thumbnail look like a face crop and not an arbitrary box.
"""
import cv2
import numpy as np
import onnxruntime as ort
import sys

IMGSZ = 640
ARCFACE_112 = np.array([
    [38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
    [41.5493, 92.3655], [70.7299, 92.2041]], np.float32)


def letterbox(im, size=IMGSZ):
    h, w = im.shape[:2]
    r = min(size / h, size / w)
    nh, nw = round(h * r), round(w * r)
    pad_t, pad_l = (size - nh) // 2, (size - nw) // 2
    out = np.full((size, size, 3), 114, np.uint8)
    out[pad_t:pad_t + nh, pad_l:pad_l + nw] = cv2.resize(im, (nw, nh))
    return out, r, pad_l, pad_t


def preferred_providers(prefer_coreml=True):
    avail = ort.get_available_providers()
    out = []
    if prefer_coreml and "CoreMLExecutionProvider" in avail:
        out.append("CoreMLExecutionProvider")
    if sys.platform != "darwin" and "CUDAExecutionProvider" in avail:
        out.append("CUDAExecutionProvider")
    if "CPUExecutionProvider" in avail:
        out.append("CPUExecutionProvider")
    return out or ["CPUExecutionProvider"]


class FaceDetector:
    def __init__(self, path, imgsz=IMGSZ, conf=0.5, iou=0.45,
                 providers=None, prefer_coreml=True):
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(
            path, so,
            providers=providers or preferred_providers(prefer_coreml=prefer_coreml))
        self.inp = self.sess.get_inputs()[0].name
        self.imgsz, self.conf, self.iou = imgsz, conf, iou

    def __call__(self, frame):
        """Returns [(box_xyxy, score, landmarks(5,2))] in original frame pixels."""
        lb, r, dx, dy = letterbox(frame, self.imgsz)
        blob = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
        blob = np.ascontiguousarray(blob, np.float32)[None] / 255.0
        out = self.sess.run(None, {self.inp: blob})[0][0]        # (8400, 16)

        score = out[:, 15]
        keep = score >= self.conf
        out, score = out[keep], score[keep]
        if not len(out):
            return []

        cx, cy, w, h = out[:, 0], out[:, 1], out[:, 2], out[:, 3]
        boxes_xywh = np.stack([cx - w / 2, cy - h / 2, w, h], axis=1)
        idx = cv2.dnn.NMSBoxes(boxes_xywh.tolist(), score.tolist(),
                               self.conf, self.iou)
        if len(idx) == 0:
            return []
        idx = np.array(idx).reshape(-1)

        out_res = []
        for i in idx:
            x1, y1 = cx[i] - w[i] / 2, cy[i] - h[i] / 2
            x2, y2 = cx[i] + w[i] / 2, cy[i] + h[i] / 2
            box = (np.array([x1, y1, x2, y2]) - [dx, dy, dx, dy]) / r
            lm = out[i, 4:14].reshape(5, 2)
            lm = (lm - [dx, dy]) / r
            out_res.append((box, float(score[i]), lm))
        return out_res


def align(frame, landmarks, size=112):
    """Similarity-transform the 5 landmarks onto the ArcFace template."""
    tfm, _ = cv2.estimateAffinePartial2D(
        landmarks.astype(np.float32), ARCFACE_112, method=cv2.LMEDS)
    if tfm is None:
        return None
    return cv2.warpAffine(frame, tfm, (size, size), borderValue=0)


def best_face_in_box(faces, ltrb):
    """Face whose centre falls inside a person box, closest to its top (the
    head), or None. `faces`: output of FaceDetector.__call__."""
    x1, y1, x2, y2 = ltrb
    cand = []
    for box, score, lm in faces:
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            cand.append((cy, box, score, lm))
    if not cand:
        return None
    cand.sort(key=lambda c: c[0])
    return cand[0][1], cand[0][2], cand[0][3]
