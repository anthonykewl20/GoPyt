"""Internal, single-consumer byte payload ownership for bounded serialization.

Payload bytes are charged; Python container metadata and allocator overhead are
not. Consumers retain the owner, not an exported bytes alias, across operations.
"""
from gopyt.resource_control import ResourceClosedError


class BytePayload:
    def __init__(self, reservation):
        self.data = b''
        self._reservation = reservation

    def close(self):
        self.data = b''
        self._reservation.release()

    def __enter__(self):
        if self._reservation.released:
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
            payload.data = b''.join(entry[0] for entry in self.entries)
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
