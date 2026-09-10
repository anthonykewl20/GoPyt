import unittest
from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
from gopyt.resource_text import decode_utf8


class TextPayload(unittest.TestCase):
    def budget(self, size):
        return ResourceBudget(ResourceLimits(size, 0, 0, 0))

    def test_roundtrip_and_alias_charge(self):
        for expected in ('', 'ascii', 'é中😀'):
            data = expected.encode('utf-8')
            budget = self.budget(10000)
            result = decode_utf8(data, budget)
            self.assertEqual(result, expected)
            self.assertEqual(hash(result), hash(expected))
            self.assertEqual(budget.snapshot()['used']['native_bytes'], 4 * (len(data) + 1))
            alias = result
            del result
            self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
            del alias
            self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_rejection_and_invalid_input_release(self):
        budget = self.budget(0)
        with self.assertRaises(ResourceLimitError):
            decode_utf8(b'hello', budget)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        budget = self.budget(1000)
        self.assertIsNone(decode_utf8(b'\xff', budget))
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_cancellation_retained_traceback_releases_temporaries(self):
        class Cancelled(Exception):
            pass
        calls = 0
        def check():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise Cancelled()
        budget = self.budget(1000)
        failure = None
        try:
            decode_utf8(b'hello', budget, check)
        except Cancelled as error:
            failure = error
        self.assertIsNotNone(failure)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)


class NativeTextPayload(unittest.TestCase):
    def test_native_roundtrip_and_budget_trap(self):
        from types import SimpleNamespace
        from gopyt.natives import NATIVES
        from gopyt.vm import Trap
        from gopyt import ops
        budget = ResourceBudget(ResourceLimits(1000, 0, 0, 0))
        vm = SimpleNamespace(resource_budget=budget, check_cancelled=lambda: None)
        result = NATIVES['core.bytes.to_str'](vm, ['中😀'.encode()], None)
        self.assertEqual(result, '中😀')
        self.assertGreater(budget.snapshot()['used']['native_bytes'], 0)
        del result
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        vm.resource_budget = ResourceBudget(ResourceLimits(0, 0, 0, 0))
        with self.assertRaises(Trap) as failure:
            NATIVES['core.bytes.to_str'](vm, [b'hello'], None)
        self.assertEqual(failure.exception.code, ops.TRAP_ALLOC)

    def test_invalid_utf8_preserves_conversion_error(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from gopyt.natives import NATIVES
        budget = ResourceBudget(ResourceLimits(1000, 0, 0, 0))
        vm = SimpleNamespace(resource_budget=budget, check_cancelled=lambda: None)
        sentinel = object()
        with patch('gopyt.natives._convert_error', return_value=sentinel) as convert:
            self.assertIs(NATIVES['core.bytes.to_str'](vm, [b'\xff'], None), sentinel)
            convert.assert_called_once_with(vm, 'utf8')
        self.assertEqual(budget.snapshot()['active_reservations'], 0)


class TextFailureOwnership(unittest.TestCase):
    def test_constructor_failure_clears_raw_with_traceback_retained(self):
        from unittest.mock import patch
        budget = ResourceBudget(ResourceLimits(1000, 0, 0, 0))
        def fail(value, reservation):
            try:
                raise MemoryError('injected owned string allocation failure')
            finally:
                value = None
        failure = None
        with patch('gopyt.resource_text._ChargedString', new=fail):
            try:
                decode_utf8(b'hello', budget)
            except MemoryError as error:
                failure = error
        self.assertIsNotNone(failure)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        frame = failure.__traceback__
        while frame is not None:
            if frame.tb_frame.f_code.co_name == 'decode_utf8':
                self.assertIsNone(frame.tb_frame.f_locals['raw'])
                self.assertIsNone(frame.tb_frame.f_locals['data'])
            frame = frame.tb_next

    def test_finalizer_never_reenters_locked_budget(self):
        import subprocess
        import sys
        program = """
from gopyt.resource_budget import ResourceBudget, ResourceLimits
from gopyt.resource_text import decode_utf8
budget = ResourceBudget(ResourceLimits(1000, 0, 0, 0))
value = decode_utf8(b'hello', budget)
with budget._lock:
    del value
assert budget.snapshot()['active_reservations'] == 0
"""
        result = subprocess.run([sys.executable, '-c', program], timeout=5,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class NativeEncodedPayload(unittest.TestCase):
    def test_encoded_output_keeps_charge_and_matches_utf8(self):
        from types import SimpleNamespace
        from gopyt.natives import NATIVES
        for text in ('', 'ascii', 'é中😀'):
            budget = ResourceBudget(ResourceLimits(1000, 0, 0, 0))
            vm = SimpleNamespace(resource_budget=budget, check_cancelled=lambda: None)
            result = NATIVES['core.bytes.from_str'](vm, [text], None)
            self.assertEqual(result, text.encode('utf-8'))
            self.assertEqual(budget.snapshot()['used']['native_bytes'], len(result))
            del result
            self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_budget_rejection_precedes_encoder(self):
        from types import SimpleNamespace
        from gopyt.natives import NATIVES
        from gopyt.vm import Trap
        from gopyt import ops
        class Never(str):
            def encode(self, *args):
                raise AssertionError('encoder reached')
        budget = ResourceBudget(ResourceLimits(0, 0, 0, 0))
        vm = SimpleNamespace(resource_budget=budget, check_cancelled=lambda: None)
        with self.assertRaises(Trap) as failure:
            NATIVES['core.bytes.from_str'](vm, [Never('hello')], None)
        self.assertEqual(failure.exception.code, ops.TRAP_ALLOC)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
