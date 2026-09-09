"""Compiled typed-time operations, OS calendar oracle and clock fault injection."""
import random
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from gopyt import ops
from gopyt.cli import build
from gopyt.testing import write_pkg
from gopyt.values import Record, UNIT
from gopyt.vm import VM, Trap, Cancelled


class TemporalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        functions = {
            'timestamp_ns': ('value: i64', 'Timestamp'),
            'timestamp_ms': ('value: i64', 'Timestamp'),
            'duration_ns': ('value: i64', 'Duration'),
            'duration_ms': ('value: i64', 'Duration'),
            'timestamp_to_ms': ('value: Timestamp', 'i64'),
            'duration_to_ms': ('value: Duration', 'i64'),
            'add': ('value: Timestamp, delta: Duration', 'Timestamp'),
            'difference': ('later: Timestamp, earlier: Timestamp', 'Duration'),
            'duration_add': ('left: Duration, right: Duration', 'Duration'),
            'elapsed': ('later: MonotonicInstant, earlier: MonotonicInstant', 'Duration'),
            'parse_timestamp': ('text: str', 'Timestamp'),
            'format_timestamp': ('value: Timestamp', 'str'),
            'parse_duration': ('text: str', 'Duration'),
            'format_duration': ('value: Duration', 'str'),
            'now': ('', 'Timestamp'),
            'monotonic_now': ('', 'MonotonicInstant'),
            'sleep': ('duration: Duration', 'unit'),
            'now_ms': ('', 'i64'),
        }
        head = 'module demo\n\nuse core.status { ConvertError }\nuse core.time { Timestamp, Duration, MonotonicInstant }\n\n'
        spec, impl = head, head.replace('MonotonicInstant }', 'MonotonicInstant, ' + ', '.join(functions) + ' }')
        for name, (signature, ret) in functions.items():
            task = name in ['now', 'monotonic_now', 'sleep', 'now_ms']
            decl = ('task' if task else 'fn') + f' {name}({signature}) -> {ret}'
            decl += '' if name == 'now_ms' else ' | ConvertError'
            decl += '\n' + ('    effects { time }\n' if task else '')
            spec += decl + '\n'
            args = ', '.join(arg.split(':')[0] for arg in signature.split(', '))
            impl += decl + '{\n    return core.time.' + name + '(' + args + ')\n}\n\n'
        write_pkg(cls.temp.name, {'spec/demo.gopyt': spec, 'impl/demo.gopyt': impl}, fmt=True)
        cls.art = build(cls.temp.name)[1]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.vm = VM(self.art, self.temp.name)

    def call(self, name, *args):
        return self.vm.call(self.vm.by_name['demo.' + name], list(args))

    def value(self, name, *fields):
        return Record(self.vm.type_id_of('core.time.' + name), list(fields))

    def field(self, result, name, value):
        self.assertEqual(self.vm.type_name(result.type_id), 'core.time.' + name)
        self.assertEqual(result.fields, [value])

    def error(self, result):
        self.assertIsInstance(result, Record)
        self.assertEqual(self.vm.type_name(result.type_id), 'core.status.ConvertError')

    def test_timestamp_roundtrip_extremes_and_os_calendar_oracle(self):
        rng = random.Random(17092026)
        cases = [-2**63, 2**63-1, -1, 0, 1, -1_000_000_001, 1_000_000_001]
        cases += [rng.randrange(-2**63, 2**63) for _ in range(120)]
        for ns in cases:
            value = self.call('timestamp_ns', ns)
            text = self.call('format_timestamp', value)
            seconds, fraction = divmod(ns, 1_000_000_000)
            expected = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(seconds)) + f'.{fraction:09d}Z'
            self.assertEqual(text, expected)
            self.field(self.call('parse_timestamp', text), 'Timestamp', ns)

    def test_explicit_offsets_and_nanosecond_precision(self):
        for text in ['1970-01-01T00:00:00Z', '1970-01-01T08:00:00+08:00',
                     '1969-12-31T19:00:00-05:00', '1970-01-01T00:00:00+00:00']:
            self.field(self.call('parse_timestamp', text), 'Timestamp', 0)
        self.field(self.call('parse_timestamp', '1969-12-31T23:59:59.999999999Z'), 'Timestamp', -1)
        self.field(self.call('parse_timestamp', '2000-02-29T00:00:00.000000001Z'), 'Timestamp', 951782400000000001)
        # Same displayed local time during a DST fold requires an explicit offset.
        a = self.call('parse_timestamp', '2025-11-02T01:30:00-04:00')
        b = self.call('parse_timestamp', '2025-11-02T01:30:00-05:00')
        self.field(self.call('difference', b, a), 'Duration', 3_600_000_000_000)

    def test_invalid_calendar_offset_precision_and_range(self):
        for text in ['1970-01-01T00:00:00', '1970-01-01 00:00:00Z', '1970-01-01t00:00:00z',
                     '1970-01-01T00:00:00-00:00', '2000-02-30T00:00:00Z', '1900-02-29T00:00:00Z',
                     '2000-01-01T24:00:00Z', '2016-12-31T23:59:60Z', '1970-01-01T00:00:00+24:00',
                     '1970-01-01T00:00:00+00:60', '1970-01-01T00:00:00.0000000001Z',
                     '0000-01-01T00:00:00Z', '9999-12-31T23:59:59Z', '1970-01-01T00:00:00Z\n',
                     '１６７７-01-01T00:00:00Z', '1677-09-21T00:12:43.145224191Z',
                     '2262-04-11T23:47:16.854775808Z']:
            self.error(self.call('parse_timestamp', text))

    def test_exact_millisecond_conversion_and_checked_arithmetic(self):
        for ns in [-2**63, -1, 1, 2**63-1]:
            for name in ['timestamp', 'duration']:
                value = self.call(name + '_ns', ns)
                self.error(self.call(name + '_to_ms', value))
        for ms in [-9223372036854, -1, 0, 1, 9223372036854]:
            for name in ['timestamp', 'duration']:
                value = self.call(name + '_ms', ms)
                self.assertEqual(self.call(name + '_to_ms', value), ms)
        for name in ['timestamp_ms', 'duration_ms']:
            self.error(self.call(name, 9223372036855))
            self.error(self.call(name, -9223372036855))
        self.field(self.call('add', self.value('Timestamp', -1), self.value('Duration', 2)), 'Timestamp', 1)
        self.error(self.call('add', self.value('Timestamp', 2**63-1), self.value('Duration', 1)))
        self.error(self.call('difference', self.value('Timestamp', 2**63-1), self.value('Timestamp', -1)))
        self.error(self.call('duration_add', self.value('Duration', -2**63), self.value('Duration', -1)))

    def test_duration_serialization(self):
        for ns in [-2**63, -1, 0, 1, 2**63-1]:
            text = self.call('format_duration', self.value('Duration', ns))
            self.assertEqual(text, str(ns) + 'ns')
            self.field(self.call('parse_duration', text), 'Duration', ns)
        for text in ['-0ns', '+1ns', '01ns', '1ms', '1.0ns', '1ns\n', '9223372036854775808ns']:
            self.error(self.call('parse_duration', text))

    def test_wall_clock_rollback_is_visible_and_legacy_floor_is_exact(self):
        with patch('gopyt.temporal.time.time_ns', side_effect=[100_000_001, -1]):
            before, after = self.call('now'), self.call('now')
        self.field(self.call('difference', after, before), 'Duration', -100_000_002)
        with patch('gopyt.natives.time.time_ns', return_value=-1):
            self.assertEqual(self.call('now_ms'), -1)
        with patch('gopyt.natives.time.time_ns', return_value=9_007_199_254_740_993_999_999):
            self.assertEqual(self.call('now_ms'), 9_007_199_254_740_993)
        with patch('gopyt.natives.time.time_ns', return_value=(2**63)*1_000_000):
            with self.assertRaises(Trap) as caught:
                self.call('now_ms')
        self.assertEqual(caught.exception.code, ops.TRAP_OVERFLOW)

    def test_monotonic_origins_backwards_and_clock_failure(self):
        with patch('gopyt.temporal.time.monotonic_ns', side_effect=[100, 125, 90]):
            a, b, backwards = self.call('monotonic_now'), self.call('monotonic_now'), self.call('monotonic_now')
        self.field(self.call('elapsed', b, a), 'Duration', 25)
        self.error(self.call('elapsed', backwards, b))
        other = VM(self.art, self.temp.name)
        sample = other.call(other.by_name['demo.monotonic_now'], [])
        self.assertNotEqual(sample.fields[1], a.fields[1])
        self.error(self.call('elapsed', sample, a))
        for name, clock in [('now', 'time_ns'), ('monotonic_now', 'monotonic_ns')]:
            with patch('gopyt.temporal.time.'+clock, side_effect=OSError('clock unavailable')):
                self.error(self.call(name))
            with patch('gopyt.temporal.time.'+clock, return_value=2**63):
                self.error(self.call(name))
        self.error(self.call('elapsed', self.value('MonotonicInstant', 0, 'bad'), a))

    def test_sleep_uses_monotonic_deadline_detects_rollback_and_cancels(self):
        with patch('gopyt.temporal.time.monotonic_ns', side_effect=[100, 100, 150, 200]), \
                patch('gopyt.temporal.time.sleep') as sleeper, \
                patch('gopyt.temporal.time.time_ns', side_effect=AssertionError('wall clock used')):
            self.assertIs(self.call('sleep', self.value('Duration', 100)), UNIT)
            self.assertEqual(sleeper.call_count, 2)
        with patch('gopyt.temporal.time.monotonic_ns', side_effect=[100, 99]):
            self.error(self.call('sleep', self.value('Duration', 1)))
        self.error(self.call('sleep', self.value('Duration', -1)))
        outer, inner = threading.Event(), threading.Event()
        self.vm.cancels = (outer, inner)
        def cancel(seconds):
            self.assertLessEqual(seconds, 0.05)
            outer.set()
        with patch('gopyt.temporal.time.sleep', side_effect=cancel):
            with self.assertRaises(Cancelled):
                self.call('sleep', self.value('Duration', 2**63-1))
        self.assertEqual(self.vm.depth, 0)
        self.vm.cancels = ()
        self.assertIs(self.call('sleep', self.value('Duration', 0)), UNIT)

    def test_malformed_record_and_unit_confusion_rejected(self):
        for value in [self.value('Timestamp', True), self.value('Timestamp', 2**63),
                      self.value('Timestamp'), self.value('Duration', 1)]:
            self.error(self.call('format_timestamp', value))
        self.error(self.call('format_duration', self.value('Timestamp', 0)))
        self.error(self.call('add', self.value('Timestamp', 0), self.value('Timestamp', 1)))
