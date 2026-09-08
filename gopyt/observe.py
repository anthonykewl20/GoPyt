"""Effect `observe`: bounded streaming telemetry (docs/hardening.md, S32).

The algorithms are ported unchanged from prototypes/observe/sketches.py, which
is the P0 calibration in docs/validation.md. Do not substitute alternatives:
Welford (1962), Count-Min (Cormode & Muthukrishnan 2005), reservoir Algorithm R
(Vitter 1985), Page CUSUM (1954) with the frozen parameters p0=0.02, k=0.01,
h=8. Memory is fixed; stream length does not grow it.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
from dataclasses import dataclass, field


U64_MAX = 2**64 - 1
FAILURE_NAMES = frozenset("Denied Throttled NotFound ConvertError DbError IoError HttpError ModelError ListenError EvolveError".split())


def compact(text):
    raw = text.encode("utf-8")
    return text if len(raw) <= 256 else "sha256:" + hashlib.sha256(raw).hexdigest()


class Bloom:
    def __init__(self):
        self.bits = bytearray(1024)

    def indices(self, key):
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        return [int.from_bytes(digest[i:i+2], "little") % 8192 for i in (0, 2, 4, 6)]

    def add(self, key):
        indices = self.indices(key)
        seen = all(self.bits[i // 8] & (1 << (i % 8)) for i in indices)
        for i in indices:
            self.bits[i // 8] |= 1 << (i % 8)
        return seen

    def contains(self, key):
        return all(self.bits[i // 8] & (1 << (i % 8)) for i in self.indices(key))


class Welford:
    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0

    def add(self, x: float) -> None:
        self.n = min(U64_MAX, self.n + 1)
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
            i = self._idx(key, r)
            self.table[r][i] = min(U64_MAX, self.table[r][i] + n)

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
        self.seen = min(U64_MAX, self.seen + 1)
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
    bloom: Bloom = field(default_factory=Bloom)
    cusum: Cusum = field(default_factory=Cusum)
    reservoir: Reservoir = field(init=False)
    events: int = 0
    fail: int = 0

    def __post_init__(self) -> None:
        self.reservoir = Reservoir(self.reservoir_k)
        # The VM updates these from parallel arms and HTTP handler threads. The
        # algorithms are the P0 port unchanged; only mutual exclusion is added,
        # so "the reservoir never exceeds K" holds under concurrency too.
        self.lock = threading.Lock()

    def event(self, tag: str, fail: bool, ms: float, task: str = "", code: int = 0) -> None:
        tag, task = compact(tag), compact(task)
        with self.lock:
            self.events = min(U64_MAX, self.events + 1)
            if fail:
                self.fail = min(U64_MAX, self.fail + 1)
            self.welford.add(ms)
            self.cms.add(tag)
            self.bloom.add(tag)
            self.cusum.add(fail)
            if fail:
                self.reservoir.add(json.dumps({"task": task, "tag": tag, "code": code}, separators=(",", ":")))

    def memory_cells(self) -> int:
        return self.cms.depth * self.cms.width + self.reservoir.k + 8 + len(self.bloom.bits)

    # -- VM edges (docs/hardening.md: recorded without agent-written logs) --

    def task(self, name: str) -> None:
        with self.lock:
            self.cms.add("task:" + name)

    def trap(self, code: int, name: str) -> None:
        self.event(f"trap:{code}:{name}", True, 0.0, name, code)

    def http(self, route: str, outcome: str, ms: float) -> None:
        self.event(f"http:{route}:{outcome}", outcome != "ok", ms)

    def note(self, tag: str) -> None:
        self.event("note:" + tag, False, 0.0)

    def deny(self, kind: str) -> None:
        self.event("deny:" + kind, True, 0.0)

    def outcome(self, task, tag, ms):
        self.event("outcome:" + tag, tag.rsplit(".", 1)[-1] in FAILURE_NAMES, ms, task)

    def snapshot(self):
        with self.lock:
            return [json.loads(row) for row in self.reservoir.items]

    def dump(self, root):
        from gopyt.files import atomic_write
        data = json.dumps({"version": 1, "rows": self.snapshot()}, separators=(",", ":")).encode()
        try:
            atomic_write(root, "build/traces", data, create_parents=True)
        except OSError:
            return False
        return True


def replay(rows):
    """Tag-only contract-trap replay: no application effects or payloads."""
    detector = Cusum()
    peak = 0.0
    for row in rows:
        detector.add(row.get("code") in (1, 2))
        peak = max(peak, detector.s)
    return peak
