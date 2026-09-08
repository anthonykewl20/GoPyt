"""Bounded, fail-closed token buckets. Expiry never grants unearned tokens."""
import hashlib
from dataclasses import dataclass


@dataclass
class Bucket:
    level: float
    last: float
    tokens: int
    refill_ms: int

    def available(self, now):
        return min(float(self.tokens), self.level + max(0, now - self.last) * self.tokens * 1000 / self.refill_ms)


class Limiter:
    def __init__(self, capacity=4096):
        self.capacity = capacity
        self.buckets = {}
        self.next_expiry = float("inf")

    def allow(self, key, tokens, refill_ms, now):
        key = hashlib.sha256(key.encode('utf-8')).digest()
        bucket = self.buckets.get(key)
        if bucket is not None and bucket.available(now) >= bucket.tokens:
            del self.buckets[key]
            bucket = None
        if bucket is None:
            if len(self.buckets) >= self.capacity:
                if now < self.next_expiry:
                    return False
                expired = [k for k, b in self.buckets.items() if b.available(now) >= b.tokens]
                for k in expired:
                    del self.buckets[k]
                self.next_expiry = min((b.last + (b.tokens - b.level) * b.refill_ms / (1000 * b.tokens)
                                        for b in self.buckets.values()), default=float("inf"))
            if len(self.buckets) >= self.capacity:
                return False
            bucket = Bucket(float(tokens), now, tokens, refill_ms)
            self.buckets[key] = bucket
        if (bucket.tokens, bucket.refill_ms) != (tokens, refill_ms):
            return False
        level = bucket.available(now)
        allowed = level >= 1.0
        bucket.level = level - 1 if allowed else level
        bucket.last = now
        # A lower bound is safe: consuming a token can only postpone expiry.
        # Under pressure, no full scan runs before any entry could be full.
        expiry = now + (bucket.tokens - bucket.level) * bucket.refill_ms / (1000 * bucket.tokens)
        self.next_expiry = min(self.next_expiry, expiry)
        return allowed
