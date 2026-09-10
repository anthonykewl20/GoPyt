"""Checked buffer/view ownership foundation; no raw buffer exports.

These internal host classes are not yet a language API. Callers retain owners until
GC/VM integration can schedule close. Returned bytes are ordinary managed values.
"""
from contextlib import contextmanager
import threading
import weakref

from gopyt.resource_control import ResourceControl, ResourceClosedError


def _range(start, length, size):
    if (type(start) is not int or type(length) is not int
            or start < 0 or length < 0 or start > size or length > size-start):
        raise ValueError('buffer range outside bounds')


class Buffer:
    def __init__(self, budget, size, *, context=None):
        self._context = weakref.proxy(context) if context is not None else None
        self._check_context()
        _range(0, size, size)
        reservation = budget.reserve(native_bytes=size, handles=1)
        payload = None
        try:
            payload = bytearray(size)
            self._control = ResourceControl(payload, reservation, lambda value: value.clear())
            self._operation_lock = threading.Lock()
            self._mutable = True
            self._budget = budget
            self._size = size
            self._check_context()
        except BaseException:
            if payload is not None:
                payload.clear()
            reservation.release()
            raise

    def _check_context(self):
        if self._context is not None:
            self._context.check_cancelled()

    @classmethod
    def map_bytes(cls, budget, data, *, context=None):
        from gopyt.resource_mapping import MappingStorage, MappingInitializationError
        if type(data) is not bytes or not data:
            raise ValueError('nonempty immutable bytes required')
        owner = cls.__new__(cls)
        owner._context = weakref.proxy(context) if context is not None else None
        owner._operation_lock = threading.Lock()
        owner._mutable = False
        owner._budget = budget
        owner._size = len(data)
        owner._check_context()
        storage = MappingStorage()
        reservation = budget.reserve(native_bytes=len(data), mapped_bytes=len(data),
                                     descriptors=1, handles=1)
        try:
            owner._control = ResourceControl(storage, reservation, lambda value: value.close())
        except BaseException:
            reservation.release()
            raise
        try:
            storage.initialize(data, budget, owner._check_context)
            return owner
        except BaseException as error:
            if not owner.close():
                raise MappingInitializationError(owner, error) from error
            raise

    @staticmethod
    def _data(payload):
        return payload if type(payload) is bytearray else payload.mapping

    @contextmanager
    def _serialized(self):
        self._check_context()
        while not self._operation_lock.acquire(timeout=.05):
            self._check_context()
        try:
            self._check_context()
            yield
        finally:
            self._operation_lock.release()

    @contextmanager
    def _access(self):
        # A lease is admitted before waiting for data serialization. Close never
        # waits for this mutex; the admitted operation finishes before cleanup.
        with self._control.lease() as payload:
            with self._serialized():
                yield self._data(payload)

    def read(self, start, length):
        _range(start, length, self._size)
        with self._access() as payload:
            with self._budget.reserve(native_bytes=length):
                # The scoped export cannot escape or survive the operation lease.
                with memoryview(payload) as view, view[start:start+length] as part:
                    return bytes(part)

    def write(self, start, data):
        if type(data) is not bytes:
            raise TypeError('immutable bytes input required')
        _range(start, len(data), self._size)
        with self._access() as payload:
            if not self._mutable:
                raise ValueError('buffer is frozen')
            with self._budget.reserve(native_bytes=len(data)):
                payload[start:start+len(data)] = data

    def freeze(self):
        with self._access():
            self._mutable = False

    def view(self, start, length):
        _range(start, length, self._size)
        with self._access():
            return BufferView(self, start, length)

    def close(self):
        return self._control.close()


class BufferView:
    def __init__(self, owner, start, length):
        _range(start, length, owner._size)
        reservation = owner._budget.reserve(handles=1)
        try:
            self.owner = owner  # A tracing edge; nested views retain the owner.
            self._start, self._length = start, length
            self._reservation = reservation
            self._lock = threading.Lock()
            self._closed = False
        except BaseException:
            reservation.release()
            raise

    @contextmanager
    def _access(self):
        # Serialize logical view close with access admission, then keep its owner
        # lease through the operation. A sibling/child has independent view state.
        with self._lock:
            if self._closed:
                raise ResourceClosedError('view is closed')
            lease = self.owner._control.lease()
            payload = lease.__enter__()
        try:
            with self.owner._serialized():
                yield self.owner._data(payload)
        finally:
            lease.__exit__(None, None, None)

    def read(self, start, length):
        _range(start, length, self._length)
        with self._access() as payload:
            with self.owner._budget.reserve(native_bytes=length):
                with memoryview(payload) as view, view[self._start+start:self._start+start+length] as part:
                    return bytes(part)

    def write(self, start, data):
        if type(data) is not bytes:
            raise TypeError('immutable bytes input required')
        _range(start, len(data), self._length)
        with self._access() as payload:
            if not self.owner._mutable:
                raise ValueError('buffer is frozen')
            with self.owner._budget.reserve(native_bytes=len(data)):
                payload[self._start+start:self._start+start+len(data)] = data

    def view(self, start, length):
        _range(start, length, self._length)
        with self._access():
            return BufferView(self.owner, self._start+start, length)

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._reservation.release()
            return True
