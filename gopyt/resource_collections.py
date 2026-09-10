"""Admitted collection producers shared by JSON and language operations."""

class OwnedList(list):
    """Private list builder with admitted slot capacity.

    Producers mutate only through append_owned. Published language operations
    build a new destination; embedding code must not mutate this owner directly.
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


def copy_list(items, budget, check=lambda: None, *, start=0, stop=None):
    """Build an owned shallow copy without an intermediate list slice."""
    result = item = None
    try:
        check()
        if stop is None:
            stop = len(items)
        if not 0 <= start <= stop <= len(items):
            raise ValueError('invalid list copy range')
        result = OwnedList(budget)
        for index in range(start, stop):
            item = items[index]
            result.append_owned(item, check)
        check()
        return result
    finally:
        items = result = item = None


def append_list(items, item, budget, check=lambda: None):
    """Preserve persistent list semantics while admitting destination slots."""
    result = None
    try:
        result = copy_list(items, budget, check)
        result.append_owned(item, check)
        return result
    finally:
        items = item = result = None


def range_list(start, stop, budget, check=lambda: None):
    """Admit generated integer payloads as well as their destination slots."""
    import sys
    from gopyt.resource_json import integer_value
    from gopyt.gobyte import TE_I64
    result = value = number = None
    try:
        check()
        if not (-(1 << 63) <= start <= stop < (1 << 63)):
            raise ValueError('invalid i64 range')
        capacity = ((64 + sys.int_info.bits_per_digit - 1) //
                    sys.int_info.bits_per_digit) * sys.int_info.sizeof_digit
        result = OwnedList(budget)
        if start == stop:
            return result
        # Admit old/new loop integers before arithmetic creates either payload.
        with budget.reserve(native_bytes=2 * capacity):
            try:
                number = start + 0
                while number < stop:
                    value = integer_value(number, TE_I64, budget, check)
                    result.append_owned(value, check)
                    number += 1
                check()
                return result
            finally:
                number = None
    finally:
        start = stop = result = value = None


class OwnedMap(dict):
    """Private map builder with admitted combined-table storage."""
    def __init__(self, budget):
        super().__init__()
        self._budget = budget
        self._reservation = None
        self._slots = 8

    def set_owned(self, key, value, check=lambda: None):
        import struct
        reservation = None
        try:
            check()
            size = len(self) + (key not in self)
            slots = self._slots
            while size > (slots * 2) // 3:
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


def set_map(items, key, value, budget, check=lambda: None):
    """Build a persistent map update without an unowned intermediate copy."""
    result = existing = None
    try:
        check()
        result = OwnedMap(budget)
        for existing in items:
            result.set_owned(existing, items[existing], check)
        result.set_owned(key, value, check)
        return result
    finally:
        items = key = value = result = existing = None


def map_keys(items, budget, check=lambda: None):
    """Return admitted sorted keys, with explicit in-place sort scratch."""
    import struct
    result = key = None
    try:
        check()
        result = OwnedList(budget)
        for key in items:
            result.append_owned(key, check)
        # No key function: valid Unicode scalar order is also UTF-8 byte order.
        # CPython's merge scratch holds at most n pointers without key values.
        with budget.reserve(native_bytes=len(result) * struct.calcsize('P')):
            list.sort(result)
            check()
        return result
    finally:
        items = key = result = None
