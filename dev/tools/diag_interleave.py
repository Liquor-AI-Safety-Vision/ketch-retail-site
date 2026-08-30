"""Does interleaving three models cost more than running each on its own?

Every simpler explanation is now out. Pose costs 25ms inside a lane and
8.3ms standalone, and it is not contention (a single lane alone shows the
same 25ms), not the shared-memory input (measured identical), not the batch
runner, and not the native thread pools (already fixed).

What is left is the one structural difference nobody has measured: a lane
runs pose, then ReID, then till, over and over, on one CUDA context. Every
standalone benchmark ran a single model in a tight loop, where its kernels,
workspace and batch-runner thread all stay hot. Interleaving three sessions
may be paying to swap between them on every frame.

So this runs the exact same calls in three increasingly lane-like loops and
reports each component's cost in each:

  A  pose only
  B  pose then ReID
  C  pose then ReID then till          (what a lane actually does)

If pose is 8ms in A and 25ms in C, the cost is the interleaving itself, and
the fix is about how models are assigned to processes -- one model per
process, or one context per model -- not about any single model.

    python backend/tools/diag_interleave.py
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_REPO = _Path(__file__).resolve().parents[2]
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = _Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "scripts", backend_root() / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from backend.src.runtime.perf import cap_native_threads  # noqa: E402
from backend.src.common.paths import model_path

cap_native_threads(1)

POSE_MODEL = model_path("yolo26m-pose.onnx")
REID_MODEL = model_path("osnet_x0_25_msmt17.onnx")
TILL_MODEL = model_path("till_seg_v4_640.onnx")
REPS = 40
ROUNDS = 2


def main() -> None:
    from diag_infer_modules import _real_frames

    from backend.src.runtime.shared_inference import SharedInferenceRegistry

    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ort-intra", type=int, default=0,
                    help="onnxruntime intra-op threads per session; 0 leaves the default, "
                         "which is the host core count and ignores OMP_NUM_THREADS entirely")
    ap.add_argument("--rounds", type=int, default=ROUNDS)
    args = ap.parse_args()

    from backend.src.runtime.perf import cpu_quota

    print(f"  ort_intra_threads={args.ort_intra or 'default (host core count)'}  "
          f"cpu quota={cpu_quota()}", file=sys.stderr)

    frames = _real_frames(8)
    registry = SharedInferenceRegistry(cpu_only=False, config={
        "pose_num_workers": 1, "pose_max_batch": 1, "pose_batch_wait_s": 0.005,
        "pose_pad_batch": True,
        "till_num_workers": 1, "till_max_batch": 1, "till_batch_wait_s": 0.005,
        "ort_intra_threads": args.ort_intra})
    try:
        pose = registry.get_pose(POSE_MODEL)
        till = registry.get_till(TILL_MODEL, imgsz=640)

        from backend.src.perception import stream_track as S

        S.REID_MODEL = REID_MODEL
        S.ORT_PROVIDERS = None
        reid = S.Embedder()

        blobs, metas = [], []
        for f in frames:
            b, m = pose.preprocess(f)
            blobs.append(b)
            metas.append(m)

        def loop(use_reid: bool, use_till: bool, reps: int):
            acc = defaultdict(float)
            t_all = time.perf_counter()
            for i in range(reps):
                k = i % len(frames)
                t = time.perf_counter()
                result = pose.detect_preprocessed(blobs[k], conf=0.5, **metas[k])
                acc["pose"] += time.perf_counter() - t
                if use_reid:
                    t = time.perf_counter()
                    reid(frames[k], result.boxes)
                    acc["reid"] += time.perf_counter() - t
                if use_till:
                    t = time.perf_counter()
                    till.detect(frames[k], conf=0.7)
                    acc["till"] += time.perf_counter() - t
            acc["wall"] = time.perf_counter() - t_all
            return {k: v / reps for k, v in acc.items()}

        variants = [("A  pose only", False, False),
                    ("B  pose + reid", True, False),
                    ("C  pose + reid + till", True, True)]
        for _n, r, t in variants:
            loop(r, t, 5)

        for rnd in range(args.rounds):
            print(f"  --- round {rnd + 1} ---", file=sys.stderr)
            for name, use_reid, use_till in variants:
                a = loop(use_reid, use_till, REPS)
                parts = "  ".join(f"{k}={a[k] * 1e3:6.2f}ms" for k in ("pose", "reid", "till")
                                  if k in a)
                print(f"    {name:<24} frame={a['wall'] * 1e3:7.2f}ms   {parts}", file=sys.stderr)
    finally:
        registry.close()


if __name__ == "__main__":
    main()
