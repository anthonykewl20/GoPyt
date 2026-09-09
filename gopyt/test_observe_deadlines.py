"""Observation contention cannot retain an expired VM caller."""
import threading
import time
import unittest
from unittest.mock import patch

from gopyt import test_native_admission as fixtures
from gopyt import natives
from gopyt.vm import Cancelled, Trap


class ObservationDeadlines(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.NativeAdmission()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.vm, self.ids = fixture.vm, fixture.ids
        self.cancel = threading.Event()

    def held(self, operation, condition):
        vm = self.vm
        lock = vm.observe.lock
        lock.acquire()
        entered = threading.Event()
        outcomes = []
        original = vm.observe.admission
        def admission(context=None):
            entered.set()
            return original(context)
        def run():
            vm.cancels = (self.cancel,)
            if condition == 'deadline':
                vm.deadline_ns = time.monotonic_ns() + 150_000_000
            try:
                outcomes.append(operation())
            except BaseException as error:
                outcomes.append(error)
        with patch.object(vm.observe, 'admission', admission):
            caller = threading.Thread(target=run)
            caller.start()
            try:
                self.assertTrue(entered.wait(2))
                if condition == 'cancel':
                    self.cancel.set()
                caller.join(2)
                self.assertFalse(caller.is_alive())
            finally:
                lock.release()
                caller.join(5)
                self.cancel.clear()
        self.assertEqual(len(outcomes), 1)
        return outcomes[0]

    def test_compiled_call_stops_before_telemetry_lock_release(self):
        for condition in ('cancel', 'deadline'):
            with self.subTest(condition=condition):
                result = self.held(lambda: self.vm.call(self.ids['demo.admit'], []), condition)
                self.assertIsInstance(result, Cancelled if condition == 'cancel' else Trap)
                if condition == 'deadline':
                    self.assertEqual(result.code, 6)
                self.assertEqual(self.vm.buckets, {})
                self.assertEqual(self.vm.observe.events, 0)
                self.assertEqual(self.vm.heap.pins, {})
        self.vm.call(self.ids['demo.admit'], [])
        self.assertEqual(len(self.vm.buckets), 1)

    def test_note_report_snapshot_and_outcome_use_context(self):
        vm = self.vm
        operations = [lambda: vm.observe.note('probe', context=vm),
                      lambda: natives._observe_report(vm, [], None),
                      lambda: vm.observe.snapshot(context=vm),
                      lambda: vm.observe.outcome('task', 'unit', 1, context=vm)]
        for operation in operations:
            with self.subTest(operation=operation):
                result = self.held(operation, 'deadline')
                self.assertIsInstance(result, Trap)
                self.assertEqual(result.code, 6)
                self.assertEqual(vm.observe.events, 0)

    def test_terminal_telemetry_does_not_replace_original_trap(self):
        vm = self.vm
        for condition in ('cancel', 'deadline'):
            for operation in (lambda: vm.observe.trap(1, 'demo', context=vm),
                              lambda: vm.observe.http('route', '500', 1, context=vm)):
                with self.subTest(condition=condition):
                    self.assertIs(self.held(operation, condition), False)
        error = Trap(1)
        def task(*args, **kwargs):
            vm.deadline_ns = time.monotonic_ns() - 1
            raise error
        try:
            with patch.object(vm.observe, 'task', task), self.assertRaises(Trap) as caught:
                vm.call(self.ids['demo.admit'], [])
            self.assertIs(caught.exception, error)
        finally:
            vm.deadline_ns = None
        self.assertTrue(vm.observe.trap(1, 'demo', context=vm))
        self.assertEqual(vm.observe.fail, 1)

    def test_acquired_expired_admission_releases_without_event(self):
        vm = self.vm
        original = vm.check_cancelled
        calls = 0
        def check():
            nonlocal calls
            calls += 1
            if calls == 2:
                vm.deadline_ns = time.monotonic_ns() - 1
            original()
        try:
            with patch.object(vm, 'check_cancelled', check), self.assertRaises(Trap):
                vm.observe.note('probe', context=vm)
        finally:
            vm.deadline_ns = None
        self.assertEqual(vm.observe.events, 0)
        self.assertTrue(vm.observe.lock.acquire(blocking=False))
        vm.observe.lock.release()
        vm.observe.note('host')
        self.assertEqual(vm.observe.events, 1)

    def test_outcome_timeout_releases_unreturned_heap_handoff(self):
        vm = self.vm
        original = vm.observe.outcome
        def outcome(*args, **kwargs):
            vm.deadline_ns = time.monotonic_ns() - 1
            return original(*args, **kwargs)
        try:
            with patch.object(vm.observe, 'outcome', outcome), self.assertRaises(Trap) as caught:
                vm.call(self.ids['demo.admit'], [])
            self.assertEqual(caught.exception.code, 6)
        finally:
            vm.deadline_ns = None
        self.assertEqual(vm.heap.handoffs, {})
        self.assertEqual(vm.heap.pins, {})
        self.assertEqual(vm.heap.frames, {})
        self.assertEqual(len(vm.buckets), 1)
