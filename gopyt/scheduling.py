"""VM-wide fail-fast admission for structured parallel workers."""
from contextlib import contextmanager
import threading

MAX_PARALLEL_WORKERS = 64


class ParallelBudget:
    def __init__(self, limit: int = MAX_PARALLEL_WORKERS):
        if type(limit) is not int or not 1 <= limit <= MAX_PARALLEL_WORKERS:
            raise ValueError('parallel_workers must be an integer in 1..64')
        self.limit = limit
        self.active = 0
        self.peak = 0
        self.rejected = 0
        self.lock = threading.Lock()

    @contextmanager
    def reserve(self, count: int):
        from gopyt import ops
        from gopyt.vm import Trap
        with self.lock:
            if type(count) is not int or count < 0 or self.active + count > self.limit:
                self.rejected += 1
                raise Trap(ops.TRAP_PAR_MAX)
            self.active += count
            self.peak = max(self.peak, self.active)
        try:
            yield
        finally:
            with self.lock:
                self.active -= count
