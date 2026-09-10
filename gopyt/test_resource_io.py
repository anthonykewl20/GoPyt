"""Read scratch admission, short-read accounting and cancellation cleanup."""
import io
import unittest

from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
from gopyt.resource_io import read_bytes


class ReadReservations(unittest.TestCase):
    def budget(self, size):
        return ResourceBudget(ResourceLimits(size, 0, 0, 0))

    def test_short_reads_release_unused_capacity_and_charge_final_copy(self):
        budget = self.budget(64)
        class Short(io.BytesIO):
            def read(self, size):
                return super().read(min(1, size))
        data = bytes(range(24))
        self.assertEqual(read_bytes(Short(data), budget, lambda: None, 100), data)
        self.assertGreaterEqual(budget.snapshot()['peak']['native_bytes'], 2 * len(data))
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_exhaustion_cancellation_and_oversize_release_all_scratch(self):
        for failure in ('budget', 'cancel', 'maximum'):
            with self.subTest(failure=failure):
                budget = self.budget(8 if failure == 'budget' else 64)
                checks = []
                def check():
                    checks.append(1)
                    if failure == 'cancel' and len(checks) == 2:
                        raise RuntimeError('cancelled')
                error = RuntimeError if failure == 'cancel' else ResourceLimitError
                with self.assertRaises(error):
                    read_bytes(io.BytesIO(b'0123456789'), budget, check,
                               4 if failure == 'maximum' else 100)
                self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_no_capacity_rejects_before_read(self):
        budget = self.budget(0)
        class NeverRead:
            def read(self, size):
                raise AssertionError('read before admission')
        with self.assertRaises(ResourceLimitError):
            read_bytes(NeverRead(), budget, lambda: None, 100)

    def test_reduce_is_atomic_and_cannot_increase_or_revive(self):
        budget = self.budget(8)
        reservation = budget.reserve(native_bytes=8)
        reservation.reduce(native_bytes=3)
        self.assertEqual(budget.snapshot()['used']['native_bytes'], 3)
        with self.assertRaises(ValueError): reservation.reduce(native_bytes=4)
        self.assertEqual(budget.snapshot()['used']['native_bytes'], 3)
        self.assertEqual(budget.snapshot()['peak']['native_bytes'], 8)
        reservation.release()
        with self.assertRaises(ValueError): reservation.reduce()
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
