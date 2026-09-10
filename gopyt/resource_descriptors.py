"""Internal descriptor ownership with admission before acquisition.

Owners are retained independently of language GC. An ambiguous close is never
retried by raw descriptor number; its reservation remains quarantined.
"""
import os
import threading

from gopyt.resource_control import ResourceClosedError


class DescriptorOwner:
    def __init__(self, registry):
        self._registry = registry
        self._lock = threading.Lock()
        self._reservation = None
        self._fd = None
        self._state = 'opening'
        self._error = None

    def fileno(self):
        with self._lock:
            if self._state != 'open':
                raise ResourceClosedError('descriptor is not open')
            return self._fd

    def close(self):
        with self._lock:
            if self._state == 'closed':
                return True
            if self._state in ('opening', 'closing', 'quarantined'):
                return False
            fd, self._fd = self._fd, None
            self._state = 'closing'
        try:
            if fd is not None:
                os.close(fd)
            self._reservation.release()
        except BaseException as error:
            with self._lock:
                self._error = type(error).__name__
                self._state = 'quarantined'
            return False
        with self._lock:
            self._state = 'closed'
        self._registry._remove(self)
        return True


class DescriptorRegistry:
    def __init__(self, budget):
        self._budget = budget
        self._lock = threading.Lock()
        self._owners = {}
        self._closed = False

    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        owner = DescriptorOwner(self)
        owner._reservation = self._budget.reserve(descriptors=1)
        try:
            with self._lock:
                if self._closed:
                    raise ResourceClosedError('descriptor registry is closed')
                self._owners[id(owner)] = owner
        except BaseException:
            owner._reservation.release()
            raise
        try:
            # Registration precedes acquisition. Concurrent teardown sees opening
            # and returns incomplete rather than discarding this owner.
            fd = os.open(path, flags, mode, dir_fd=dir_fd)
        except BaseException:
            with owner._lock:
                owner._state = 'empty'
            owner.close()
            raise
        with owner._lock:
            owner._fd = fd
            owner._state = 'open'
        with self._lock:
            closed = self._closed
        if closed:
            owner.close()
            raise ResourceClosedError('descriptor registry closed during acquisition')
        return owner

    def _remove(self, owner):
        with self._lock:
            self._owners.pop(id(owner), None)

    def pending(self):
        with self._lock:
            return len(self._owners)

    def close(self):
        with self._lock:
            # Prepare before changing admission: allocation failure leaves retry
            # possible and all registered owners retained.
            owners = list(self._owners.values())
            self._closed = True
        for owner in owners:
            owner.close()
        return self.pending() == 0
