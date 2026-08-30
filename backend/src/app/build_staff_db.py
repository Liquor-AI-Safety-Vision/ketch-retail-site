from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.src.perception.face_align import FaceDetector, align  # noqa: E402
from backend.src.perception.face_identity import FaceIdentifier  # noqa: E402

from backend.src.infra.local_config import load_staff_seed_entries
from backend.src.common.paths import backend_root, model_path


def best_face_in_crop(detector, frame, crop_box, center_t, fps, search_window_s):
    x1, y1, x2, y2 = crop_box
    best = None
    best_key = None
    for dt in np.arange(-search_window_s, search_window_s + 1e-9, 0.25):
        t = center_t + float(dt)
        if t < 0:
            continue
        frame_idx = max(0, int(round(t * fps)))
        yield ("seek", frame_idx)


def find_best_face(detector, cap, center_t, crop_box, fps, search_window_s):
    x1, y1, x2, y2 = crop_box
    best = None
    best_key = None
    for dt in np.arange(-search_window_s, search_window_s + 1e-9, 0.25):
        t = center_t + float(dt)
        if t < 0:
            continue
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(t * fps))))
        ok, frame = cap.read()
        if not ok:
            continue
        roi = frame[y1:y2, x1:x2]
        faces = detector(roi)
        for box, score, landmarks in faces:
            area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
            chip = align(roi, landmarks, size=112)
            if chip is None:
                continue
            key = (float(score), float(area))
            if best is None or key > best_key:
                best = {
                    "t": t,
                    "score": float(score),
                    "box": [float(v) for v in box.tolist()],
                    "chip_bgr": chip,
                    "roi_bgr": roi.copy(),
                }
                best_key = key
    return best


def main():
    p = argparse.ArgumentParser(description=__doc__ or "Build local dev staff SQLite DB")
    p.add_argument(
        "--seed-config",
        default=str(backend_root() / "config/staff_seed/local_dev_staff.json"),
    )
    p.add_argument(
        "--face-model",
        default=model_path("yolov6s_face.onnx"),
    )
    p.add_argument(
        "--embed-model",
        default=model_path("ms1mv3_arcface_r18.onnx"),
    )
    p.add_argument("--cpu", action="store_true", default=True)
    a = p.parse_args()

    db_path, artifacts_dir, entries = load_staff_seed_entries(a.seed_config)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(artifacts_dir, exist_ok=True)

    providers = ["CPUExecutionProvider"] if a.cpu else None
    detector = FaceDetector(
        a.face_model, conf=0.2, providers=providers, prefer_coreml=not a.cpu
    )
    identifier = FaceIdentifier(
        a.face_model,
        a.embed_model,
        db_path,
        providers=providers,
        prefer_coreml=not a.cpu,
        match_threshold=0.3,
        match_margin=0.1,
    )

    for entry in entries:
        cap = cv2.VideoCapture(entry.video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        best = find_best_face(
            detector,
            cap,
            center_t=entry.timestamp_s,
            crop_box=entry.crop_box_xyxy,
            fps=fps,
            search_window_s=entry.search_window_s,
        )
        cap.release()
        if best is None:
            raise SystemExit(f"no face found for {entry.person_id}")

        chip_path = os.path.join(artifacts_dir, f"{entry.person_id}_chip.jpg")
        roi_path = os.path.join(artifacts_dir, f"{entry.person_id}_roi.jpg")
        dbg = best["roi_bgr"].copy()
        box = np.array(best["box"]).astype(int)
        cv2.rectangle(dbg, (box[0], box[1]), (box[2], box[3]), (0, 255, 0), 2)
        cv2.putText(
            dbg,
            f"t={best['t']:.2f}s s={best['score']:.2f}",
            (5, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )
        cv2.imwrite(chip_path, best["chip_bgr"])
        cv2.imwrite(roi_path, dbg)

        metadata = dict(entry.metadata)
        metadata.update(
            {
                "company_id": entry.company_id,
                "store_id": entry.store_id,
                "seed_video_path": entry.video_path,
                "seed_timestamp_s": entry.timestamp_s,
                "selected_timestamp_s": best["t"],
                "selected_face_score": best["score"],
                "crop_box_xyxy": list(entry.crop_box_xyxy),
            }
        )
        identifier.register(
            entry.person_id,
            entry.display_name,
            best["chip_bgr"],
            detector_score=best["score"],
            image_path=chip_path,
            source="backend/src/app/build_staff_db.py",
            is_staff=entry.is_staff,
            company_id=entry.company_id,
            store_id=entry.store_id,
            metadata=metadata,
        )
        print(
            f"seeded {entry.person_id} ({entry.display_name}) "
            f"from t={best['t']:.2f}s score={best['score']:.3f}"
        )

    print(f"sqlite db: {db_path}")
    print(f"artifacts: {artifacts_dir}")


if __name__ == "__main__":
    main()
