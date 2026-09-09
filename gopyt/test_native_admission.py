"""Compiled native admission rejects expired waiters before changing state."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from gopyt.cli import build, make_vm
from gopyt.testing import write_pkg
from gopyt import test_vm as fixtures
from gopyt.vm import Cancelled, Trap


class ProbedLock:
    def __init__(self):
        self.lock = threading.Lock()
        self.attempted = threading.Event()
        self.after_acquire = lambda: None

    def acquire(self, *, timeout):
        self.attempted.set()
        acquired = self.lock.acquire(timeout=timeout)
        if acquired:
            self.after_acquire()
        return acquired

    def release(self):
        self.lock.release()


class NativeAdmission(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = str(Path(temporary.name).resolve())
        effects = 'model, time, log, observe, filesystem.read, filesystem.write'
        signatures = ('task admit() -> unit | Throttled\n    effects { time }\n\n'
                      'task harden() -> Applied | NoChange | EvolveError\n'
                      '    effects { ' + effects + ' }\n')
        bodies = ('task admit() -> unit | Throttled\n    effects { time }\n'
                  '{\n    return core.limit.allow("probe", 1, 100000)\n}\n\n'
                  'task harden() -> Applied | NoChange | EvolveError\n'
                  '    effects { ' + effects + ' }\n{\n    return core.evolve.propose()\n}\n')
        agent = ('\nagent Warden\n    effects { ' + effects + ' }\n'
                 '    tasks { harden }\n    evolve {\n        max 2\n'
                 '        timeout_ms 30000\n        reservoir 32\n    }\n')
        common = 'use core.status { Throttled }\nuse core.evolve { Applied, NoChange, EvolveError }'
        files = dict(fixtures.API_FILES)
        files.update(fixtures.module(signatures + agent, bodies,
                     spec_uses=common, uses=common.replace('EvolveError }', 'EvolveError, propose }')
                     + '\nuse core.limit { allow }'))
        write_pkg(self.root, files, fmt=True)
        prog, art, self.ids = build(self.root)
        self.vm = make_vm(self.root, prog, art, self.ids)
        self.vm.observe.cusum.alarm = True
        self.vm.lock = ProbedLock()
        self.cancel = threading.Event()

    def test_waiting_callers_stop_before_held_lock_release(self):
        for target in ('demo.admit', 'demo.harden', 'api.serve'):
            for condition in ('deadline', 'cancel'):
                with self.subTest(target=target, condition=condition):
                    lock = self.vm.lock
                    lock.attempted.clear()
                    lock.lock.acquire()
                    outcomes = []
                    def run():
                        self.vm.cancels = (self.cancel,)
                        if condition == 'deadline':
                            self.vm.deadline_ns = time.monotonic_ns() + 200_000_000
                        try:
                            outcomes.append(self.vm.call(self.ids[target], []))
                        except BaseException as error:
                            outcomes.append(error)
                    caller = threading.Thread(target=run)
                    with patch('gopyt.evolve.propose') as evolve, patch('gopyt.server._addr') as address:
                        caller.start()
                        try:
                            self.assertTrue(lock.attempted.wait(2))
                            if condition == 'cancel':
                                self.cancel.set()
                            caller.join(2)
                            self.assertFalse(caller.is_alive())
                            self.assertEqual(len(outcomes), 1)
                            self.assertIsInstance(outcomes[0], Cancelled if condition == 'cancel' else Trap)
                            if condition == 'deadline':
                                self.assertEqual(outcomes[0].code, 6)
                            evolve.assert_not_called()
                            address.assert_not_called()
                        finally:
                            lock.release()
                            caller.join(4)
                            self.cancel.clear()
                    self.assertFalse(self.vm.serving)
                    self.assertFalse(self.vm.evolve_in_flight)
                    self.assertEqual(self.vm.evolve_last, 0)
                    self.assertEqual(self.vm.buckets, {})
        self.vm.call(self.ids['demo.admit'], [])
        self.assertEqual(len(self.vm.buckets), 1)

    def test_cancellation_racing_with_acquisition_releases_without_mutation(self):
        self.vm.cancels = (self.cancel,)
        for condition in ('cancel', 'deadline'):
            with self.subTest(condition=condition):
                def expired():
                    if condition == 'cancel':
                        self.cancel.set()
                    else:
                        self.vm.deadline_ns = time.monotonic_ns() - 1
                self.vm.lock.after_acquire = expired
                try:
                    with self.assertRaises(Cancelled if condition == 'cancel' else Trap) as caught:
                        self.vm.call(self.ids['demo.admit'], [])
                    if condition == 'deadline':
                        self.assertEqual(caught.exception.code, 6)
                    self.assertFalse(self.vm.lock.lock.locked())
                    self.assertEqual(self.vm.buckets, {})
                finally:
                    self.vm.lock.after_acquire = lambda: None
                    self.cancel.clear()
                    self.vm.deadline_ns = None
        self.vm.call(self.ids['demo.admit'], [])
        self.assertEqual(len(self.vm.buckets), 1)

    def test_acquired_exception_releases_lock_and_preserves_recovery(self):
        with patch.object(self.vm.limiter, 'allow', side_effect=RuntimeError('host failure')):
            with self.assertRaisesRegex(RuntimeError, 'host failure'):
                self.vm.call(self.ids['demo.admit'], [])
        self.assertFalse(self.vm.lock.lock.locked())
        self.vm.call(self.ids['demo.admit'], [])
        self.assertEqual(len(self.vm.buckets), 1)
