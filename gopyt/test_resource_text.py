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
