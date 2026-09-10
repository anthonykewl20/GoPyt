"""Logical close, concurrent admission and physical release are separate states."""
import concurrent.futures
import threading
import unittest

from gopyt.resource_budget import ResourceBudget, ResourceLimits
from gopyt.resource_control import ResourceControl, ResourceClosedError


class ResourceLifetimeControl(unittest.TestCase):
    def fixture(self, closer):
        budget = ResourceBudget(ResourceLimits(4,0,0,1))
        control = ResourceControl(bytearray(b'data'), budget.reserve(native_bytes=4,handles=1), closer)
        return budget, control

    def test_close_defers_physical_release_until_admitted_access_finishes(self):
        closed = []
        budget, control = self.fixture(lambda payload: closed.append(bytes(payload)))
        entered, finish = threading.Event(), threading.Event()
        def access():
            with control.lease() as payload:
                entered.set()
                self.assertTrue(finish.wait(5))
                self.assertEqual(bytes(payload), b'data')
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(access)
            try:
                self.assertTrue(entered.wait(5))
                self.assertFalse(control.close())
                self.assertEqual(closed, [])
                self.assertEqual(budget.snapshot()['used']['native_bytes'], 4)
                with self.assertRaises(ResourceClosedError):
                    with control.lease(): pass
            finally:
                finish.set()
            future.result(5)
        self.assertEqual(closed, [b'data'])
        self.assertEqual(control.snapshot()['state'], 'released')
        self.assertEqual(budget.snapshot()['used']['native_bytes'], 0)

    def test_failed_cleanup_remains_charged_and_retry_is_explicit(self):
        attempts = []
        def closer(payload):
            attempts.append(bytes(payload))
            if len(attempts) == 1:
                raise OSError('injected release failure')
        budget, control = self.fixture(closer)
        self.assertFalse(control.close())
        self.assertEqual(control.snapshot()['cleanup_error'], 'OSError')
        self.assertEqual(budget.snapshot()['active_reservations'], 1)
        self.assertTrue(control.close())
        self.assertEqual(attempts, [b'data', b'data'])
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_simultaneous_close_runs_closer_once_without_state_lock(self):
        calls = []
        entered, finish = threading.Event(), threading.Event()
        def closer(payload):
            calls.append(bytes(payload))
            entered.set()
            self.assertTrue(finish.wait(5))
        budget, control = self.fixture(closer)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            first = pool.submit(control.close)
            try:
                self.assertTrue(entered.wait(5))
                results = [pool.submit(control.close) for _ in range(16)]
                self.assertTrue(all(not result.result(5) for result in results))
                self.assertTrue(control.snapshot()['cleaning'])
                self.assertEqual(budget.snapshot()['active_reservations'], 1)
            finally:
                finish.set()
            self.assertTrue(first.result(5))
        self.assertEqual(calls, [b'data'])
        self.assertTrue(control.close())

    def test_exception_in_nested_leases_does_not_strand_release(self):
        calls=[]
        budget, control = self.fixture(lambda payload: calls.append(bytes(payload)))
        with self.assertRaisesRegex(RuntimeError, 'operation'):
            with control.lease(), control.lease():
                self.assertFalse(control.close())
                raise RuntimeError('operation')
        self.assertEqual(calls, [b'data'])
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
