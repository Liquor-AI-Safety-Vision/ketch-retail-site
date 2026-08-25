"""RETIRED. This measured the request/reply inference proxy, which was rejected.

It is kept as a record rather than deleted, because the number it produced is
the reason the current architecture looks the way it does, and a deleted file
argues nothing.

What it asked: does a request that crosses a process boundary cost materially
more than the same call made in-process? If so, moving the models out is
paying rent on every frame.

What it found: yes, decisively. A ping with no model behind it cost 2.30ms.
The same ping against a process that merely had models loaded and idle cost
9.35ms. Three explanations were tested and ruled out -- onnxruntime's thread
count, its spin-waiting, and the caller's own CUDA context -- which left the
shape itself: blocking request/reply asks the process least able to answer
quickly, two or three times per frame, against a frame whose real cost is
about 25ms.

So the proxy was replaced by the one-way pipeline in
runtime/inference_server.py, and ClientChannel / InferenceClient /
ServerConfig / serve were deleted with it. This file imported all four, so it
has not been runnable since. It reported that as an ImportError at startup,
which is at least loud -- but a diagnostic tool that cannot start is a trap
for whoever reaches for it mid-investigation.

Where its measurements live now:

    frame transport costs         runtime/frame_ring.py, module docstring
    why one-way, not request/reply runtime/inference_server.py, module docstring
    per-model latency and batching backend/tools/diag_infer_modules.py
    per-tier throughput            backend/tools/diag_split_tiers.py

The last two are the live tools. diag_infer_modules covers everything
_bench_local here did, and covers it better: it sweeps batch size and caller
count instead of timing a single serialised path.
"""
from __future__ import annotations

import sys

MESSAGE = __doc__


def main() -> int:
    print(MESSAGE, file=sys.stderr)
    print("Nothing to run here. Use backend/tools/diag_infer_modules.py or "
          "backend/tools/diag_split_tiers.py.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
