"""Native thread-pool caps, and the import-order rule that makes them work.

OpenBLAS, OpenMP and MKL size their thread pools once, when the library is
first loaded, from the environment. Setting OMP_NUM_THREADS after numpy or
onnxruntime is already imported changes nothing: the pool exists by then and
never re-reads the variable. That is exactly what used to happen here --
cap_native_threads(1) ran at the top of each process entry point, but the
entry point's own module imported numpy first, so every cap was a no-op.

It cost about five times the runtime. This container reports 192 CPUs to
os.cpu_count() while its cgroup quota is 23 cores, so the pools sized
themselves at 192 threads on 23 cores' worth of CPU, and OpenBLAS spins
before sleeping. A tiny mask matmul in till's postprocess would leave dozens
of threads spinning, which then stole the CPU that onnxruntime's next call
needed: measured 60.08ms per till call against 11.67ms with the caps
actually applied, and 16.65 calls/s against 85.68.

So the rule is: call cap_native_threads() before importing numpy, cv2 or
onnxruntime -- in practice, as the first thing an entry point does. Spawned
children inherit os.environ as it stood when the parent started them, so a
parent that caps early enough fixes every child too, before the child's
interpreter has loaded anything.

cpu_quota() exists because os.cpu_count() is the wrong number to size
anything by in a container: it reports the host's cores, not the ones this
process may use.
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

_THREAD_ENV_KEYS = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)

# The libraries whose pools are fixed at load time. If any of these is
# already imported, setting the variables can no longer resize its pool.
_POOL_OWNING_MODULES = ("numpy", "cv2", "onnxruntime", "torch")


def cpu_quota(default: int = 1) -> int:
    """How many cores this process may actually use.

    Prefers the scheduler affinity mask, then the cgroup v2 / v1 quota, and
    only falls back to os.cpu_count() -- which on this container reports the
    host's 192 rather than the 23 the cgroup allows.
    """
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        pass

    for path, parse in (
        ("/sys/fs/cgroup/cpu.max", lambda t: t.split()),
        ("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", None),
    ):
        try:
            text = open(path).read().strip()
        except OSError:
            continue
        try:
            if parse is not None:
                quota, period = parse(text)
                if quota != "max":
                    return max(1, int(float(quota) / float(period)))
            else:
                quota = int(text)
                period = int(open("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read().strip())
                if quota > 0:
                    return max(1, quota // period)
        except (ValueError, OSError):
            continue

    return max(default, os.cpu_count() or default)


def cap_native_threads(num_threads: int = 1, *, warn: bool = True) -> bool:
    """Cap the native pools. Returns False if it was called too late to work.

    Returning a bool rather than failing loudly is deliberate: a late call
    still sets the environment that spawned children will inherit, so it is
    worth doing even when this process can no longer benefit. But a silent
    no-op is what hid a 5x slowdown for as long as it did, so by default this
    says so on stderr.
    """
    value = str(max(1, int(num_threads)))
    already_loaded = [m for m in _POOL_OWNING_MODULES if m in sys.modules]
    stale = already_loaded and any(os.environ.get(k) != value for k in _THREAD_ENV_KEYS)

    for key in _THREAD_ENV_KEYS:
        os.environ[key] = value

    try:
        import cv2  # noqa: WPS433

        # OpenCV is the exception: it exposes a runtime setter, so this one
        # takes effect no matter when it is called.
        cv2.setNumThreads(int(value))
        cv2.ocl.setUseOpenCL(False)
    except Exception:
        pass

    if stale and warn:
        logger.warning(
            "cap_native_threads(%s) ran after %s was already imported: the BLAS/OpenMP "
            "pools are already sized and will not shrink in this process. Children "
            "spawned from here will still inherit the cap.",
            value, ", ".join(already_loaded),
        )
    return not stale
