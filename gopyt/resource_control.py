"""Native resource lifetime primitive; language handles and GC integration follow.

Leases are internal host scopes. Never expose their payload as a raw language or
embedding buffer. A closer must be idempotent after a partial failure; failed
cleanup keeps the reservation charged and available for explicit retry.
"""
from contextlib import contextmanager
import threading


class ResourceClosedError(Exception):
    pass


_RELEASED = object()


class ResourceControl:
    def __init__(self, payload, reservation, closer):
        if reservation.released:
            raise ValueError('live reservation required')
        if not callable(closer):
            raise TypeError('resource closer required')
        self._lock = threading.Lock()
        self._payload = payload
        self._reservation = reservation
        self._closer = closer
        self._closed = False
        self._leases = 0
        self._cleaning = False
        self._cleanup_error = None

    @contextmanager
    def lease(self):
        with self._lock:
            if self._closed:
                raise ResourceClosedError('resource is closed')
            self._leases += 1
            payload = self._payload
        try:
            yield payload
        finally:
            with self._lock:
                self._leases -= 1
            self._reap()

    def close(self):
        """Reject new access immediately; admitted access completes before release.

        Does not wait for leases. Returns true only if physical cleanup completed.
        Owners must retain this control until released, including failed cleanup.
        """
        with self._lock:
            self._closed = True
        self._reap()
        return self.snapshot()['state'] == 'released'

    def _reap(self):
        with self._lock:
            if (not self._closed or self._leases or self._cleaning
                    or self._payload is _RELEASED):
                return
            self._cleaning = True
            payload = self._payload
        try:
            # Never invoke a closer while holding the state/heap mutator lock.
            self._closer(payload)
        except BaseException as error:
            with self._lock:
                # Retain only the error category, not traceback/payload references.
                self._cleanup_error = type(error).__name__
                self._cleaning = False
            return
        with self._lock:
            self._payload = _RELEASED
            self._closer = None
            self._cleanup_error = None
            self._cleaning = False
            self._reservation.release()

    def snapshot(self):
        with self._lock:
            state = 'released' if self._payload is _RELEASED else 'closing' if self._closed else 'open'
            return {'state': state, 'leases': self._leases, 'cleaning': self._cleaning,
                    'cleanup_error': self._cleanup_error}
