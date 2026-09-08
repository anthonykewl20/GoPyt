"""P0 calibration: Welford, Count-Min, Vitter reservoir, Page CUSUM.

Not an app mock. A known Bernoulli stream validates the detectors in
docs/hardening.md. Stream length does not grow memory.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field


class Welford:
    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0

    def add(self, x: float) -> None:
        self.n += 1
        d = x - self.mean
        self.mean += d / self.n
        self.m2 += d * (x - self.mean)

    def variance(self) -> float:
        if self.n < 2:
            return 0.0
        return self.m2 / (self.n - 1)


class CountMin:
    # hardening.md: width=ceil(e/0.01), depth=ceil(log(1/0.01)).
    def __init__(self, depth: int = 5, width: int = 272) -> None:
        self.depth = depth
        self.width = width
        self.table = [[0] * width for _ in range(depth)]

    def _idx(self, key: str, row: int) -> int:
        h = hashlib.sha256(f"{row}:{key}".encode()).digest()
        return int.from_bytes(h[:8], "little") % self.width

    def add(self, key: str, n: int = 1) -> None:
        for r in range(self.depth):
            self.table[r][self._idx(key, r)] += n

    def estimate(self, key: str) -> int:
        return min(self.table[r][self._idx(key, r)] for r in range(self.depth))


class Reservoir:
    def __init__(self, k: int) -> None:
        if k <= 0:
            raise ValueError("k")
        self.k = k
        self.seen = 0
        self.items: list[str] = []

    def add(self, item: str) -> None:
        self.seen += 1
        if len(self.items) < self.k:
            self.items.append(item)
            return
        j = random.randrange(self.seen)
        if j < self.k:
            self.items[j] = item


@dataclass
class Cusum:
    """Page CUSUM on Bernoulli x_t in {0,1}. Reference p0, slack k, threshold h."""

    p0: float = 0.02
    k: float = 0.01
    h: float = 8.0
    s: float = 0.0
    alarm: bool = False

    def add(self, fail: bool) -> None:
        x = 1.0 if fail else 0.0
        self.s = max(0.0, self.s + x - self.p0 - self.k)
        if self.s > self.h:
            self.alarm = True


@dataclass
class Observe:
    reservoir_k: int = 32
    welford: Welford = field(default_factory=Welford)
    cms: CountMin = field(default_factory=CountMin)
    cusum: Cusum = field(default_factory=Cusum)
    reservoir: Reservoir = field(init=False)

    def __post_init__(self) -> None:
        self.reservoir = Reservoir(self.reservoir_k)

    def event(self, tag: str, fail: bool, ms: float) -> None:
        self.welford.add(ms)
        self.cms.add(tag)
        self.cusum.add(fail)
        if fail:
            self.reservoir.add(tag)

    def memory_cells(self) -> int:
        return self.cms.depth * self.cms.width + self.reservoir.k + 8
