"""Cancellation at the actual compiled task/native/publication boundary."""
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from gopyt import ops
from gopyt.cli import build
from gopyt.storage import Store
from gopyt.testing import write_pkg
from gopyt.test_vm import module
from gopyt.vm import VM, Cancelled, Trap


class TransactionCancellation(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = str(Path(temp.name).resolve())
        signatures = '''task apply() -> bool | DbError
    effects { database.read, database.write }

task timed() -> list[bool | DbError]
    effects { database.read, database.write, time }
'''
        bodies = '''task apply() -> bool | DbError
    effects { database.read, database.write }
{
    changes = core.list.empty[Change]()
    first = core.list.append[Change](changes, Change { key: "balance"
        expected: some("10")
        value: some("9") })
    both = core.list.append[Change](first, Change { key: "receipt"
        expected: none
        value: some("done") })
    return store.db.compare_exchange_many(both)
}

task timed() -> list[bool | DbError]
    effects { database.read, database.write, time }
{
    return parallel max 1 timeout_ms 20 {
        apply()
    }
}
'''
        write_pkg(self.root, module(signatures, bodies,
            uses='use core.list { empty, append }\nuse core.status { DbError }\nuse store.db { Change, compare_exchange_many }',
            spec_uses='use core.status { DbError }'), fmt=True)
        _, art, self.ids = build(self.root)
        self.vm = VM(art, self.root)
        self.vm.db.put('balance', '10')
        self.cancel = threading.Event()
        self.vm.cancels = (self.cancel,)

    def reconcile(self, committed):
        expected = ['9', 'done'] if committed else ['10', None]
        self.assertEqual(Store(self.root).get_many(['balance', 'receipt']), expected)
        self.cancel.clear()
        self.assertIs(self.vm.call(self.ids['demo.apply'], []), not committed)
        self.assertEqual(Store(self.root).get_many(['balance', 'receipt']), ['9', 'done'])

    def test_cancellation_before_call_has_no_effect(self):
        self.cancel.set()
        with self.assertRaises(Cancelled):
            self.vm.call(self.ids['demo.apply'], [])
        self.reconcile(False)

    def test_cancellation_during_native_does_not_roll_back_commit(self):
        for after in (False, True):
            with self.subTest(after=after):
                self.vm.db.compare_exchange_many([('balance', self.vm.db.get('balance'), '10'),
                                                  ('receipt', self.vm.db.get('receipt'), None)])
                original = os.replace
                def replacing(*args, **kwargs):
                    if not after:
                        self.cancel.set()
                    result = original(*args, **kwargs)
                    if after:
                        self.cancel.set()
                    return result
                with patch('gopyt.storage.os.replace', replacing), self.assertRaises(Cancelled):
                    self.vm.call(self.ids['demo.apply'], [])
                self.reconcile(True)

    def test_cancellation_can_hide_post_replace_db_error(self):
        original = os.fsync
        def syncing(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                self.cancel.set()
                raise OSError('injected directory fsync failure')
            return original(fd)
        with patch('gopyt.storage.os.fsync', syncing), self.assertRaises(Cancelled):
            self.vm.call(self.ids['demo.apply'], [])
        self.reconcile(True)

    def test_lock_timeout_returns_db_error_without_mutation(self):
        self.vm.db._operation_lock.acquire()
        try:
            with patch('gopyt.storage.LOCK_TIMEOUT', 0.01):
                result = self.vm.call(self.ids['demo.apply'], [])
            self.assertEqual(self.vm.type_name(result.type_id), 'core.status.DbError')
        finally:
            self.vm.db._operation_lock.release()
        self.reconcile(False)

    def test_prepublication_failure_with_cancellation_has_no_commit(self):
        def failing(*args):
            self.cancel.set()
            raise OSError('injected snapshot load failure')
        with patch.object(self.vm.db, '_load', failing), self.assertRaises(Cancelled):
            self.vm.call(self.ids['demo.apply'], [])
        self.reconcile(False)

    def test_parallel_timeout_waits_for_admitted_publication_then_traps(self):
        original = os.replace
        observed = []
        clock = [time.monotonic_ns()]
        def replacing(*args, **kwargs):
            # Keep the deadline clock frozen until publication is entered.
            # Then cross the actual inherited deadline and wait for the real
            # coordinator stop event. Admission need not finish within 20 ms
            # of wall time; real wait/join timeouts still bound a broken test.
            clock[0] = self.vm.deadline_ns + 1
            stops = self.vm.cancels
            observed.append(stops[-1].wait(2))
            return original(*args, **kwargs)
        with patch('time.monotonic_ns', side_effect=lambda: clock[0]), \
                patch('gopyt.storage.os.replace', replacing), self.assertRaises(Trap) as raised:
            self.vm.call(self.ids['demo.timed'], [])
        self.assertEqual(raised.exception.code, ops.TRAP_TIMEOUT)
        self.assertEqual(observed, [True])
        self.reconcile(True)


class EncryptedTransactionCancellation(TransactionCancellation):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        key = Path(temp.name).resolve() / 'key'
        key.write_bytes(os.urandom(32))
        key.chmod(0o600)
        env = patch.dict(os.environ, {
            'GOPYT_SECURITY_PROFILE': 'strict',
            'GOPYT_STORE_KEY_FILE': str(key),
            'GOPYT_STORE_KEYRING_FILE': '',
            'GOPYT_STORE_ID': 'transaction-cancellation',
        })
        env.start()
        self.addCleanup(env.stop)
        super().setUp()
