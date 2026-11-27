from __future__ import annotations

from typing import Any


def record_track_quality(metrics: dict[str, Any], tracker: Any, live: list) -> None:
    """Accumulate the two numbers that expose identity fragmentation.

    Neither needs ground-truth labels, which is what makes them usable as an
    A/B gate. When re-identification starts failing, the tracker gives up on
    tracks and mints fresh ids for the same people: tracks_created climbs
    while the average lifetime drops. A change that buys throughput by
    quietly degrading identity shows up here even though the alert count and
    the frame rate both still look fine.

    Both updates are O(1) per frame, so this stays out of the way of what it
    is measuring.
    """
    created = int(getattr(tracker, "_next_id", 0))
    metrics["tracks_created"] = created
    total = metrics.get("track_frames_total", 0) + len(live)
    metrics["track_frames_total"] = total
    metrics["track_lifetime_frames_avg"] = (total / created) if created else 0.0
