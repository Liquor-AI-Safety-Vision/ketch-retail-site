"""Register one face into the local SQLite gallery.

Input can be either:
  - a plain image file containing one face, or
  - a video + timestamp, from which the strongest face at that moment is taken.
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
from backend.src.perception.face_identity import FaceIdentifier

HERE = str(_Path(__file__).resolve().parents[2])


def best_face(frame, det):
    faces = det(frame)
    if not faces:
        raise SystemExit("no face detected")
    box, score, lm = max(faces, key=lambda rec: rec[1])
    chip = align(frame, lm, size=112)
    if chip is None:
        raise SystemExit("face alignment failed")
    return chip, float(score)


def load_frame(image_path=None, video_path=None, t_sec=None):
    if image_path:
        frame = cv2.imread(image_path)
        if frame is None:
            raise SystemExit(f"cannot read image {image_path}")
        return frame
    if not video_path:
        raise SystemExit("need either --image or --video")
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if t_sec is not None:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t_sec * fps)))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"cannot read {video_path} at {t_sec}s")
    return frame


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--db", required=True)
    p.add_argument("--person-id", required=True)
    p.add_argument("--display-name", required=True)
    p.add_argument("--image")
    p.add_argument("--video")
    p.add_argument("--time", type=float)
    p.add_argument("--face-model", default=f"{HERE}/backend/models/yolov6s_face.onnx")
    p.add_argument("--embed-model", default=f"{HERE}/backend/models/ms1mv3_arcface_r18.onnx")
    p.add_argument("--out-chip")
    p.add_argument("--cpu", action="store_true")
    p.add_argument("--non-staff", action="store_true")
    p.add_argument("--company-id")
    p.add_argument("--store-id")
    a = p.parse_args()

    providers = ["CPUExecutionProvider"] if a.cpu else None
    det = FaceDetector(a.face_model, conf=0.20, providers=providers,
                       prefer_coreml=not a.cpu)
    ident = FaceIdentifier(
        a.face_model, a.embed_model, a.db, providers=providers,
        prefer_coreml=not a.cpu)

    frame = load_frame(image_path=a.image, video_path=a.video, t_sec=a.time)
    chip, score = best_face(frame, det)
    ident.register(
        a.person_id, a.display_name, chip, detector_score=score,
        image_path=a.image or a.video, source="register_face_in_gallery",
        is_staff=not a.non_staff,
        company_id=a.company_id,
        store_id=a.store_id,
        metadata={"time_sec": a.time})

    if a.out_chip:
        os.makedirs(os.path.dirname(os.path.abspath(a.out_chip)), exist_ok=True)
        cv2.imwrite(a.out_chip, chip)
    print(f"registered {a.person_id} ({a.display_name}) score={score:.3f}")
    if a.out_chip:
        print(f"saved {a.out_chip}")


if __name__ == "__main__":
    main()
