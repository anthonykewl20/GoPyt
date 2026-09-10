"""Owned Unicode payload capacity; excludes object metadata and allocator overhead."""


class _ChargedString(str):
    def __new__(cls, value, reservation):
        try:
            result = super().__new__(cls, value)
            result._reservation = reservation
            return result
        finally:
            value = None

    def __del__(self):
        reservation = getattr(self, '_reservation', None)
        if reservation is not None:
            reservation.finalize()


def decode_utf8(data, budget, check=lambda: None):
    """Return charged text, or None for invalid UTF-8 after discarding its error.

    Strict CPython decoding can overlap two Unicode buffers, each bounded by
    four bytes per input byte plus a terminator. Error input can copy the bytes.
    Reserve that temporary capacity separately from the owned string copy.
    """
    reservation = None
    result = raw = None
    transferred = False
    try:
        check()
        size = len(data)
        reservation = budget.reserve(native_bytes=4 * (size + 1))
        with budget.reserve(native_bytes=8 * (size + 1) + size):
            try:
                try:
                    raw = bytes.decode(data, 'utf-8', 'strict')
                except UnicodeDecodeError:
                    # Do not propagate an exception retaining an uncharged
                    # input copy after this temporary reservation is released.
                    return None
                check()
                result = _ChargedString(raw, reservation)
                transferred = True
                return result
            finally:
                raw = None
    finally:
        data = result = None
        if reservation is not None and not transferred:
            reservation.release()
