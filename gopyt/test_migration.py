"""Initial encryption has a durable recovery copy and explicit source identity."""
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from gopyt.security_config import MAGIC
from gopyt.storage import Store, StorageError, DIRECTORY, DATABASE


def _crash_migration(root, backup, digest, environment, point):
    os.environ.clear()
    os.environ.update(environment)
    real_link, real_replace, real_fsync = os.link, os.replace, os.fsync
    count = 0

    def link(*args, **kwargs):
        if point == 'before-link':
            os._exit(77)
        result = real_link(*args, **kwargs)
        if point == 'after-link':
            os._exit(77)
        return result

    def replace(*args, **kwargs):
        if point == 'before-replace':
            os._exit(77)
        result = real_replace(*args, **kwargs)
        if point == 'after-replace':
            os._exit(77)
        return result

    def fsync(*args, **kwargs):
        nonlocal count
        count += 1
        if point == f'before-fsync-{count}':
            os._exit(77)
        result = real_fsync(*args, **kwargs)
        if point == f'after-fsync-{count}':
            os._exit(77)
        return result

    with patch('os.link', link), patch('os.replace', replace), patch('os.fsync', fsync):
        Store(root).migrate(backup=backup, expected_digest=digest)


class Migration(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'app'
        self.root.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith('GOPYT_')}
        env.update(GOPYT_SECURITY_PROFILE='development', GOPYT_STORE_ID='migration-test')
        setting = patch.dict(os.environ, env, clear=True)
        setting.start()
        self.addCleanup(setting.stop)
        Store(self.root).put('balance', '10')
        self.path = self.root / DIRECTORY / DATABASE
        self.original = self.path.read_bytes()
        self.digest = hashlib.sha256(self.original).hexdigest()
        self.key = self.base / 'key'
        self.material = os.urandom(32)
        self.key.write_bytes(self.material)
        self.key.chmod(0o600)
        os.environ.update(GOPYT_SECURITY_PROFILE='strict', GOPYT_STORE_KEY_FILE=str(self.key))
        self.backup = self.base / 'recovery'

    def migrate(self, **kwargs):
        return Store(self.root).migrate(backup=self.backup, expected_digest=kwargs.get('digest', self.digest))

    def test_cli_and_independent_decryption_and_retry(self):
        result = subprocess.run([sys.executable, '-m', 'gopyt.store_admin', 'migrate',
            '--root', str(self.root), '--backup', str(self.backup), '--expected-digest', self.digest],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'committed')
        ciphertext = self.path.read_bytes()
        self.assertEqual(ciphertext, self.backup.read_bytes())
        from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV
        prefix = b'GOPYT-SIV1\0'
        data = AESGCMSIV(self.material).decrypt(ciphertext[len(prefix):len(prefix)+12],
            ciphertext[len(prefix)+12:], prefix+b'migration-test')
        self.assertEqual(data, self.original)
        db = sqlite3.connect(':memory:')
        try:
            db.deserialize(data)
            self.assertEqual(db.execute('SELECT key,value FROM kv ORDER BY key').fetchall(), [('balance','10')])
        finally:
            db.close()
        self.migrate()
        self.assertEqual(self.path.read_bytes(), ciphertext)
        fresh = subprocess.run([sys.executable, '-c',
            'from gopyt.storage import Store;import sys;assert Store(sys.argv[1]).get("balance")=="10"', str(self.root)],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(fresh.returncode, 0, fresh.stderr)

    def test_bad_digest_and_wrong_key_do_not_overwrite(self):
        with self.assertRaises(StorageError):
            self.migrate(digest='0'*64)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.path.read_bytes(), self.original)
        self.migrate()
        ciphertext = self.path.read_bytes()
        self.key.write_bytes(os.urandom(32))
        with self.assertRaises(StorageError):
            self.migrate()
        self.assertEqual(self.path.read_bytes(), ciphertext)
        self.assertEqual(self.backup.read_bytes(), ciphertext)

    def test_failed_active_publication_and_missing_file_recover(self):
        with patch('gopyt.storage.os.replace', side_effect=OSError('injected')):
            with self.assertRaises(StorageError):
                self.migrate()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertTrue(self.backup.read_bytes().startswith(MAGIC))
        self.path.unlink()
        self.migrate()
        self.assertEqual(self.path.read_bytes(), self.backup.read_bytes())
        self.assertEqual(Store(self.root).get('balance'), '10')

    def test_existing_bad_backup_and_incompatible_settings_fail_closed(self):
        self.backup.write_bytes(b'corrupt')
        self.backup.chmod(0o600)
        with self.assertRaises(StorageError):
            self.migrate()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.backup.read_bytes(), b'corrupt')
        self.backup.unlink()
        with patch.dict(os.environ, {'GOPYT_STORE_ANCHOR_DIR': str(self.base/'anchor')}):
            with self.assertRaises(StorageError):
                self.migrate()
        self.assertFalse(self.backup.exists())
        with self.assertRaises(StorageError):
            Store(self.root).get('balance')

    def test_process_crashes_resume_the_same_operation(self):
        points = ['before-link', 'after-link', 'before-replace', 'after-replace']
        points += [f'{side}-fsync-{number}' for number in range(1, 5) for side in ('before', 'after')]
        for point in points:
            with self.subTest(point=point):
                # Reset only this completed child trial's private fixture state.
                self.path.write_bytes(self.original)
                for path in self.base.iterdir():
                    if path.name == 'recovery' or path.name.startswith('.gopyt-migration-'):
                        path.unlink()
                process = multiprocessing.get_context('spawn').Process(target=_crash_migration,
                    args=(str(self.root), str(self.backup), self.digest, dict(os.environ), point))
                process.start()
                try:
                    process.join(15)
                    self.assertFalse(process.is_alive())
                    self.assertEqual(process.exitcode, 77)
                finally:
                    if process.is_alive():
                        process.terminate()
                        process.join()
                    process.close()
                self.migrate()
                self.assertEqual(self.path.read_bytes(), self.backup.read_bytes())
                self.assertEqual(Store(self.root).get('balance'), '10')

    def test_schema_values_and_corrupt_source_are_not_published(self):
        for kind in ('schema', 'value', 'corrupt'):
            with self.subTest(kind=kind):
                db = sqlite3.connect(':memory:')
                try:
                    db.deserialize(self.original)
                    if kind == 'schema':
                        db.execute('CREATE TABLE unexpected (value TEXT)')
                    elif kind == 'value':
                        db.execute("UPDATE kv SET value=x'00ff'")
                    db.commit()
                    data = db.serialize() if kind != 'corrupt' else b'not a SQLite database'
                finally:
                    db.close()
                self.path.write_bytes(data)
                with self.assertRaises(StorageError):
                    self.migrate(digest=hashlib.sha256(data).hexdigest())
                self.assertEqual(self.path.read_bytes(), data)
                self.assertFalse(self.backup.exists())

    def test_unsafe_recovery_paths_do_not_publish(self):
        target = self.base / 'unrelated'
        target.write_bytes(b'keep')
        target.chmod(0o600)
        self.backup.symlink_to(target)
        with self.assertRaises(StorageError):
            self.migrate()
        self.backup.unlink()
        os.link(target, self.backup)
        with self.assertRaises(StorageError):
            self.migrate()
        self.backup.unlink()
        self.base.chmod(0o755)
        try:
            with self.assertRaises(StorageError):
                self.migrate()
        finally:
            self.base.chmod(0o700)
        with self.assertRaises(StorageError):
            Store(self.root).migrate(backup=self.root/'backup', expected_digest=self.digest)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(target.read_bytes(), b'keep')

    def test_recovery_lock_obeys_deadline_without_publication(self):
        stage = '.gopyt-migration-' + hashlib.sha256(self.backup.name.encode()).hexdigest()
        fd = os.open(self.base/(stage+'.lock'), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with patch('gopyt.storage.LOCK_TIMEOUT', .03):
                start = time.monotonic()
                with self.assertRaises(StorageError):
                    self.migrate()
                self.assertLess(time.monotonic()-start, 1)
            self.assertEqual(self.path.read_bytes(), self.original)
            self.assertFalse(self.backup.exists())
        finally:
            os.close(fd)
        self.migrate()
        self.assertEqual(Store(self.root).get('balance'), '10')

    def test_cancellation_before_active_publication_preserves_recovery(self):
        class Stop(Exception):
            pass
        class Context:
            deadline_ns = None
            cancelled = False
            def check_cancelled(self):
                if self.cancelled:
                    raise Stop()
        context = Context()
        original = os.fsync
        calls = 0
        def fsync(fd):
            nonlocal calls
            calls += 1
            original(fd)
            if calls == 2:
                context.cancelled = True
        with patch('os.fsync', fsync), self.assertRaises(Stop):
            Store(self.root, context=context).migrate(backup=self.backup, expected_digest=self.digest)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertTrue(self.backup.read_bytes().startswith(MAGIC))
        self.migrate()
        self.assertEqual(Store(self.root).get('balance'), '10')
