"""Shared reservation ledger for native resources; not a process RSS meter.

Callers reserve before acquiring resources and retain the reservation until physical
release. This module does not itself allocate buffers, open files or unmap memory.
"""
from dataclasses import dataclass
import threading


class ResourceLimitError(Exception):
    """A reservation exceeds one or more configured resource limits.

    `fields` names the limits that were actually exceeded, so a caller can
    record which pressure refused the work without re-deriving it. It stays a
    plain tuple of field names; nothing request-supplied is carried here.
    """

    def __init__(self, message, fields=()):
        super().__init__(message)
        self.fields = tuple(fields)


@dataclass(frozen=True)
class ResourceLimits:
    native_bytes: int
    mapped_bytes: int
    descriptors: int
    handles: int

    def __post_init__(self):
        for name in ('native_bytes', 'mapped_bytes', 'descriptors', 'handles'):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= (1 << 63) - 1:
                raise ValueError('resource limits require nonnegative i64 integers')


class _Token:
    __slots__ = ('finalized',)

    def __init__(self):
        self.finalized = False


class _Reservation:
    __slots__ = ('_budget', '_token')

    def __init__(self, budget, token):
        self._budget = budget
        self._token = token

    def release(self):
        """Idempotent and thread-safe; call only after physical resource release."""
        self._budget._release(self._token)

    def finalize(self):
        """Mark physically freed payloads without locking or allocating in GC."""
        self._token.finalized = True
        self._budget._pending_finalizers = True

    def reduce(self, *, native_bytes=0, mapped_bytes=0, descriptors=0, handles=0):
        """Atomically reduce an admitted reservation after unused capacity is known."""
        self._budget._reduce(self._token, ResourceLimits(
            native_bytes, mapped_bytes, descriptors, handles))

    @property
    def released(self):
        with self._budget._lock:
            self._budget._drain_finalizers_locked()
            return self._token not in self._budget._active

    def __enter__(self):
        if self.released:
            raise ValueError('reservation already released')
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        return False


class ResourceBudget:
    """Atomic shared budget. Parallel contexts share this exact ledger instance.

    Dimensions are charged independently. A failed multidimensional reservation
    changes no usage. Limits are immutable; no process-global policy is inferred.
    Host owners must explicitly release reservations on every failed constructor
    and close path. Dropping a Python reference is not physical resource release.
    """
    _names = ('native_bytes', 'mapped_bytes', 'descriptors', 'handles')

    def __init__(self, limits):
        if not isinstance(limits, ResourceLimits):
            raise TypeError('explicit ResourceLimits required')
        self._limits = limits
        self._lock = threading.Lock()
        self._used = {name: 0 for name in self._names}
        self._peak = dict(self._used)
        self._active = {}
        self._rejected = 0
        self._pending_finalizers = False

    @property
    def limits(self):
        return self._limits

    def reserve(self, *, native_bytes=0, mapped_bytes=0, descriptors=0, handles=0):
        amounts = ResourceLimits(native_bytes, mapped_bytes, descriptors, handles)
        token = _Token()
        # Construct before admission, so allocation failure cannot strand a charge.
        reservation = _Reservation(self, token)
        with self._lock:
            self._drain_finalizers_locked()
            exceeded = tuple(name for name in self._names
                             if getattr(amounts, name)
                             > getattr(self._limits, name) - self._used[name])
            if exceeded:
                self._rejected += 1
                raise ResourceLimitError('native resource budget exceeded', exceeded)
            updated = {name: self._used[name] + getattr(amounts, name) for name in self._names}
            peaks = {name: max(self._peak[name], updated[name]) for name in self._names}
            self._active[token] = amounts
            self._used = updated
            self._peak = peaks
        return reservation

    def _drain_finalizers_locked(self):
        # Finalizers only set existing fields. They can run during any allocation
        # below without taking this lock or overwriting a counter transaction.
        while self._pending_finalizers:
            self._pending_finalizers = False
            try:
                for token in tuple(self._active):
                    if token.finalized:
                        self._release_locked(token)
            except BaseException:
                self._pending_finalizers = True
                raise

    def _release_locked(self, token):
        amounts = self._active.get(token)
        if amounts is None:
            return
        updated = {name: self._used[name] - getattr(amounts, name) for name in self._names}
        del self._active[token]
        self._used = updated

    def _release(self, token):
        with self._lock:
            self._drain_finalizers_locked()
            self._release_locked(token)

    def _reduce(self, token, amounts):
        with self._lock:
            self._drain_finalizers_locked()
            previous = self._active.get(token)
            if previous is None:
                raise ValueError('reservation already released')
            if any(getattr(amounts, name) > getattr(previous, name) for name in self._names):
                raise ValueError('reservation reduction cannot increase capacity')
            updated = {name: self._used[name] - getattr(previous, name) + getattr(amounts, name)
                       for name in self._names}
            self._active[token] = amounts
            self._used = updated

    def snapshot(self):
        """Independent non-secret counter copies, consistent at one lock boundary."""
        with self._lock:
            self._drain_finalizers_locked()
            return {'limits': {name: getattr(self._limits, name) for name in self._names},
                    'used': dict(self._used), 'peak': dict(self._peak),
                    'active_reservations': len(self._active), 'rejected': self._rejected}
