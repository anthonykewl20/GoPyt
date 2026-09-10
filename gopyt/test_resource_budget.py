"""Independent reservation accounting, concurrency and exceptional cleanup."""
import concurrent.futures
import threading
import unittest

from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError


class ResourceAccounting(unittest.TestCase):
    def test_atomic_dimensions_and_exact_once_release(self):
        budget = ResourceBudget(ResourceLimits(10, 20, 2, 4))
        first = budget.reserve(native_bytes=7, mapped_bytes=8, descriptors=1, handles=2)
        before = budget.snapshot()
        with self.assertRaises(ResourceLimitError):
            budget.reserve(native_bytes=1, mapped_bytes=1, descriptors=2, handles=1)
        after = budget.snapshot()
        self.assertEqual(after['used'], before['used'])
        self.assertEqual(after['peak'], before['peak'])
        self.assertEqual(after['active_reservations'], 1)
        second = budget.reserve(native_bytes=3, mapped_bytes=12, descriptors=1, handles=2)
        self.assertEqual(budget.snapshot()['used'], dict(native_bytes=10,mapped_bytes=20,descriptors=2,handles=4))
        first.release(); first.release()
        self.assertEqual(budget.snapshot()['used'], dict(native_bytes=3,mapped_bytes=12,descriptors=1,handles=2))
        second.release()
        self.assertTrue(first.released and second.released)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        self.assertEqual(list(budget.snapshot()['used'].values()), [0,0,0,0])

    def test_concurrent_admission_never_exceeds_shared_capacity(self):
        budget = ResourceBudget(ResourceLimits(3, 3, 3, 3))
        start = threading.Barrier(16)
        admitted = threading.Barrier(16)
        def run(_):
            start.wait(5)
            try:
                reservation = budget.reserve(native_bytes=1,mapped_bytes=1,descriptors=1,handles=1)
            except ResourceLimitError:
                reservation = None
            admitted.wait(5)  # No winner releases until all contenders have attempted.
            if reservation:
                reservation.release()
            return reservation is not None
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            outcomes = list(pool.map(run, range(16)))
        self.assertEqual(sum(outcomes), 3)
        result = budget.snapshot()
        self.assertEqual(list(result['peak'].values()), [3,3,3,3])
        self.assertEqual(list(result['used'].values()), [0,0,0,0])
        self.assertEqual(result['rejected'], 13)

    def test_exception_unwinds_and_double_release_race_is_safe(self):
        budget = ResourceBudget(ResourceLimits(8,0,1,1))
        with self.assertRaisesRegex(RuntimeError, 'constructor failed'):
            with budget.reserve(native_bytes=8, descriptors=1, handles=1):
                raise RuntimeError('constructor failed')
        reservation = budget.reserve(native_bytes=8, descriptors=1, handles=1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: reservation.release(), range(32)))
        self.assertEqual(list(budget.snapshot()['used'].values()), [0,0,0,0])
        with self.assertRaises(ValueError):
            with reservation:
                pass

    def test_invalid_amounts_and_snapshot_cannot_change_ledger(self):
        for value in (-1, True, 1.5, 1 << 63):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ResourceLimits(value,0,0,0)
        budget = ResourceBudget(ResourceLimits(2,0,0,0))
        for value in (-1, True, 1.5, 1 << 63):
            with self.subTest(value=value), self.assertRaises(ValueError):
                budget.reserve(native_bytes=value)
        snapshot = budget.snapshot()
        snapshot['used']['native_bytes'] = -100
        snapshot['limits']['native_bytes'] = 1000
        with self.assertRaises(ResourceLimitError):
            budget.reserve(native_bytes=3)
        self.assertEqual(budget.snapshot()['used']['native_bytes'], 0)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
