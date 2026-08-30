from __future__ import annotations

from pathlib import Path as _Path

import argparse
from pathlib import Path

import onnx
from onnxruntime.transformers.float16 import convert_float_to_float16


ROOT = _Path(__file__).resolve().parents[2]

DEFAULT_MODELS = [
    "yolo26m-pose.onnx",
    "osnet_x0_25_msmt17.onnx",
    "yolov6s_face.onnx",
    "ms1mv3_arcface_r18.onnx",
    "till_seg_v4_640.onnx",
]


def convert_one(src: Path, dst: Path):
    model = onnx.load(str(src), load_external_data=False)
    model_fp16 = convert_float_to_float16(
        model,
        keep_io_types=False,
        disable_shape_infer=False,
        force_fp16_initializers=True,
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model_fp16, str(dst))


def main():
    ap = argparse.ArgumentParser(description="Convert selected ONNX models to FP16.")
    ap.add_argument("--src-root", default=str(ROOT / "backend/models"))
    ap.add_argument("--dst-root", default=str(ROOT / "backend/models_fp16"))
    ap.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="Comma-separated model filenames under --src-root.",
    )
    args = ap.parse_args()

    src_root = Path(args.src_root)
    dst_root = Path(args.dst_root)
    models = [item.strip() for item in args.models.split(",") if item.strip()]
    for name in models:
        src = src_root / name
        dst = dst_root / name
        convert_one(src, dst)
        print(f"[fp16] {src} -> {dst}")


if __name__ == "__main__":
    main()
