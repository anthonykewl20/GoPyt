"""Authenticated replay rejection and authority/publication crash boundaries."""
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import hashlib
import fcntl
import threading
import time
import unittest
from unittest.mock import patch

from gopyt.storage import Store, StorageError, DIRECTORY, DATABASE


def crash_writer(root, boundary):
    original = os.replace
    def replace(source, destination, *args, **kwargs):
        if boundary == 'before_anchor' and destination == 'record.json':
            os._exit(77)
        if boundary == 'before_snapshot' and destination == DATABASE:
            os._exit(77)
        result = original(source, destination, *args, **kwargs)
        if boundary == 'after_snapshot' and destination == DATABASE:
            os._exit(77)
        return result
    with patch('gopyt.storage.os.replace', replace):
        Store(root).put('balance', '9')


def crash_at_sync(root, number, after):
    original, count = os.fsync, 0
    def sync(fd):
        nonlocal count
        count += 1
        if count == number and not after: os._exit(78)
        result = original(fd)
        if count == number and after: os._exit(78)
        return result
    with patch('gopyt.storage.os.fsync', sync):
        Store(root).put('balance', '9')


def competing_writer(root, result):
    try:
        result.send(Store(root).compare_exchange('balance', '10', '9'))
    except BaseException as error:
        result.send(type(error).__name__)
    finally:
        result.close()


class RollbackAuthority(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'app'
        self.root.mkdir()
        self.anchor = self.base / 'anchor'
        self.anchor.mkdir(mode=0o700)
        key = self.base / 'key'
        key.write_bytes(os.urandom(32))
        key.chmod(0o600)
        env = patch.dict(os.environ, {'GOPYT_SECURITY_PROFILE':'strict',
            'GOPYT_STORE_KEY_FILE':str(key), 'GOPYT_STORE_KEYRING_FILE':'',
            'GOPYT_STORE_ID':'rollback-test', 'GOPYT_STORE_ANCHOR_DIR':str(self.anchor)})
        env.start()
        self.addCleanup(env.stop)
        self.store = Store(self.root)
        self.snapshot = self.root / DIRECTORY / DATABASE

    def record(self):
        return json.loads((self.anchor / 'record.json').read_text())

    def test_enrollment_required_and_cannot_overwrite(self):
        with self.assertRaises(StorageError):
            self.store.put('balance', '10')
        self.assertTrue(self.store.enroll_anchor())
        self.assertEqual(self.record()['generation'], 0)
        self.assertIsNone(self.store.get('balance'))
        with self.assertRaises(StorageError):
            self.store.enroll_anchor()
        self.store.put('balance', '10')
        self.assertEqual(self.record()['generation'], 1)
        self.assertEqual(Store(self.root).get('balance'), '10')

    def test_replay_rejects_fresh_cached_reads_and_write(self):
        self.store.enroll_anchor()
        self.store.put('balance', '10')
        old = self.snapshot.read_bytes()
        self.store.put('balance', '9')
        current = self.snapshot.read_bytes()
        self.assertEqual(self.store.get('balance'), '9')
        self.snapshot.write_bytes(old)
        for store in (self.store, Store(self.root)):
            with self.assertRaises(StorageError): store.get('balance')
            with self.assertRaises(StorageError): store.put('balance', '8')
        self.assertEqual(self.snapshot.read_bytes(), old)
        self.snapshot.write_bytes(current)
        self.assertEqual(self.store.get('balance'), '9')

    def test_admitted_snapshot_recovered_after_replace_failure(self):
        self.store.enroll_anchor()
        self.store.put('balance', '10')
        original = os.replace
        def fail(source, destination, *args, **kwargs):
            if destination == DATABASE:
                raise OSError('injected publication failure')
            return original(source, destination, *args, **kwargs)
        with patch('gopyt.storage.os.replace', fail):
            with self.assertRaises(StorageError): self.store.put('balance', '9')
        self.assertEqual(self.record()['generation'], 2)
        self.assertTrue(list((self.root / DIRECTORY).glob('.pending-*')))
        self.assertEqual(Store(self.root).get('balance'), '9')
        self.assertFalse(list((self.root / DIRECTORY).glob('.pending-*')))

    def test_missing_admitted_staging_fails_closed(self):
        self.store.enroll_anchor()
        self.store.put('balance', '10')
        original = os.replace
        def fail(source, destination, *args, **kwargs):
            if destination == DATABASE: raise OSError('injected publication failure')
            return original(source, destination, *args, **kwargs)
        with patch('gopyt.storage.os.replace', fail):
            with self.assertRaises(StorageError): self.store.put('balance', '9')
        for path in (self.root / DIRECTORY).glob('.pending-*'): path.unlink()
        with self.assertRaises(StorageError): Store(self.root).get('balance')
        self.assertEqual(self.record()['generation'], 2)

    def test_authority_failure_before_advance_preserves_old_snapshot(self):
        self.store.enroll_anchor()
        self.store.put('balance', '10')
        old = self.snapshot.read_bytes()
        original = os.replace
        def fail(source, destination, *args, **kwargs):
            if destination == 'record.json': raise OSError('injected authority failure')
            return original(source, destination, *args, **kwargs)
        with patch('gopyt.rollback.os.replace', fail):
            with self.assertRaises(StorageError): self.store.put('balance', '9')
        self.assertEqual(self.record()['generation'], 1)
        self.assertEqual(self.snapshot.read_bytes(), old)
        self.assertEqual(Store(self.root).get('balance'), '10')

    def test_missing_authority_rejects_cached_read(self):
        self.store.enroll_anchor()
        self.store.put('balance', '10')
        self.assertEqual(self.store.get('balance'), '10')
        (self.anchor / 'record.json').unlink()
        with self.assertRaises(StorageError): self.store.get('balance')
        self.assertFalse((self.anchor / 'record.json').exists())

    def test_substituted_identity_and_record_reject(self):
        self.store.enroll_anchor()
        self.store.put('balance', '10')
        with patch.dict(os.environ, {'GOPYT_STORE_ID':'other'}):
            with self.assertRaises(StorageError): self.store.get('balance')
        record = self.record()
        record['stage'] = '../escape'
        (self.anchor / 'record.json').write_text(json.dumps(record))
        with self.assertRaises(StorageError): self.store.get('balance')

    def test_enroll_existing_authenticated_snapshot(self):
        with patch.dict(os.environ, {'GOPYT_STORE_ANCHOR_DIR':''}):
            Store(self.root).put('balance', '10')
        self.store.enroll_anchor()
        self.assertEqual(self.store.get('balance'), '10')
        self.assertEqual(self.record()['generation'], 0)
        self.store.rekey()
        self.assertEqual(self.record()['generation'], 1)
        self.assertEqual(Store(self.root).get('balance'), '10')

    def test_process_crash_publication_boundaries(self):
        context = multiprocessing.get_context('spawn')
        for boundary, expected in [('before_anchor', '10'), ('before_snapshot', '9'), ('after_snapshot', '9')]:
            with self.subTest(boundary=boundary):
                # One independently enrolled store/authority for each crash.
                app = self.base / boundary
                app.mkdir()
                anchor = self.base / (boundary + '-anchor')
                anchor.mkdir(mode=0o700)
                with patch.dict(os.environ, {'GOPYT_STORE_ANCHOR_DIR':str(anchor)}):
                    store = Store(app)
                    store.enroll_anchor()
                    store.put('balance', '10')
                    child = context.Process(target=crash_writer, args=(str(app), boundary))
                    child.start()
                    try:
                        child.join(10)
                        self.assertEqual(child.exitcode, 77)
                    finally:
                        if child.is_alive(): child.kill(); child.join()
                        child.close()
                    self.assertEqual(Store(app).get('balance'), expected)
                    self.assertFalse(list((app / DIRECTORY).glob('.pending-*')))

    def test_competing_processes_advance_once(self):
        self.store.enroll_anchor()
        self.store.put('balance', '10')
        context = multiprocessing.get_context('spawn')
        children, readers = [], []
        try:
            for _ in range(4):
                reader, writer = context.Pipe(duplex=False)
                child = context.Process(target=competing_writer, args=(str(self.root), writer))
                child.start()
                writer.close()
                children.append(child)
                readers.append(reader)
            results = []
            for reader in readers:
                self.assertTrue(reader.poll(10))
                results.append(reader.recv())
            for child in children:
                child.join(10)
                self.assertEqual(child.exitcode, 0)
            self.assertEqual(sorted(results), [False, False, False, True])
            self.assertEqual(self.record()['generation'], 2)
            self.assertEqual(Store(self.root).get('balance'), '9')
        finally:
            for reader in readers: reader.close()
            for child in children:
                if child.is_alive(): child.kill(); child.join()
                child.close()

    def test_authorized_restore_advances_and_rejects_prior_snapshots(self):
        self.store.enroll_anchor()
        self.store.put('balance', '10')
        backup = self.snapshot.read_bytes()
        self.store.put('balance', '9')
        before = self.snapshot.read_bytes()
        self.snapshot.write_bytes(backup)  # Ordinary access must fail first.
        with self.assertRaises(StorageError): self.store.get('balance')
        self.assertEqual(self.store.anchor_status()['generation'], 2)
        result = self.store.restore_anchor(backup, expected_generation=2, reason='approved recovery')
        self.assertEqual(result['generation'], 3)
        self.assertEqual(self.store.get('balance'), '10')
        restored = self.snapshot.read_bytes()
        self.assertNotEqual(restored, backup)
        receipt = json.loads((self.anchor / 'restore-3.json').read_text())
        self.assertEqual(receipt['backup_digest'], hashlib.sha256(backup).hexdigest())
        self.assertEqual(receipt['reason'], 'approved recovery')
        for replay in (backup, before):
            self.snapshot.write_bytes(replay)
            with self.assertRaises(StorageError): Store(self.root).get('balance')
        self.snapshot.write_bytes(restored)
        self.store.put('balance', '8')
        self.assertEqual(self.record()['restore'], result['restore'])
        self.assertEqual(json.loads((self.anchor / 'restore-3.json').read_text()), receipt)

    def test_invalid_or_stale_restore_cannot_change_state(self):
        self.store.enroll_anchor()
        self.store.put('balance', '10')
        backup = self.snapshot.read_bytes()
        record = self.record()
        for data, generation, reason in [(backup, 0, 'stale'), (b'invalid', 1, 'corrupt'),
                                          (backup, 1, ''), (backup, True, 'bool')]:
            with self.subTest(generation=generation, reason=reason):
                with self.assertRaises(StorageError):
                    self.store.restore_anchor(data, expected_generation=generation, reason=reason)
                self.assertEqual(self.snapshot.read_bytes(), backup)
                self.assertEqual(self.record(), record)
        self.assertFalse(list(self.anchor.glob('restore-*.json')))

    def test_restore_recovers_after_receipt_publication_failure(self):
        self.store.enroll_anchor()
        self.store.put('balance', '10')
        backup = self.snapshot.read_bytes()
        self.store.put('balance', '9')
        original = os.replace
        def fail(source, destination, *args, **kwargs):
            if destination == 'restore-3.json': raise OSError('receipt publication failed')
            return original(source, destination, *args, **kwargs)
        with patch('gopyt.rollback.os.replace', fail):
            with self.assertRaises(StorageError):
                self.store.restore_anchor(backup, expected_generation=2, reason='receipt recovery')
        self.assertEqual(self.record()['generation'], 3)
        self.assertEqual(Store(self.root).get('balance'), '10')
        self.assertEqual(json.loads((self.anchor / 'restore-3.json').read_text())['generation'], 3)

    def test_operator_cli_restore_missing_snapshot(self):
        def command(*args):
            return subprocess.run([sys.executable, '-m', 'gopyt.store_admin', '--root', str(self.root), *args],
                                  capture_output=True, text=True)
        result = command('enroll')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.store.put('balance', '10')
        backup = self.base / 'backup'
        backup.write_bytes(self.snapshot.read_bytes())
        self.snapshot.unlink()
        result = command('status')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['generation'], 1)
        result = command('restore', '--backup', str(backup), '--expected-generation', '1', '--reason', 'operator restore')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['generation'], 2)
        self.assertEqual(Store(self.root).get('balance'), '10')
        stale = command('restore', '--backup', str(backup), '--expected-generation', '1', '--reason', 'stale')
        self.assertEqual(stale.returncode, 2)

    def test_process_crashes_at_each_durability_barrier(self):
        context = multiprocessing.get_context('spawn')
        # read-side authority barrier, staging file/dir, authority file/dir,
        # snapshot directory. A process exit is not a power-loss simulation.
        for number in range(1, 7):
            for after in (False, True):
                with self.subTest(barrier=number, after=after):
                    name = f'sync-{number}-{after}'
                    root, anchor = self.base / name, self.base / (name + '-anchor')
                    root.mkdir(); anchor.mkdir(mode=0o700)
                    with patch.dict(os.environ, {'GOPYT_STORE_ANCHOR_DIR':str(anchor)}):
                        store = Store(root); store.enroll_anchor(); store.put('balance', '10')
                        child = context.Process(target=crash_at_sync, args=(str(root), number, after))
                        child.start()
                        try:
                            child.join(10)
                            self.assertEqual(child.exitcode, 78)
                        finally:
                            if child.is_alive(): child.kill(); child.join()
                            child.close()
                        self.assertEqual(Store(root).get('balance'), '9' if number >= 5 else '10')

    def test_private_authority_files_and_symlinks(self):
        self.store.enroll_anchor(); self.store.put('balance', '10')
        record = self.anchor / 'record.json'
        for path in (self.anchor, record, self.anchor / 'lock'):
            mode = path.stat().st_mode & 0o777
            path.chmod(mode | 0o040)
            try:
                with self.assertRaises(StorageError): self.store.get('balance')
            finally:
                path.chmod(mode)
        saved = self.base / 'saved-record'
        record.rename(saved); record.symlink_to(saved)
        with self.assertRaises(StorageError): self.store.get('balance')

    def test_compiled_native_replay_is_typed_error(self):
        from gopyt.test_vm import module
        from gopyt.testing import write_pkg
        from gopyt.cli import build
        from gopyt.vm import VM
        from gopyt.values import Record
        files = module('task read() -> str | NotFound | DbError\n    effects { database.read }\n',
            'task read() -> str | NotFound | DbError\n    effects { database.read }\n{\n    return store.db.get("balance")\n}\n',
            uses='use core.status { DbError, NotFound }\nuse store.db { get }',
            spec_uses='use core.status { DbError, NotFound }')
        write_pkg(self.root, files, fmt=True)
        _, artifact, ids = build(self.root)
        self.store.enroll_anchor(); self.store.put('balance', '10')
        old = self.snapshot.read_bytes(); self.store.put('balance', '9')
        vm = VM(artifact, self.root)
        self.assertEqual(vm.call(ids['demo.read'], []), '9')
        self.snapshot.write_bytes(old)
        result = vm.call(ids['demo.read'], [])
        self.assertIsInstance(result, Record)
        self.assertEqual(vm.type_name(result.type_id), 'core.status.DbError')

    def test_held_authority_lock_observes_context_cancellation(self):
        from gopyt.vm import Cancelled
        self.store.enroll_anchor(); self.store.put('balance', '10')
        cancel, blocked = threading.Event(), threading.Event()
        class Context:
            deadline_ns = None
            def check_cancelled(self):
                if cancel.is_set(): raise Cancelled()
        context = Context()
        store = Store(self.root, context=context)
        fd = os.open(self.anchor / 'lock', os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        original, outcomes = fcntl.flock, []
        def flock(*args):
            try: return original(*args)
            except BlockingIOError:
                blocked.set(); raise
        def run():
            try: outcomes.append(store.get('balance'))
            except BaseException as error: outcomes.append(error)
        caller = threading.Thread(target=run)
        try:
            with patch('gopyt.rollback.fcntl.flock', flock):
                caller.start()
                self.assertTrue(blocked.wait(5))
                cancel.set(); caller.join(5)
                self.assertFalse(caller.is_alive())
                self.assertEqual(len(outcomes), 1)
                self.assertIsInstance(outcomes[0], Cancelled)
        finally:
            os.close(fd)
            if caller.ident is not None: caller.join(5)
        self.assertEqual(self.store.get('balance'), '10')


if __name__ == '__main__':
    unittest.main()
