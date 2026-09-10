"""Internal admitted JSON token producers; complete parser integration is pending."""
import json
from decimal import Decimal, InvalidOperation
from gopyt.jsonc import ConvertFail
from gopyt.resource_text import _ChargedString, utf8_size


def string_token(text, start, budget, check=lambda: None):
    """Decode a quote-prefixed token, returning owned text and the next offset."""
    reservation = None
    raw = result = None
    transferred = False
    try:
        check()
        if not 0 <= start < len(text) or text[start] != '"':
            raise ConvertFail('syntax')
        # Find the closing quote without allocating a substring. Escaped quotes
        # are skipped; the strict scanner validates escape syntax afterwards.
        end = start + 1
        while end < len(text):
            if (end - start) % 4096 == 0:
                check()
            char = text[end]
            if char == '"':
                break
            end += 2 if char == '\\' else 1
        if end >= len(text):
            raise ConvertFail('syntax')
        capacity = 4 * (end - start + 1)
        reservation = budget.reserve(native_bytes=capacity)
        # Cover scanner writer replacement and substring temporaries separately
        # from the final owned copy. This is payload capacity, not heap metadata.
        with budget.reserve(native_bytes=3 * capacity):
            try:
                invalid = False
                try:
                    raw, next_offset = json.decoder.scanstring(text, start + 1, True)
                    utf8_size(raw, check)
                except (ValueError, UnicodeEncodeError):
                    invalid = True
                if invalid:
                    raise ConvertFail('syntax')
                check()
                result = _ChargedString(raw, reservation)
                transferred = True
                return result, next_offset
            finally:
                raw = None
    finally:
        text = result = None
        if reservation is not None and not transferred:
            reservation.release()


def number_span(text, start, check=lambda: None):
    """Validate JSON number grammar without slicing or converting its digits.

    Return the next offset and whether Decimal conversion is required. The caller
    validates the surrounding delimiter and admits numeric storage separately.
    """
    try:
        check()
        size = len(text)
        index = start
        if not 0 <= index < size:
            raise ConvertFail('number')
        if text[index] == '-':
            index += 1
        if index >= size:
            raise ConvertFail('number')
        if text[index] == '0':
            index += 1
        elif '1' <= text[index] <= '9':
            while index < size and '0' <= text[index] <= '9':
                if (index - start) % 4096 == 0:
                    check()
                index += 1
        else:
            raise ConvertFail('number')
        decimal = False
        if index < size and text[index] == '.':
            decimal = True
            index += 1
            first = index
            while index < size and '0' <= text[index] <= '9':
                if (index - start) % 4096 == 0:
                    check()
                index += 1
            if index == first:
                raise ConvertFail('number')
        if index < size and text[index] in 'eE':
            decimal = True
            index += 1
            if index < size and text[index] in '+-':
                index += 1
            first = index
            while index < size and '0' <= text[index] <= '9':
                if (index - start) % 4096 == 0:
                    check()
                index += 1
            if index == first:
                raise ConvertFail('number')
        check()
        return index, decimal
    finally:
        text = None


class _ChargedInteger(int):
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


def integer_token(text, start, budget, check=lambda: None):
    """Admit an integer token and bounded conversion scratch before producing it."""
    import sys
    reservation = None
    raw = result = chunk = None
    transferred = False
    try:
        end, decimal = number_span(text, start, check)
        if decimal:
            raise ConvertFail('number')
        negative = text[start] == '-'
        first = start + int(negative)
        digits = end - first
        limit = sys.get_int_max_str_digits()
        if limit and digits > limit:
            raise ConvertFail('syntax')
        # One binary limb per decimal digit is conservative on both supported
        # CPython limb formats. Include a carry limb, including for zero.
        capacity = sys.int_info.sizeof_digit * (digits + 1)
        reservation = budget.reserve(native_bytes=capacity)
        # Multiply/add can overlap the old integer, product, and sum. The
        # final subclass copy has its own reservation above. A chunk contains
        # at most nine ASCII characters plus its terminator; four extra
        # limbs cover the chunk integer and its power-of-ten multiplier.
        with budget.reserve(native_bytes=3 * capacity + 10 + 4 * sys.int_info.sizeof_digit):
            try:
                raw = 0
                index = first
                while index < end:
                    check()
                    stop = min(index + 9, end)
                    chunk = text[index:stop]
                    raw = raw * (10 ** (stop - index)) + int(chunk)
                    chunk = None
                    index = stop
                if negative:
                    raw = -raw
                check()
                result = _ChargedInteger(raw, reservation)
                transferred = True
                return result, end
            finally:
                raw = chunk = None
    finally:
        text = result = None
        if reservation is not None and not transferred:
            reservation.release()


class _ChargedDecimal(Decimal):
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


def decimal_token(text, start, budget, check=lambda: None):
    """Construct an owned Decimal after admitting coefficient and input scratch."""
    reservation = None
    token = result = None
    transferred = False
    try:
        end, decimal = number_span(text, start, check)
        if not decimal:
            raise ConvertFail('number')
        size = end - start
        # libmpdec uses at most eight bytes per coefficient word. One word per
        # token character plus the four embedded words is conservative. Exact
        # conversion uses maxcontext with clamp=0, not exponent-sized expansion.
        capacity = 8 * (size + 4)
        reservation = budget.reserve(native_bytes=capacity)
        # ASCII token and numeric_as_ascii each need size+1 bytes. Retain another
        # coefficient capacity for allocator resize overlap during finalization.
        with budget.reserve(native_bytes=2 * (size + 1) + capacity):
            try:
                token = text[start:end]
                invalid = False
                try:
                    result = _ChargedDecimal(token, reservation)
                except InvalidOperation:
                    invalid = True
                if invalid:
                    raise ConvertFail('syntax')
                transferred = True
                check()
                return result, end
            finally:
                token = None
    finally:
        text = result = None
        if reservation is not None and not transferred:
            reservation.release()


class _JsonArray(list):
    """Parser-private append-only list; only append_owned may mutate its slots.

    This owner is not a general language list implementation. Typed conversion
    must admit its own destination before exposing a mutable language value.
    """
    def __init__(self, budget):
        super().__init__()
        self._budget = budget
        self._reservation = None
        self._capacity = 0

    def append_owned(self, value, check=lambda: None):
        import struct
        reservation = None
        try:
            check()
            size = len(self) + 1
            if size > self._capacity:
                # CPython 3.11/3.14 append growth, including alignment padding.
                # Keep the old allocation admitted during realloc overlap.
                capacity = (size + (size >> 3) + 6) & ~3
                reservation = self._budget.reserve(
                    native_bytes=capacity * struct.calcsize('P'))
                list.append(self, value)
                previous = self._reservation
                self._reservation = reservation
                self._capacity = capacity
                reservation = None
                if previous is not None:
                    previous.release()
            else:
                list.append(self, value)
            check()
        finally:
            value = None
            if reservation is not None:
                reservation.release()
            self = None

    def __del__(self):
        reservation = getattr(self, '_reservation', None)
        if reservation is not None:
            reservation.finalize()


class _JsonObject(dict):
    """Parser-private insert-only object with admitted hash-table storage."""
    def __init__(self, budget):
        super().__init__()
        self._budget = budget
        self._reservation = None
        self._slots = 8

    def insert_owned(self, key, value, check=lambda: None):
        import struct
        reservation = None
        try:
            check()
            if not isinstance(key, str):
                raise ConvertFail('key')
            if key in self:
                raise ConvertFail('duplicate key')
            slots = self._slots
            while len(self) + 1 > (slots * 2) // 3:
                slots *= 2
            # Combined tables: each index is at most pointer-sized and each
            # general entry contains hash/key/value. Charging four words per
            # slot also conservatively covers the smaller Unicode entry layout.
            # Reserve even without growth: a str subclass key can change layout.
            reservation = self._budget.reserve(
                native_bytes=slots * 4 * struct.calcsize('P'))
            dict.__setitem__(self, key, value)
            previous = self._reservation
            self._reservation = reservation
            self._slots = slots
            reservation = None
            if previous is not None:
                previous.release()
            check()
        finally:
            key = value = None
            if reservation is not None:
                reservation.release()
            self = None

    def __del__(self):
        reservation = getattr(self, '_reservation', None)
        if reservation is not None:
            reservation.finalize()
