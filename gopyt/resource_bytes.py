"""Internal, single-consumer byte payload ownership for bounded serialization.

Payload bytes are charged; Python container metadata and allocator overhead are
not. Retained aliases to the charged bytes preserve their reservation.
"""
from gopyt.resource_control import ResourceClosedError


class _ChargedBytes(bytes):
    def __new__(cls, data, reservation):
        try:
            value = super().__new__(cls, data)
            value._reservation = reservation
            return value
        finally:
            data = None

    def __del__(self):
        reservation = getattr(self, '_reservation', None)
        if reservation is not None:
            reservation.release()


class BytePayload:
    def __init__(self, reservation):
        self.data = b''
        self._reservation = reservation
        self._transferred = False
        self._closed = False

    def close(self):
        self.data = b''
        if not self._transferred:
            self._reservation.release()
        self._closed = True

    def __enter__(self):
        if self._closed:
            raise ResourceClosedError('byte payload is closed')
        return self

    def __exit__(self, *unused):
        self.close()


class ByteBuilder:
    def __init__(self, budget):
        self.budget = budget
        self.entries = []
        self.size = 0
        self.closed = False

    def append_text(self, text):
        if self.closed:
            raise ResourceClosedError('byte builder is closed')
        # UTF-8 needs at most four bytes per code point. Admission precedes
        # encoding; shrink to actual length without allocating another copy.
        reservation = self.budget.reserve(native_bytes=4 * len(text))
        entry = None
        raw = None
        adopted = False
        try:
            entry = [None, reservation]
            self.entries.append(entry)
            raw = text.encode('utf-8')
            reservation.reduce(native_bytes=len(raw))
            self.size += len(raw)
            entry[0] = raw
            adopted = True
        finally:
            raw = None
            text = None
            if not adopted:
                if entry is not None and self.entries and self.entries[-1] is entry:
                    self.entries.pop()
                reservation.release()

    def finish(self):
        if self.closed:
            raise ResourceClosedError('byte builder is closed')
        reservation = None
        payload = None
        succeeded = False
        try:
            reservation = self.budget.reserve(native_bytes=self.size)
            payload = BytePayload(reservation)
            with self.budget.reserve(native_bytes=self.size):
                raw = None
                try:
                    raw = b''.join(entry[0] for entry in self.entries)
                    payload.data = _ChargedBytes(raw, reservation)
                    payload._transferred = True
                finally:
                    raw = None
            succeeded = True
            return payload
        finally:
            self.close()
            if not succeeded and reservation is not None:
                if payload is not None:
                    payload.close()
                else:
                    reservation.release()

    def close(self):
        self.closed = True
        for entry in self.entries:
            entry[0] = None
            entry[1].release()
        self.entries.clear()
        self.size = 0


class _ChargedBuffer(bytearray):
    def __init__(self, size, reservation):
        super().__init__(size)
        self._reservation = reservation

    def __del__(self):
        reservation = getattr(self, '_reservation', None)
        if reservation is not None:
            reservation.release()


def read_chunk(stream, budget, size, check):
    """Read into admitted scratch, returning a separately charged immutable chunk."""
    if type(size) is not int or size < 1:
        raise ValueError('positive read size required')
    check()
    scratch_reservation = budget.reserve(native_bytes=size)
    scratch = None
    try:
        try:
            scratch = _ChargedBuffer(size, scratch_reservation)
        except BaseException:
            scratch_reservation.release()
            raise
        count = stream.readinto(scratch)
        check()
        if type(count) is not int or not 0 <= count <= size:
            raise OSError('invalid bounded read result')
        reservation = budget.reserve(native_bytes=count)
        try:
            with memoryview(scratch) as view:
                with view[:count] as used:
                    return _ChargedBytes(used, reservation)
        except BaseException:
            reservation.release()
            raise
    finally:
        scratch = None


def read_payload(stream, budget, limit, check):
    """Read at most limit bytes; the consumer checks its protocol size ceiling."""
    if type(limit) is not int or limit < 0:
        raise ValueError('nonnegative read limit required')
    chunks = []
    chunk = None
    raw = None
    payload = None
    reservation = None
    length = 0
    succeeded = False
    try:
        while length < limit:
            state = budget.snapshot()
            available = state['limits']['native_bytes'] - state['used']['native_bytes']
            size = min(65536, limit - length, max(1, available // 2))
            chunk = read_chunk(stream, budget, size, check)
            if not chunk:
                chunk = None
                break
            length += len(chunk)
            chunks.append(chunk)
            chunk = None
        check()
        reservation = budget.reserve(native_bytes=length)
        payload = BytePayload(reservation)
        with budget.reserve(native_bytes=length):
            try:
                raw = b''.join(chunks)
                payload.data = _ChargedBytes(raw, reservation)
                payload._transferred = True
            finally:
                raw = None
        succeeded = True
        return payload
    finally:
        chunk = None
        chunks.clear()
        if not succeeded and reservation is not None:
            if payload is not None:
                payload.close()
            else:
                reservation.release()
