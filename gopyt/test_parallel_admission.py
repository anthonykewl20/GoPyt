"""Compiled nested/concurrent admission, deadline and cleanup regressions."""
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from gopyt import ops
from gopyt.cli import build
from gopyt.testing import write_pkg
from gopyt.values import UNIT
from gopyt.vm import VM, Trap, Cancelled


class ParallelAdmission(unittest.TestCase):
    def make(self, depth=1, fan=4, limit=64, timeout=5000):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        def expression(level):
            if level == 0:
                return 'core.time.sleep_ms(200)'
            return f'parallel max {fan} timeout_ms {timeout} {{\n' + '\n'.join(expression(level-1) for _ in range(fan)) + '\n}'
        ret = 'list[' * depth + 'unit' + ']' * depth
        decl = f'task run() -> {ret}\n    effects {{ time }}\n'
        write_pkg(temp.name, {'spec/demo.gopyt': 'module demo\n\n' + decl,
                              'impl/demo.gopyt': 'module demo\n\nuse core.time { sleep_ms }\n\n' + decl + '{\n    return ' + expression(depth) + '\n}\n'}, fmt=True)
        vm = VM(build(temp.name)[1], temp.name, parallel_workers=limit)
        vm.natives = dict(vm.natives)
        return vm

    def call(self, vm):
        return vm.call(vm.by_name['demo.run'], [])

    def assert_trap(self, vm, code):
        with self.assertRaises(Trap) as caught:
            self.call(vm)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(vm.parallel_budget.active, 0)

    def test_rejection_starts_no_arm_or_worker(self):
        vm = self.make(fan=4, limit=3)
        called = []
        vm.natives['core.time.sleep_ms'] = lambda *args: called.append(1) or UNIT
        with patch('gopyt.vm.threading.Thread.start') as start:
            self.assert_trap(vm, ops.TRAP_PAR_MAX)
        start.assert_not_called()
        self.assertEqual(called, [])
        self.assertEqual(vm.parallel_budget.peak, 0)
        self.assertEqual(vm.parallel_budget.rejected, 1)

    def test_nested_groups_cannot_multiply_past_vm_budget(self):
        vm = self.make(depth=3, fan=4, limit=8)
        before = {t.ident for t in threading.enumerate()}
        self.assert_trap(vm, ops.TRAP_PAR_MAX)
        self.assertLessEqual(vm.parallel_budget.peak, 8)
        self.assertGreater(vm.parallel_budget.rejected, 0)
        self.assertEqual([t for t in threading.enumerate() if t.ident not in before], [])
        # A failed nested group must leave the VM usable for an admitted group.
        vm.natives['core.time.sleep_ms'] = lambda *args: UNIT
        ids = [vm.by_name['core.time.sleep_ms']]
        self.assertEqual(vm.run_parallel(ids, [0], 1, 1000), [UNIT])
        self.assertEqual(vm.parallel_budget.active, 0)

    def test_overlapping_host_calls_share_admission(self):
        vm = self.make(fan=4, limit=4)
        entered, release = threading.Event(), threading.Event()
        active = 0
        lock = threading.Lock()
        outcome = []
        def held(*args):
            nonlocal active
            with lock:
                active += 1
                if active == 4:
                    entered.set()
            if not release.wait(3):
                raise AssertionError('test release timed out')
            return UNIT
        vm.natives['core.time.sleep_ms'] = held
        def invoke():
            try:
                outcome.append(self.call(vm))
            except BaseException as error:
                outcome.append(error)
        host = threading.Thread(target=invoke)
        host.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(Trap) as caught:
                self.call(vm)
            self.assertEqual(caught.exception.code, ops.TRAP_PAR_MAX)
            self.assertEqual(vm.parallel_budget.active, 4)
        finally:
            release.set()
            host.join(3)
        self.assertFalse(host.is_alive())
        self.assertEqual(outcome, [[UNIT] * 4])
        self.assertEqual(vm.parallel_budget.active, 0)
        self.assertEqual(vm.parallel_budget.peak, 4)

    def test_partial_thread_start_failure_joins_and_releases_capacity(self):
        vm = self.make(fan=2, limit=2)
        original = threading.Thread.start
        starts = []
        def start(thread):
            starts.append(thread)
            if len(starts) == 2:
                raise RuntimeError('simulated thread exhaustion')
            return original(thread)
        with patch('gopyt.vm.threading.Thread.start', new=start):
            self.assert_trap(vm, ops.TRAP_PAR_MAX)
        self.assertFalse(starts[0].is_alive())
        vm.natives['core.time.sleep_ms'] = lambda *args: UNIT
        self.assertEqual(self.call(vm), [UNIT, UNIT])
        self.assertEqual(vm.parallel_budget.active, 0)

    def test_absolute_host_deadline_propagates_through_nested_groups(self):
        vm = self.make(depth=2, fan=2, limit=8)
        original = vm.natives['core.time.sleep_ms']
        deadlines = []
        lock = threading.Lock()
        def capture(vm, args, func):
            with lock:
                deadlines.append(vm.deadline_ns)
            return original(vm, args, func)
        vm.natives['core.time.sleep_ms'] = capture
        vm.deadline_ns = time.monotonic_ns() + 30_000_000
        expected = vm.deadline_ns
        started = time.monotonic()
        self.assert_trap(vm, ops.TRAP_TIMEOUT)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(deadlines)
        self.assertEqual(set(deadlines), {expected})
        self.assertEqual(vm.deadline_ns, expected)
        vm.deadline_ns = None

    def test_expired_host_deadline_does_not_admit_work(self):
        vm = self.make(limit=4)
        vm.deadline_ns = time.monotonic_ns() - 1
        called = []
        vm.natives['core.time.sleep_ms'] = lambda *args: called.append(1) or UNIT
        self.assert_trap(vm, ops.TRAP_TIMEOUT)
        self.assertEqual(called, [])
        self.assertEqual(vm.parallel_budget.peak, 0)
        vm.deadline_ns = None
        self.assertEqual(self.call(vm), [UNIT] * 4)

    def test_noncooperative_native_is_joined_before_timeout_returns(self):
        vm = self.make(fan=1, limit=1, timeout=5)
        effects, outcomes = [], []
        entered, stopped, release = (threading.Event() for _ in range(3))
        clock = [time.monotonic_ns()]
        def blocking(*args):
            # Expire only after actual native admission, then observe the real
            # coordinator's stop request while deliberately refusing to finish.
            clock[0] = vm.deadline_ns + 1
            entered.set()
            if vm.cancels[-1].wait(2):
                stopped.set()
            if release.wait(3):
                effects.append('published')
            return UNIT
        def run():
            try:
                outcomes.append(self.call(vm))
            except BaseException as error:
                outcomes.append(error)
        vm.natives['core.time.sleep_ms'] = blocking
        caller = threading.Thread(target=run)
        with patch('time.monotonic_ns', side_effect=lambda: clock[0]):
            caller.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(stopped.wait(2))
                caller.join(.05)
                self.assertTrue(caller.is_alive(), 'timeout abandoned the admitted writer')
                self.assertEqual(effects, [])
            finally:
                clock[0] += 10_000_000_000
                release.set()
                caller.join(3)
        self.assertFalse(caller.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], Trap)
        self.assertEqual(outcomes[0].code, ops.TRAP_TIMEOUT)
        self.assertEqual(effects, ['published'])
        self.assertEqual(vm.parallel_budget.active, 0)

    def test_ancestor_cancel_releases_all_worker_capacity(self):
        vm = self.make(depth=2, fan=2, limit=8)
        cancel = threading.Event()
        vm.cancels = (cancel,)
        timer = threading.Timer(0.01, cancel.set)
        timer.start()
        try:
            with self.assertRaises(Cancelled):
                self.call(vm)
        finally:
            timer.join()
        self.assertEqual(vm.parallel_budget.active, 0)
        self.assertEqual(vm.depth, 0)

    def test_invalid_host_worker_limits_are_rejected(self):
        for value in [0, -1, 65, True, 1.5]:
            with self.subTest(limit=value), self.assertRaises(ValueError):
                self.make(limit=value)

    def http_files(self, timeout=1000):
        from gopyt.test_vm import API_FILES
        files = dict(API_FILES)
        files['impl/api.gopyt'] = files['impl/api.gopyt'].replace(
            'use core.log { write }', 'use core.list { len }\nuse core.log { write }').replace(
            '    core.log.write("echo")',
            '    checks = parallel max 2 timeout_ms ' + str(timeout) + ' {\n'
            '        core.log.write("arm")\n        core.log.write("arm")\n    }').replace(
            'amount: core.str.len(name)', 'amount: core.str.len(name) + core.list.len(checks)')
        for path in files:
            files[path] = files[path].replace('task get_echo(name: str) -> Receipt\n    effects { log }', 'task get_echo(name: str) -> Receipt\n    effects { time, log }').replace('effects { network, log }', 'effects { network, time, log }')
        return files

    def test_http_parallel_overload_is_503_and_recovers(self):
        from gopyt.test_app_runtime import running_server, request
        files = self.http_files()
        with patch('gopyt.test_app_runtime.fixtures.API_FILES', files):
            with running_server(MAX_HANDLERS=2) as (vm, port):
                with vm.parallel_budget.reserve(64):
                    self.assertEqual(request(port), (503, b''))
                self.assertEqual(request(port), (200, b'{"amount":5}'))
                self.assertEqual(vm.parallel_budget.active, 0)

    def test_http_parallel_timeout_is_504(self):
        from gopyt.test_app_runtime import running_server, request
        files = self.http_files(timeout=5)
        with patch('gopyt.test_app_runtime.fixtures.API_FILES', files):
            with running_server(MAX_HANDLERS=2) as (vm, port):
                vm.natives = dict(vm.natives)
                vm.natives['core.log.write'] = lambda *args: time.sleep(0.03) or UNIT
                self.assertEqual(request(port), (504, b''))
                self.assertEqual(vm.parallel_budget.active, 0)

    def test_own_deadline_includes_admission_lock_contention(self):
        vm = self.make(fan=1, limit=1, timeout=5)
        entered = threading.Event()
        calls, outcome = [], []
        original = vm.parallel_budget.reserve
        def reserve(count):
            entered.set()
            return original(count)
        vm.parallel_budget.reserve = reserve
        vm.natives['core.time.sleep_ms'] = lambda *args: calls.append(1) or UNIT
        def invoke():
            try:
                outcome.append(self.call(vm))
            except BaseException as error:
                outcome.append(error)
        with vm.parallel_budget.lock:
            host = threading.Thread(target=invoke)
            host.start()
            self.assertTrue(entered.wait(2))
            time.sleep(0.02)
        host.join(2)
        self.assertFalse(host.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], Trap)
        self.assertEqual(outcome[0].code, ops.TRAP_TIMEOUT)
        self.assertEqual(calls, [])
        self.assertEqual(vm.parallel_budget.active, 0)

    def test_both_sleep_loops_clamp_wait_to_inherited_deadline(self):
        from gopyt.temporal import wait_ns
        for mode in ['legacy', 'typed']:
            with self.subTest(mode=mode):
                vm = self.make(depth=0)
                now = [1_000_000_000]
                vm.deadline_ns = now[0] + 5_000_000
                waits = []
                def sleep(seconds):
                    waits.append(seconds)
                    now[0] += round(seconds * 1_000_000_000)
                with patch('gopyt.natives.time.monotonic_ns', side_effect=lambda: now[0]), \
                        patch('gopyt.natives.time.sleep', side_effect=sleep):
                    with self.assertRaises(Trap) as caught:
                        if mode == 'legacy':
                            self.call(vm)
                        else:
                            wait_ns(vm, 200_000_000)
                self.assertEqual(caught.exception.code, ops.TRAP_TIMEOUT)
                self.assertEqual(waits, [0.005])
