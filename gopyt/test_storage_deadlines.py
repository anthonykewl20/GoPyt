"""Compiled storage admission under contended locks and publication cancellation."""
import fcntl
import os
from pathlib import Path
import stat
import threading
import time
import unittest
from unittest.mock import patch

from gopyt import test_transaction_cancellation as fixtures
from gopyt.storage import Store, DIRECTORY
from gopyt.vm import Cancelled, Trap


class StorageDeadlines(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TransactionCancellation()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_contended_local_and_process_locks_stop_before_peer_release(self):
        fixture = self.fixture
        for kind in ('local', 'process'):
            for condition in ('deadline', 'cancel'):
                with self.subTest(kind=kind, condition=condition):
                    entered = threading.Event()
                    outcomes = []
                    original = fixture.vm.db._lock_remaining
                    def remaining(deadline):
                        if kind == 'local':
                            entered.set()
                        return original(deadline)
                    flock = fcntl.flock
                    def lock(*args):
                        try:
                            return flock(*args)
                        except BlockingIOError:
                            entered.set()
                            raise
                    def run():
                        fixture.vm.cancels = (fixture.cancel,)
                        if condition == 'deadline':
                            fixture.vm.deadline_ns = time.monotonic_ns() + 200_000_000
                        try:
                            outcomes.append(fixture.vm.call(fixture.ids['demo.apply'], []))
                        except BaseException as error:
                            outcomes.append(error)
                    if kind == 'local':
                        fixture.vm.db._operation_lock.acquire()
                        release = fixture.vm.db._operation_lock.release
                    else:
                        fd = os.open(Path(fixture.root, DIRECTORY, 'lock'), os.O_RDWR)
                        fcntl.flock(fd, fcntl.LOCK_EX)
                        release = lambda: os.close(fd)
                    caller = threading.Thread(target=run)
                    with patch.object(fixture.vm.db, '_lock_remaining', remaining), \
                            patch('gopyt.storage.fcntl.flock', lock):
                        caller.start()
                        try:
                            self.assertTrue(entered.wait(2))
                            if condition == 'cancel':
                                fixture.cancel.set()
                            caller.join(2)
                            self.assertFalse(caller.is_alive(), 'operation waited for held lock')
                            self.assertEqual(len(outcomes), 1)
                            self.assertIsInstance(outcomes[0], Cancelled if condition == 'cancel' else Trap)
                            if condition == 'deadline':
                                self.assertEqual(outcomes[0].code, 6)
                        finally:
                            release()
                            caller.join(4)
                            fixture.cancel.clear()
                    self.assertEqual(Store(fixture.root).get_many(['balance', 'receipt']), ['10', None])
                    self.assertFalse(list(Path(fixture.root, DIRECTORY).glob('.pending-*')))
        fixture.reconcile(False)

    def test_cancelled_snapshot_preparation_cannot_publish(self):
        fixture = self.fixture
        original = fixture.vm.db._load
        def load(*args):
            original(*args)
            fixture.cancel.set()
        with patch.object(fixture.vm.db, '_load', load), self.assertRaises(Cancelled):
            fixture.vm.call(fixture.ids['demo.apply'], [])
        self.assertFalse(list(Path(fixture.root, DIRECTORY).glob('.pending-*')))
        fixture.reconcile(False)

    def test_deadline_after_temporary_fsync_discards_unpublished_snapshot(self):
        fixture = self.fixture
        original = os.fsync
        def sync(fd):
            result = original(fd)
            if stat.S_ISREG(os.fstat(fd).st_mode):
                fixture.vm.deadline_ns = time.monotonic_ns() - 1
            return result
        try:
            with patch('gopyt.storage.os.fsync', sync), self.assertRaises(Trap) as caught:
                fixture.vm.call(fixture.ids['demo.apply'], [])
            self.assertEqual(caught.exception.code, 6)
        finally:
            fixture.vm.deadline_ns = None
        self.assertFalse(list(Path(fixture.root, DIRECTORY).glob('.pending-*')))
        fixture.reconcile(False)


class EncryptedStorageDeadlines(StorageDeadlines):
    def setUp(self):
        self.fixture = fixtures.EncryptedTransactionCancellation()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
