from __future__ import annotations

import os


def cap_native_threads(num_threads: int = 1):
    value = str(max(1, int(num_threads)))
    for key in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ[key] = value

    try:
        import cv2  # noqa: WPS433

        cv2.setNumThreads(int(value))
        cv2.ocl.setUseOpenCL(False)
    except Exception:
        pass
