"""Real preparation children and source publication under inherited contexts."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import multiprocessing
from multiprocessing.process import BaseProcess
import os
from pathlib import Path
import signal
import socket
import threading
import time
import unittest
from unittest.mock import patch

from gopyt import evolve, transaction
from gopyt import test_native_admission as fixtures
from gopyt import test_evolve as pipeline
from gopyt.cli import build, make_vm
from gopyt.manifest import package_digest
from gopyt.vm import Cancelled, Trap


def held_worker(writer, root, max_candidates, module, traces):
    if traces['mode'] == 'ignore':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if traces['mode'] == 'partial':
        writer.sock.sendall((100).to_bytes(4, 'big') + b'{')
    Path(root, 'worker-ready').write_text(str(os.getpid()))
    while not Path(root, 'worker-release').exists():
        time.sleep(.01)
    try:
        writer.send(evolve.Outcome('NoChange'))
    finally:
        writer.close()


def wire_worker(writer, root, max_candidates, module, traces):
    try:
        if traces is None:
            writer.send(evolve.Outcome('NoChange'))
        else:
            for offset in range(0, len(traces), 7):
                writer.sock.sendall(traces[offset:offset + 7])
    finally:
        writer.close()


class EvolutionDeadlines(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NativeAdmission()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.vm = self.fixture.vm
        self.root = self.fixture.root
        self.cancel = threading.Event()
        self.children = {p.pid for p in multiprocessing.active_children()}

    def invoke(self):
        self.vm.evolve_last = 0
        return self.vm.call(self.fixture.ids['demo.harden'], [])

    def sources(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in Path(self.root).rglob('*.gopyt')}

    @contextmanager
    def resources(self):
        pairs, exits = [], []
        original_pair, original_close = socket.socketpair, BaseProcess.close
        def pair(*args, **kwargs):
            result = original_pair(*args, **kwargs)
            pairs.append(result)
            return result
        def close(process):
            exits.append(process.exitcode)
            return original_close(process)
        with patch('gopyt.evolve.socket.socketpair', pair), patch.object(BaseProcess, 'close', close):
            try:
                yield exits
            finally:
                self.assertTrue(pairs)
                self.assertTrue(all(sock.fileno() == -1 for pair in pairs for sock in pair))
                self.assertTrue(exits)
                self.assertEqual({p.pid for p in multiprocessing.active_children()} - self.children, set())
                self.assertFalse(self.vm.evolve_in_flight)

    def test_started_and_partial_workers_observe_cancellation_and_deadline(self):
        for mode, condition in (('hold', 'cancel'), ('partial', 'deadline'), ('ignore', 'cancel')):
            with self.subTest(mode=mode, condition=condition):
                ready = Path(self.root, 'worker-ready')
                release = Path(self.root, 'worker-release')
                ready.unlink(missing_ok=True)
                release.unlink(missing_ok=True)
                before = self.sources()
                outcomes = []
                expire = threading.Event()
                original = self.vm.check_cancelled
                def check():
                    if expire.is_set():
                        self.vm.deadline_ns = time.monotonic_ns() - 1
                    original()
                def run():
                    self.vm.cancels = (self.cancel,)
                    try:
                        outcomes.append(self.invoke())
                    except BaseException as error:
                        outcomes.append(error)
                with patch('gopyt.evolve._prepare_worker', held_worker), \
                        patch.object(self.vm.observe, 'snapshot', return_value={'mode': mode}), \
                        patch.object(self.vm, 'check_cancelled', check), self.resources() as exits:
                    caller = threading.Thread(target=run)
                    caller.start()
                    try:
                        until = time.monotonic() + 5
                        while not ready.exists() and caller.is_alive() and time.monotonic() < until:
                            time.sleep(.01)
                        self.assertTrue(ready.exists(), outcomes)
                        if condition == 'cancel':
                            self.cancel.set()
                        else:
                            expire.set()
                        caller.join(3)
                        self.assertFalse(caller.is_alive(), 'preparation outlived context')
                        self.assertEqual(len(outcomes), 1)
                        self.assertIsInstance(outcomes[0], Cancelled if condition == 'cancel' else Trap)
                        if condition == 'deadline':
                            self.assertEqual(outcomes[0].code, 6)
                    finally:
                        self.cancel.set()
                        release.write_text('release')
                        caller.join(5)
                        self.cancel.clear()
                    if mode == 'ignore':
                        self.assertEqual(exits, [-signal.SIGKILL])
                self.assertEqual(before, self.sources())

    def test_own_budget_is_data_and_inherited_deadline_is_a_trap(self):
        for inherited in (False, True):
            with self.subTest(inherited=inherited):
                with patch('gopyt.evolve._prepare_worker', held_worker), \
                        patch.object(self.vm.observe, 'snapshot', return_value={'mode': 'hold'}), \
                        patch.object(self.vm, 'evolve_bounds', return_value=(2, 30_000 if inherited else 100, 32)), \
                        self.resources():
                    try:
                        if inherited:
                            self.vm.deadline_ns = time.monotonic_ns() + 100_000_000
                            with self.assertRaises(Trap) as caught:
                                self.invoke()
                            self.assertEqual(caught.exception.code, 6)
                        else:
                            result = self.invoke()
                            self.assertEqual(self.vm.type_name(result.type_id), 'core.evolve.EvolveError')
                            self.assertEqual(result.fields, ['timeout'])
                    finally:
                        self.vm.deadline_ns = None

    def test_valid_fragmented_result_and_invalid_frames(self):
        valid = json.dumps({'kind': 'NoChange', 'digest': '', 'message': '', 'candidate_digest': ''}).encode()
        duplicate = b'{"kind":"NoChange","kind":"Prepared","digest":"","message":"","candidate_digest":""}'
        utf16 = valid.decode().encode('utf-16')
        frames = [(len(utf16).to_bytes(4, 'big') + utf16, False), (None, True), (len(valid).to_bytes(4, 'big') + valid, True),
                  ((evolve.MAX_OUTCOME_BYTES + 1).to_bytes(4, 'big'), False),
                  ((10).to_bytes(4, 'big') + b'{}', False),
                  (len(duplicate).to_bytes(4, 'big') + duplicate, False),
                  (len(valid).to_bytes(4, 'big') + valid + b'extra', False)]
        for frame, accepted in frames:
            with self.subTest(frame=frame), patch('gopyt.evolve._prepare_worker', wire_worker), \
                    patch.object(self.vm.observe, 'snapshot', return_value=frame), self.resources():
                result = self.invoke()
                self.assertEqual(self.vm.type_name(result.type_id),
                                 'core.evolve.NoChange' if accepted else 'core.evolve.EvolveError')

    def test_start_failure_before_and_after_spawn_closes_owned_resources(self):
        original = BaseProcess.start
        for started in (False, True):
            def start(process):
                if started:
                    original(process)
                raise OSError('injected startup failure')
            with self.subTest(started=started), patch.object(BaseProcess, 'start', start), \
                    patch('gopyt.evolve._prepare_worker', held_worker), \
                    patch.object(self.vm.observe, 'snapshot', return_value={'mode': 'hold'}), self.resources():
                result = self.invoke()
                self.assertEqual(self.vm.type_name(result.type_id), 'core.evolve.EvolveError')
                self.assertEqual(result.fields, ['OSError'])

    def test_apply_lock_context_never_enters_recovery(self):
        for condition in ('cancel', 'deadline'):
            with self.subTest(condition=condition):
                self.apply_lock_wait(condition)

    def apply_lock_wait(self, condition):
        fd = os.open(Path(self.root, transaction.LOCK), os.O_RDWR)
        holder = os.fdopen(fd, 'rb')
        self.addCleanup(holder.close)
        fcntl.flock(fd, fcntl.LOCK_EX)
        outcomes, attempted = [], threading.Event()
        original = fcntl.flock
        def flock(*args):
            try:
                return original(*args)
            except BlockingIOError:
                attempted.set()
                raise
        def run():
            self.vm.cancels = (self.cancel,)
            try:
                if condition == 'deadline':
                    self.vm.deadline_ns = time.monotonic_ns() + 200_000_000
                evolve._apply(self.root, evolve.Outcome('Prepared'), context=self.vm)
            except BaseException as error:
                outcomes.append(error)
        with patch('fcntl.flock', flock), patch('gopyt.transaction.recover') as recover:
            caller = threading.Thread(target=run)
            caller.start()
            try:
                self.assertTrue(attempted.wait(2))
                if condition == 'cancel':
                    self.cancel.set()
                caller.join(2)
                self.assertFalse(caller.is_alive())
                self.assertEqual(len(outcomes), 1)
                self.assertIsInstance(outcomes[0], Cancelled if condition == 'cancel' else Trap)
                if condition == 'deadline':
                    self.assertEqual(outcomes[0].code, 6)
                recover.assert_not_called()
            finally:
                holder.close()
                caller.join(5)
                self.cancel.clear()
        with transaction.guard(self.root):
            pass

    def test_recovery_finishes_before_observing_cancellation(self):
        path = Path(self.root, 'gopyt.lock')
        before = path.read_bytes()
        with transaction.guard(self.root):
            with patch('gopyt.transaction.remove', side_effect=OSError('held committed journal')):
                with self.assertRaises(OSError):
                    transaction.commit(self.root, [('gopyt.lock', before, before)])
        self.assertTrue(Path(self.root, transaction.JOURNAL).exists())
        original = transaction.recover
        self.vm.cancels = (self.cancel,)
        def recover(root):
            self.cancel.set()
            original(root)
        with patch('gopyt.transaction.recover', recover), self.assertRaises(Cancelled):
            with transaction.guard(self.root, context=self.vm):
                self.fail('cancelled recovery admitted new work')
        self.assertFalse(Path(self.root, transaction.JOURNAL).exists())
        self.assertEqual(path.read_bytes(), before)

    def prepared(self):
        fixture = pipeline.ThroughTheVm()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        prog, art, ids = build(fixture.root)
        vm = make_vm(fixture.root, prog, art, ids)
        plan = evolve._prepare(fixture.root, 2, module='auth', traces=[])
        self.assertEqual(plan.kind, 'Prepared')
        return fixture.root, vm, plan

    def test_cancellation_during_revalidation_cannot_publish(self):
        root, vm, plan = self.prepared()
        before = package_digest(root)
        vm.cancels = (self.cancel,)
        original = evolve.package_digest
        def digest(path):
            result = original(path)
            if path == plan.message:
                self.cancel.set()
            return result
        with patch('gopyt.evolve.package_digest', digest), \
                patch('gopyt.evolve.commit', wraps=evolve.commit) as commit, self.assertRaises(Cancelled):
            evolve._apply(root, plan, context=vm)
        commit.assert_not_called()
        self.assertEqual(package_digest(root), before)
        self.assertFalse(Path(root, transaction.JOURNAL).exists())

    def test_entered_commit_finishes_before_timeout_is_reported(self):
        root, vm, plan = self.prepared()
        before = package_digest(root)
        original = evolve.commit
        def commit(*args, **kwargs):
            vm.deadline_ns = time.monotonic_ns() - 1
            return original(*args, **kwargs)
        try:
            with patch('gopyt.evolve.commit', commit), self.assertRaises(Trap) as caught:
                evolve._apply(root, plan, context=vm)
            self.assertEqual(caught.exception.code, 6)
        finally:
            vm.deadline_ns = None
        self.assertNotEqual(package_digest(root), before)
        self.assertEqual(package_digest(root), plan.candidate_digest)
        self.assertFalse(Path(root, transaction.JOURNAL).exists())
        build(root)

    def test_own_wave_budget_bounds_apply_lock_wait(self):
        fd = os.open(Path(self.root, transaction.LOCK), os.O_RDWR)
        with os.fdopen(fd, 'rb'):
            fcntl.flock(fd, fcntl.LOCK_EX)
            with patch('gopyt.evolve._prepare_child', return_value=evolve.Outcome('Prepared')), \
                    patch('gopyt.transaction.recover') as recover:
                result = evolve.propose(self.root, True, 2, timeout_ms=100)
            self.assertEqual((result.kind, result.message), ('EvolveError', 'timeout'))
            recover.assert_not_called()
        with transaction.guard(self.root):
            pass
