"""Render proposed crossing lines over the traffic maps, so placement can be
judged against where people actually walk rather than against one still frame."""
from pathlib import Path as _Path
import os

import cv2
import numpy as np

ROOT = str(_Path(__file__).resolve().parents[2])

PROPOSALS = {
    # name: (points, label for the side the reference pixel is on, ref pixel)
    "storage_1": dict(
        pts=[(1425, 0), (1470, 560), (1495, 1080)],
        inside="INSIDE cold storage", outside="OUT (sales floor)",
        ref=(200, 540),
        note="at the cooler door: the frame edge runs (1425,0) -> (1495,1080)"),
    "storage_2": dict(
        pts=[(0, 395), (960, 420), (1920, 465)],
        inside="INSIDE (deeper in / toward the far door)",
        outside="OUT (toward camera)",
        ref=(960, 100),
        note="across the corridor, far enough back that people are still large"),
}

for name, P in PROPOSALS.items():
    img = cv2.imread(f"{ROOT}/dev/outputs/{name}/{name}_traffic.jpg")
    if img is None:
        raise SystemExit(f"{name}_traffic.jpg missing - run traffic_map.py first")
    H, W = img.shape[:2]
    p = np.array(P["pts"], np.float32)

    # shade the two half-planes using the same nearest-segment rule zones.py uses
    a, d = p[:-1], p[1:] - p[:-1]
    ln = np.linalg.norm(d, axis=1)
    yy, xx = np.mgrid[0:H, 0:W]
    dists = np.stack([((xx - a[i, 0]) * d[i, 1] - (yy - a[i, 1]) * d[i, 0]) / ln[i]
                      for i in range(len(a))])
    sd = np.take_along_axis(dists, np.abs(dists).argmin(0)[None], 0)[0]
    ref_sign = np.sign(sd[int(P["ref"][1]), int(P["ref"][0])])

    ov = img.copy()
    ov[np.sign(sd) == ref_sign] = (70, 200, 70)
    ov[np.sign(sd) != ref_sign] = (60, 130, 255)
    out = cv2.addWeighted(ov, 0.22, img, 0.78, 0)

    # extend past the borders, the way the rule uses it
    q = p.copy()
    for i, j in ((0, 1), (-1, -2)):
        v = q[i] - q[j]
        q[i] = q[i] + v / np.linalg.norm(v) * (W + H)
    cv2.polylines(out, [q.astype(np.int32)], False, (255, 255, 255), 6)
    for x, y in P["pts"]:
        cv2.circle(out, (int(x), int(y)), 12, (255, 255, 255), -1)
        cv2.circle(out, (int(x), int(y)), 12, (0, 0, 0), 3)
        cv2.putText(out, f"({x},{y})", (int(x) + 18, int(y) - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 0, 0), 5)
        cv2.putText(out, f"({x},{y})", (int(x) + 18, int(y) - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2)

    for txt, col, pos in ((P["inside"], (70, 220, 70), P["ref"]),
                          (P["outside"], (60, 150, 255), None)):
        if pos is None:
            continue
        cv2.putText(out, txt, (int(pos[0]) - 60, int(pos[1])),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 8)
        cv2.putText(out, txt, (int(pos[0]) - 60, int(pos[1])),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, col, 3)

    bar = np.zeros((70, W, 3), np.uint8)
    cv2.putText(bar, f"{name}: {P['note']}", (20, 46),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (240, 240, 240), 2)
    cv2.imwrite(f"{ROOT}/dev/outputs/{name}/{name}_proposal.jpg",
                cv2.resize(np.vstack([out, bar]), (1500, int(1500 * (H + 70) / W))))
    print(f"{name}: ref pixel {P['ref']} -> sign {ref_sign:+.0f}, "
          f"green area {100*(np.sign(sd) == ref_sign).mean():.0f}%")
