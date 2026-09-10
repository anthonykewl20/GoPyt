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


class _Frame:
    __slots__ = ('parent', 'container', 'key', 'phase')

    def __init__(self, parent, container):
        try:
            self.parent = parent
            self.container = container
            self.key = None
            self.phase = 'first'
        finally:
            self = parent = container = None


def parse_owned(text, budget, check=lambda: None):
    """Internal iterative JSON parser whose produced payloads own reservations.

    Linked parser frames are metadata; container pointer/table storage and scalar
    payloads are admitted by their producers. Production routing is pending.
    """
    frame = value = key = None
    index = 0
    try:
        size = len(text)
        while True:
            check()
            while index < size and text[index] in ' \t\r\n':
                if index % 4096 == 0:
                    check()
                index += 1
            ready = False
            if frame is not None:
                is_object = isinstance(frame.container, _JsonObject)
                closing = '}' if is_object else ']'
                if frame.phase in ('first', 'comma') and index < size and text[index] == closing:
                    value = frame.container
                    frame = frame.parent
                    index += 1
                    ready = True
                else:
                    if frame.phase == 'comma':
                        if index >= size or text[index] != ',':
                            raise ConvertFail('syntax')
                        index += 1
                        frame.phase = 'key' if is_object else 'value'
                        continue
                    if frame.phase == 'first':
                        frame.phase = 'key' if is_object else 'value'
                    if frame.phase == 'key':
                        key, index = string_token(text, index, budget, check)
                        if key in frame.container:
                            raise ConvertFail('duplicate key')
                        frame.key = key
                        key = None
                        frame.phase = 'colon'
                        continue
                    if frame.phase == 'colon':
                        if index >= size or text[index] != ':':
                            raise ConvertFail('syntax')
                        index += 1
                        frame.phase = 'value'
                        continue
            if not ready:
                if index >= size:
                    raise ConvertFail('syntax')
                char = text[index]
                if char in '[{':
                    container = _JsonArray(budget) if char == '[' else _JsonObject(budget)
                    try:
                        frame = _Frame(frame, container)
                    finally:
                        container = None
                    index += 1
                    continue
                if char == '"':
                    value, index = string_token(text, index, budget, check)
                elif char == '-' or '0' <= char <= '9':
                    _, decimal = number_span(text, index, check)
                    producer = decimal_token if decimal else integer_token
                    value, index = producer(text, index, budget, check)
                elif text.startswith('true', index):
                    value = True
                    index += 4
                elif text.startswith('false', index):
                    value = False
                    index += 5
                elif text.startswith('null', index):
                    value = None
                    index += 4
                else:
                    raise ConvertFail('syntax')
            if frame is None:
                while index < size and text[index] in ' \t\r\n':
                    if index % 4096 == 0:
                        check()
                    index += 1
                if index != size:
                    raise ConvertFail('trailing')
                check()
                return value
            if isinstance(frame.container, _JsonObject):
                frame.container.insert_owned(frame.key, value, check)
                frame.key = None
            else:
                frame.container.append_owned(value, check)
            value = None
            frame.phase = 'comma'
    finally:
        text = frame = value = key = None


from gopyt.values import I32, U32, U64


def _owned_integer_new(cls, value, reservation):
    try:
        result = int.__new__(cls, value)
        result._reservation = reservation
        return result
    finally:
        value = None


class _ChargedI32(I32):
    __new__ = _owned_integer_new
    __del__ = _ChargedInteger.__del__


class _ChargedU32(U32):
    __new__ = _owned_integer_new
    __del__ = _ChargedInteger.__del__


class _ChargedU64(U64):
    __new__ = _owned_integer_new
    __del__ = _ChargedInteger.__del__


def integer_value(value, tag, budget, check=lambda: None):
    """Admit a typed destination for an already integral JSON integer token."""
    import sys
    from gopyt.jsonc import INT_RANGE
    from gopyt.gobyte import TE_I32, TE_I64, TE_U32, TE_U64
    reservation = result = None
    transferred = False
    try:
        check()
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConvertFail('int')
        lo, hi = INT_RANGE[tag]
        if not lo <= value <= hi:
            raise ConvertFail('range')
        capacity = ((64 + sys.int_info.bits_per_digit - 1) //
                    sys.int_info.bits_per_digit) * sys.int_info.sizeof_digit
        reservation = budget.reserve(native_bytes=capacity)
        constructor = {TE_I32: _ChargedI32, TE_I64: _ChargedInteger,
                       TE_U32: _ChargedU32, TE_U64: _ChargedU64}[tag]
        with budget.reserve(native_bytes=capacity):
            result = constructor(value, reservation)
        transferred = True
        check()
        return result
    finally:
        value = result = None
        if reservation is not None and not transferred:
            reservation.release()


def decimal_integer_value(value, tag, budget, check=lambda: None):
    """Admit Decimal integrality checking and bounded integer export separately."""
    rounded = raw = result = None
    try:
        check()
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ConvertFail('int')
        # Rounding copies or right-shifts the coefficient and may add one word.
        # The existing object's size includes its coefficient; two capacities
        # cover the destination and resize overlap, without making a tuple copy.
        capacity = Decimal.__sizeof__(value) + 8
        with budget.reserve(native_bytes=2 * capacity):
            try:
                rounded = Decimal.to_integral_value(value)
                check()
                if value != rounded:
                    raise ConvertFail('int')
            finally:
                rounded = None
        if value < -(2**63) or value > 2**64 - 1:
            raise ConvertFail('range')
        # At most 20 decimal digits remain. Cover the rounded mpd and shifted
        # export coefficient, plus old/new binary export buffers. One maximum
        # width word per decimal digit is conservative in both limb formats.
        with budget.reserve(native_bytes=2 * 8 * (20 + 4) + 2 * 4 * 20):
            try:
                raw = int(value)
                check()
                result = integer_value(raw, tag, budget, check)
                return result
            finally:
                raw = None
    finally:
        value = rounded = raw = result = None


def decode_value(art, data, te_ix, budget, check=lambda: None):
    """Internal typed conversion with separately owned destination containers."""
    from gopyt import jsonc as j
    result = child = key = None
    try:
        check()
        te = art.texprs[te_ix]
        tag = te.tag
        if tag in (j.TE_F64, j.TE_BYTES):
            raise j.NotJson()
        if tag == j.TE_BOOL:
            if not isinstance(data, bool):
                raise ConvertFail('bool')
            return data
        if tag in j.INT_RANGE:
            producer = decimal_integer_value if isinstance(data, Decimal) else integer_value
            return producer(data, tag, budget, check)
        if tag == j.TE_STR:
            if not isinstance(data, str):
                raise ConvertFail('str')
            return data
        if tag == j.TE_UNIT:
            if data != {}:
                raise ConvertFail('unit')
            return j.UNIT
        if tag == j.TE_OPT:
            if data is None:
                return j.NONE
            child = decode_value(art, data, te.a, budget, check)
            return j.Some(child)
        if tag == j.TE_LIST:
            if not isinstance(data, list):
                raise ConvertFail('list')
            result = _JsonArray(budget)
            for child in data:
                result.append_owned(decode_value(art, child, te.a, budget, check), check)
            return result
        if tag == j.TE_MAP:
            if art.texprs[te.a].tag != j.TE_STR:
                raise j.NotJson()
            if not isinstance(data, dict):
                raise ConvertFail('map')
            result = _JsonObject(budget)
            for key, child in data.items():
                result.insert_owned(key, decode_value(art, child, te.b, budget, check), check)
            return result
        if tag == j.TE_NOM:
            return _nominal_value(art, data, te.a, budget, check)
        if tag == j.TE_UNION:
            if not isinstance(data, dict) or len(data) != 1:
                raise ConvertFail('union')
            key = next(iter(data))
            for member in te.members:
                if j._member_name(art, member) == key:
                    return decode_value(art, data[key], member, budget, check)
            raise ConvertFail('union member')
        raise j.NotJson()
    finally:
        data = result = child = key = None


def _nominal_value(art, data, type_id, budget, check):
    from gopyt import jsonc as j
    body = fields = names = key = None
    try:
        check()
        td = art.types[type_id]
        if td.kind == 3:
            raise j.NotJson()
        if not isinstance(data, dict):
            raise ConvertFail('object')
        variant = None
        if td.kind == 1:
            declarations = td.fields
            body = data
        else:
            if len(data) != 1:
                raise ConvertFail('enum')
            key = next(iter(data))
            for index, (name, declarations) in enumerate(td.variants):
                if art.const_str(name) == key:
                    variant = index
                    break
            if variant is None:
                raise ConvertFail('variant')
            body = data[key]
            if not isinstance(body, dict):
                raise ConvertFail('enum payload')
        names = _JsonArray(budget)
        for name, _ in declarations:
            names.append_owned(art.const_str(name), check)
        for key in body:
            if key not in names:
                raise ConvertFail('unknown key')
        fields = _JsonArray(budget)
        for (_, field_type), name in zip(declarations, names):
            if name in body:
                fields.append_owned(decode_value(art, body[name], field_type, budget, check), check)
            elif art.texprs[field_type].tag == j.TE_OPT:
                fields.append_owned(j.NONE, check)
            else:
                raise ConvertFail('missing field')
        if variant is None:
            return j.Record(type_id, fields)
        return j.EnumVal(type_id, variant, fields)
    finally:
        data = body = fields = names = key = None
