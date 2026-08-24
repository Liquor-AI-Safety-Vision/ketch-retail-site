from __future__ import annotations

import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class TimingAccumulator:
    count: int = 0
    total_s: float = 0.0
    max_s: float = 0.0

    def add(self, elapsed_s: float):
        self.count += 1
        self.total_s += elapsed_s
        self.max_s = max(self.max_s, elapsed_s)

    def as_dict(self) -> dict[str, float | int]:
        avg_s = self.total_s / self.count if self.count else 0.0
        return {
            "count": self.count,
            "total_ms": round(self.total_s * 1000.0, 3),
            "avg_ms": round(avg_s * 1000.0, 3),
            "max_ms": round(self.max_s * 1000.0, 3),
        }


@dataclass
class TimingCollector:
    stats: dict[str, TimingAccumulator] = field(default_factory=lambda: defaultdict(TimingAccumulator))

    @contextmanager
    def track(self, key: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.stats[key].add(time.perf_counter() - started)

    def add(self, key: str, elapsed_s: float):
        self.stats[key].add(elapsed_s)

    def merge_dict(self, raw: dict[str, float]):
        for key, value in raw.items():
            self.add(key, value)

    def as_dict(self) -> dict[str, dict[str, float | int]]:
        return {key: acc.as_dict() for key, acc in sorted(self.stats.items())}
