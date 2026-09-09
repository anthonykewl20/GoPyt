"""Typed nanosecond time; see docs/time-amendment-2026-09-10.md."""
from __future__ import annotations

from datetime import datetime, timedelta
import re
import time

from gopyt.values import Record, UNIT

MIN_NS = -(2**63)
MAX_NS = 2**63 - 1
SECOND = 1_000_000_000
MILLISECOND = 1_000_000
EPOCH = datetime(1970, 1, 1)
STAMP = re.compile(r'([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})\Z', re.ASCII)
DURATION = re.compile(r'(0|-?[1-9][0-9]{0,18})ns\Z', re.ASCII)
ORIGIN = re.compile(r'[0-9a-f]{32}\Z', re.ASCII)


class TimeError(ValueError):
    pass


def integer(value: object) -> int:
    if type(value) is not int or not MIN_NS <= value <= MAX_NS:
        raise TimeError('time integer outside i64 range')
    return value


def record(vm, name: str, value: int) -> Record:
    return Record(vm.type_id_of('core.time.' + name), [integer(value)])


def field(vm, value: object, name: str) -> int:
    if (not isinstance(value, Record) or value.type_id != vm.type_id_of('core.time.' + name)
            or len(value.fields) != 1):
        raise TimeError('invalid ' + name + ' record')
    return integer(value.fields[0])


def timestamp_ns(vm, value: int) -> Record:
    return record(vm, 'Timestamp', value)


def timestamp_ms(vm, value: int) -> Record:
    return record(vm, 'Timestamp', integer(value) * MILLISECOND)


def duration_ns(vm, value: int) -> Record:
    return record(vm, 'Duration', value)


def duration_ms(vm, value: int) -> Record:
    return record(vm, 'Duration', integer(value) * MILLISECOND)


def milliseconds(vm, value: Record, name: str) -> int:
    ns = field(vm, value, name)
    if ns % MILLISECOND:
        raise TimeError('time conversion would discard nanoseconds')
    return ns // MILLISECOND


def add(vm, value: Record, delta: Record) -> Record:
    return timestamp_ns(vm, field(vm, value, 'Timestamp') + field(vm, delta, 'Duration'))


def difference(vm, later: Record, earlier: Record) -> Record:
    return duration_ns(vm, field(vm, later, 'Timestamp') - field(vm, earlier, 'Timestamp'))


def duration_add(vm, left: Record, right: Record) -> Record:
    return duration_ns(vm, field(vm, left, 'Duration') + field(vm, right, 'Duration'))


def instant(vm, value: object) -> tuple[int, str]:
    if (not isinstance(value, Record) or value.type_id != vm.type_id_of('core.time.MonotonicInstant')
            or len(value.fields) != 2):
        raise TimeError('invalid MonotonicInstant record')
    ticks, origin = value.fields
    if type(origin) is not str or ORIGIN.fullmatch(origin) is None:
        raise TimeError('invalid monotonic clock origin')
    return integer(ticks), origin


def elapsed(vm, later: Record, earlier: Record) -> Record:
    end, end_origin = instant(vm, later)
    start, start_origin = instant(vm, earlier)
    if end_origin != start_origin:
        raise TimeError('monotonic clock origin mismatch')
    if end < start:
        raise TimeError('monotonic clock moved backwards or samples reversed')
    return duration_ns(vm, end - start)


def parse_timestamp(vm, text: str) -> Record:
    match = STAMP.fullmatch(text) if type(text) is str and len(text) <= 35 else None
    if match is None:
        raise TimeError('timestamp requires an explicit RFC3339 offset and at most nine fractional digits')
    year, month, day, hour, minute, second = map(int, match.groups()[:6])
    fraction, zone = match.groups()[6:]
    if second > 59 or zone == '-00:00':
        raise TimeError('leap seconds and unknown offsets are unsupported')
    try:
        date = datetime(year, month, day, hour, minute, second)
    except ValueError as error:
        raise TimeError('invalid Gregorian date or time') from error
    offset = 0
    if zone != 'Z':
        hours, minutes = int(zone[1:3]), int(zone[4:])
        if hours > 23 or minutes > 59:
            raise TimeError('invalid UTC offset')
        offset = (hours * 60 + minutes) * 60 * (1 if zone[0] == '+' else -1)
    delta = date - EPOCH
    seconds = delta.days * 86400 + delta.seconds - offset
    return timestamp_ns(vm, seconds * SECOND + int((fraction or '').ljust(9, '0')))


def format_timestamp(vm, value: Record) -> str:
    seconds, fraction = divmod(field(vm, value, 'Timestamp'), SECOND)
    date = EPOCH + timedelta(seconds=seconds)
    return f'{date.year:04d}-{date.month:02d}-{date.day:02d}T{date.hour:02d}:{date.minute:02d}:{date.second:02d}.{fraction:09d}Z'


def parse_duration(vm, text: str) -> Record:
    if type(text) is not str or len(text) > 22 or DURATION.fullmatch(text) is None:
        raise TimeError('duration requires canonical signed integer nanoseconds with ns suffix')
    return duration_ns(vm, int(text[:-2]))


def format_duration(vm, value: Record) -> str:
    return str(field(vm, value, 'Duration')) + 'ns'


def clock(read) -> int:
    try:
        return integer(read())
    except (OSError, OverflowError) as error:
        raise TimeError('host clock unavailable') from error


def now(vm) -> Record:
    return timestamp_ns(vm, clock(time.time_ns))


def monotonic_now(vm) -> Record:
    return Record(vm.type_id_of('core.time.MonotonicInstant'), [clock(time.monotonic_ns), vm.clock_id])


def wait_ns(vm, duration: int):
    """Integer deadline with bounded host sleeps and ancestor cancellation."""
    from gopyt.vm import Cancelled
    previous = clock(time.monotonic_ns)
    deadline = previous + duration
    while True:
        if any(cancel.is_set() for cancel in vm.cancels):
            raise Cancelled()
        current = clock(time.monotonic_ns)
        if current < previous:
            raise TimeError('monotonic clock moved backwards during sleep')
        previous = current
        remaining = deadline - current
        if remaining <= 0:
            return UNIT
        try:
            time.sleep(min(remaining, 50_000_000) / SECOND)
        except (OSError, OverflowError) as error:
            raise TimeError('host sleep unavailable') from error


def sleep(vm, duration: Record):
    ns = field(vm, duration, 'Duration')
    if ns < 0:
        raise TimeError('sleep duration must be nonnegative')
    return wait_ns(vm, ns)


def install(table: dict) -> None:
    def wrap(operation):
        def call(vm, args, func):
            try:
                return operation(vm, *args)
            except TimeError as error:
                return Record(vm.type_id_of('core.status.ConvertError'), [str(error)])
        return call
    operations = {'timestamp_ns': timestamp_ns, 'timestamp_ms': timestamp_ms,
                  'duration_ns': duration_ns, 'duration_ms': duration_ms,
                  'timestamp_to_ms': lambda vm, value: milliseconds(vm, value, 'Timestamp'),
                  'duration_to_ms': lambda vm, value: milliseconds(vm, value, 'Duration'),
                  'add': add, 'difference': difference, 'duration_add': duration_add,
                  'elapsed': elapsed, 'parse_timestamp': parse_timestamp,
                  'format_timestamp': format_timestamp, 'parse_duration': parse_duration,
                  'format_duration': format_duration, 'now': now, 'monotonic_now': monotonic_now,
                  'sleep': sleep}
    for name, operation in operations.items():
        table['core.time.' + name] = wrap(operation)
