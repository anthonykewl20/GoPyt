"""Budgeted immutable read chunks, including the simultaneous final output copy."""
from gopyt.resource_budget import ResourceLimitError


def read_bytes(stream, budget, check, maximum):
    chunks = []
    reservations = []
    chunk = None
    length = 0
    try:
        while True:
            check()
            state = budget.snapshot()
            available = state['limits']['native_bytes'] - state['used']['native_bytes']
            # A snapshot guides chunk size only. reserve() atomically arbitrates
            # concurrent consumers, and final-copy admission remains independent.
            size = min(65536, maximum + 1 - length, max(1, available // 2))
            reservation = budget.reserve(native_bytes=size)
            try:
                reservations.append(reservation)
            except BaseException:
                reservation.release()
                raise
            chunk = stream.read(size)
            check()
            if chunk is None:
                raise OSError('file read would block')
            if type(chunk) is not bytes or len(chunk) > size:
                raise OSError('invalid bounded file read')
            reservation.reduce(native_bytes=len(chunk))
            if not chunk:
                with budget.reserve(native_bytes=length):
                    return b''.join(chunks)
            length += len(chunk)
            if length > maximum:
                raise ResourceLimitError('file allocation ceiling exceeded')
            chunks.append(chunk)
            chunk = None
    finally:
        # Clear native scratch references before relinquishing their charges,
        # including on cancellation and exceptions retained by a traceback.
        chunk = None
        chunks.clear()
        for reservation in reservations:
            reservation.release()
