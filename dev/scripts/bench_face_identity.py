"""Quick benchmark for detect + align + ArcFace on storage footage.

Runs the three checks needed before wiring identity into the warehouse path:

  1. identical crop vs itself
  2. same person across nearby frames
  3. different people across different footage

The default timestamps are the strongest face detections measured on the sample
storage clips already in this repo.
"""
import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[2]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import argparse
import os

import cv2

from backend.src.perception.face_align import FaceDetector, align
from backend.src.perception.face_identity import ArcFaceEmbedder

HERE = str(_Path(__file__).resolve().parents[2])


def best_face_at(video_path, t_sec, det):
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t_sec * fps)))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"cannot read {video_path} at {t_sec}s")
    faces = det(frame)
    if not faces:
        raise SystemExit(f"no face detected in {video_path} at {t_sec}s")
    box, score, lm = max(faces, key=lambda rec: rec[1])
    chip = align(frame, lm, size=112)
    if chip is None:
        raise SystemExit(f"align failed in {video_path} at {t_sec}s")
    return chip, float(score)


def cosine(a, b):
    return float((a * b).sum())


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--face-model", default=f"{HERE}/backend/models/yolov6s_face.onnx")
    p.add_argument("--embed-model", default=f"{HERE}/backend/models/ms1mv3_arcface_r18.onnx")
    p.add_argument("--storage-1", default=f"{HERE}/data/videos/cctv_storage/storage_1.mp4")
    p.add_argument("--storage-2", default=f"{HERE}/data/videos/cctv_storage/storage_2.mp4")
    p.add_argument("--t1", type=float, default=154.4,
                   help="storage_1 timestamp for same-person sample A")
    p.add_argument("--t2", type=float, default=174.4,
                   help="storage_1 timestamp for same-person sample B")
    p.add_argument("--t3", type=float, default=64.0,
                   help="storage_2 timestamp for different-person sample")
    p.add_argument("--out-dir", default=f"{HERE}/dev/outputs/face_bench")
    p.add_argument("--cpu", action="store_true",
                   help="force CPUExecutionProvider for reproducible local testing")
    a = p.parse_args()

    providers = ["CPUExecutionProvider"] if a.cpu else None
    det = FaceDetector(a.face_model, conf=0.20, providers=providers,
                       prefer_coreml=not a.cpu)
    emb = ArcFaceEmbedder(a.embed_model, providers=providers,
                          prefer_coreml=not a.cpu)

    os.makedirs(a.out_dir, exist_ok=True)
    s1_a, sc1 = best_face_at(a.storage_1, a.t1, det)
    s1_b, sc2 = best_face_at(a.storage_1, a.t2, det)
    s2_a, sc3 = best_face_at(a.storage_2, a.t3, det)
    cv2.imwrite(f"{a.out_dir}/storage1_a.jpg", s1_a)
    cv2.imwrite(f"{a.out_dir}/storage1_b.jpg", s1_b)
    cv2.imwrite(f"{a.out_dir}/storage2_a.jpg", s2_a)

    e1, e2, e3 = emb(s1_a), emb(s1_b), emb(s2_a)
    print(f"storage_1 @{a.t1:.1f}s  face score={sc1:.3f}")
    print(f"storage_1 @{a.t2:.1f}s  face score={sc2:.3f}")
    print(f"storage_2 @{a.t3:.1f}s  face score={sc3:.3f}")
    print(f"identical  storage1_a vs itself : {cosine(e1, e1):.4f}")
    print(f"near-same  storage1_a vs storage1_b : {cosine(e1, e2):.4f}")
    print(f"different  storage1_a vs storage2_a : {cosine(e1, e3):.4f}")
    print(f"saved chips in {a.out_dir}")


if __name__ == "__main__":
    main()
