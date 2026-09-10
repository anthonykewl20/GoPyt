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


def slice_text(text, start, end, budget, check=lambda: None):
    """Copy a validated code-point slice with raw/owned capacity admitted first."""
    reservation = None
    raw = result = None
    transferred = False
    try:
        check()
        capacity = 4 * (end - start + 1)
        reservation = budget.reserve(native_bytes=capacity)
        with budget.reserve(native_bytes=capacity):
            try:
                raw = str.__getitem__(text, slice(start, end))
                check()
                result = _ChargedString(raw, reservation)
                transferred = True
                return result
            finally:
                raw = None
    finally:
        text = result = None
        if reservation is not None and not transferred:
            reservation.release()


def utf8_size(text, check=lambda: None):
    """Measure strict UTF-8 payload length without allocating encoded copies."""
    try:
        size = 0
        for index, char in enumerate(text):
            if index % 4096 == 0:
                check()
            point = ord(char)
            if 0xD800 <= point <= 0xDFFF:
                raise UnicodeEncodeError('utf-8', text, index, index + 1,
                                         'surrogates not allowed')
            size += 1 if point < 128 else 2 if point < 2048 else 3 if point < 65536 else 4
        check()
        return size
    finally:
        text = None


def concat_text(left, right, budget, check=lambda: None):
    """Concatenate borrowed strings with temporary and owned capacity admitted."""
    reservation = None
    raw = result = None
    transferred = False
    try:
        check()
        capacity = 4 * (len(left) + len(right) + 1)
        reservation = budget.reserve(native_bytes=capacity)
        with budget.reserve(native_bytes=capacity):
            try:
                raw = str.__add__(left, right)
                check()
                result = _ChargedString(raw, reservation)
                transferred = True
                return result
            finally:
                raw = None
    finally:
        left = right = result = None
        if reservation is not None and not transferred:
            reservation.release()


def format_integer(value, budget, check=lambda: None):
    """Format a language-width integer with admitted decimal and Unicode storage."""
    import sys
    reservation = raw = result = None
    transferred = False
    try:
        check()
        if (isinstance(value, bool) or not isinstance(value, int) or
                not -(1 << 63) <= value < (1 << 64)):
            raise ValueError('language integer required')
        # Both signed i64 and u64 need at most 20 ASCII characters plus NUL.
        capacity = 21
        limbs = (64 + sys.int_info.bits_per_digit - 1) // sys.int_info.bits_per_digit
        # Decimal conversion scratch is <= 1 + 2*binary_limbs on pinned CPython.
        scratch = (1 + 2 * limbs) * sys.int_info.sizeof_digit
        reservation = budget.reserve(native_bytes=capacity)
        with budget.reserve(native_bytes=capacity + scratch):
            try:
                raw = int.__repr__(value)
                check()
                result = _ChargedString(raw, reservation)
                transferred = True
                return result
            finally:
                raw = None
    finally:
        value = result = None
        if reservation is not None and not transferred:
            reservation.release()
