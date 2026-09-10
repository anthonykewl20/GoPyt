"""Publication faults exercise actual files; no simulated durability claims."""
from contextlib import ExitStack
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gopyt import storage
from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
from gopyt.resource_descriptors import DescriptorRegistry


STAGES = ('create', 'partial_write', 'write', 'flush', 'file_sync', 'close', 'replace', 'directory_sync')


def inject(stack, stage, edge, failure):
    """Instrument the real I/O operations without changing their success path."""
    def around(name, function, *args, **kwargs):
        if name == stage and edge == 'before':
            failure()
        result = function(*args, **kwargs)
        if name == stage and edge == 'after':
            failure()
        return result

    original_open, original_fdopen = os.open, os.fdopen
    original_sync, original_replace = os.fsync, os.replace

    def opening(path, flags, *args, **kwargs):
        if isinstance(path, str) and path.startswith('.pending-'):
            return around('create', original_open, path, flags, *args, **kwargs)
        return original_open(path, flags, *args, **kwargs)

    class Writer:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            try:
                around('close', self.stream.close)
            finally:
                self.stream.close()

        def write(self, data):
            if stage == 'partial_write':
                # Leave a genuinely truncated pending snapshot on disk.
                self.stream.write(data[:len(data) // 2])
                self.stream.flush()
                failure()
            return around('write', self.stream.write, data)

        def flush(self):
            return around('flush', self.stream.flush)

        def fileno(self):
            return self.stream.fileno()

    def fdopening(fd, mode, *args, **kwargs):
        stream = original_fdopen(fd, mode, *args, **kwargs)
        return Writer(stream) if mode == 'wb' else stream

    def syncing(fd):
        name = 'directory_sync' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file_sync'
        return around(name, original_sync, fd)

    stack.enter_context(patch('gopyt.storage.os.open', opening))
    stack.enter_context(patch('gopyt.storage.os.fdopen', fdopening))
    stack.enter_context(patch('gopyt.storage.os.fsync', syncing))
    stack.enter_context(patch('gopyt.storage.os.replace',
                              lambda *a, **kw: around('replace', original_replace, *a, **kw)))


def child(root, stage, edge):
    with ExitStack() as stack:
        inject(stack, stage, edge, lambda: os._exit(73))
        storage.Store(root).compare_exchange_many(
            [('left', '10', '9'), ('right', '10', '11'), ('receipt', None, 'done')])
    raise AssertionError('fault was not reached')


class Publication(unittest.TestCase):
    def test_publication_descriptor_admission_and_cleanup(self):
        class Context:
            def check_cancelled(self): pass
        for capacity, fault in ((0, None), (1, 'write'), (1, 'existing'), (1, None)):
            with self.subTest(capacity=capacity, fault=fault), tempfile.TemporaryDirectory() as root:
                context = Context()
                budget = ResourceBudget(ResourceLimits(0, 0, capacity, 0))
                context.descriptors = DescriptorRegistry(budget)
                db = storage.Store(root, context=context)
                target = Path(root, storage.DATABASE)
                target.write_bytes(b'old')
                pending = Path(root, '.pending-fixed')
                if fault == 'existing': pending.write_bytes(b'foreign')
                directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                self.addCleanup(os.close, directory)
                original_replace = os.replace
                def replace(*args, **kwargs):
                    self.assertEqual(budget.snapshot()['used']['descriptors'], 0)
                    return original_replace(*args, **kwargs)
                with ExitStack() as stack:
                    stack.enter_context(patch('gopyt.storage.secrets.token_hex', return_value='fixed'))
                    stack.enter_context(patch('gopyt.storage.os.replace', side_effect=replace))
                    if fault == 'write':
                        def fail(): raise OSError('injected write failure')
                        inject(stack, 'write', 'before', fail)
                    if capacity == 0:
                        with self.assertRaises(ResourceLimitError): db._publish(directory, b'new')
                    elif fault:
                        with self.assertRaises(OSError): db._publish(directory, b'new')
                    else:
                        db._publish(directory, b'new')
                self.assertEqual(target.read_bytes(), b'new' if capacity and fault is None else b'old')
                if fault == 'existing': self.assertEqual(pending.read_bytes(), b'foreign')
                else: self.assertFalse(pending.exists())
                self.assertEqual(budget.snapshot()['active_reservations'], 0)
                self.assertEqual(context.descriptors.pending(), 0)
                self.assertTrue(context.descriptors.close())

    def exercise(self, crash):
        for stage in STAGES:
            for edge in ('before', 'after'):
                # An exception after a successful open invents an API which
                # loses its returned descriptor. Process death covers that edge.
                if stage == 'partial_write' and edge == 'after':
                    continue
                if not crash and stage == 'create' and edge == 'after':
                    continue
                with self.subTest(stage=stage, edge=edge), tempfile.TemporaryDirectory() as temp:
                    root = str(Path(temp).resolve())
                    db = storage.Store(root)
                    db.compare_exchange_many([('left', None, '10'), ('right', None, '10')])
                    keys = ['left', 'right', 'receipt']
                    self.assertEqual(db.get_many(keys), ['10', '10', None])
                    if crash:
                        result = subprocess.run([sys.executable, '-m', __name__, root, stage, edge],
                                                capture_output=True, text=True, timeout=15)
                        self.assertEqual(result.returncode, 73, result.stderr)
                    else:
                        def fail():
                            raise OSError('injected publication failure')
                        with ExitStack() as stack:
                            inject(stack, stage, edge, fail)
                            with self.assertRaises(storage.StorageError):
                                db.compare_exchange_many([('left', '10', '9'),
                                    ('right', '10', '11'), ('receipt', None, 'done')])
                    published = stage == 'directory_sync' or (stage == 'replace' and edge == 'after')
                    expected = ['9', '11', 'done'] if published else ['10', '10', None]
                    # Both a warm reader and a fresh instance must reconcile.
                    self.assertEqual(db.get_many(keys), expected)
                    fresh = storage.Store(root)
                    self.assertEqual(fresh.get_many(keys), expected)
                    self.assertEqual(list(Path(root, storage.DIRECTORY).glob('.pending-*')), [])
                    # Retry with the original receipt condition commits once.
                    self.assertEqual(fresh.compare_exchange_many([('left', '10', '9'),
                        ('right', '10', '11'), ('receipt', None, 'done')]), not published)
                    self.assertEqual(fresh.get_many(keys), ['9', '11', 'done'])

    def test_io_errors_at_publication_edges(self):
        self.exercise(False)

    def test_process_death_at_publication_edges(self):
        self.exercise(True)


class EncryptedPublication(Publication):
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
            'GOPYT_STORE_ID': 'publication-faults',
        })
        env.start()
        self.addCleanup(env.stop)


if __name__ == '__main__':
    child(*sys.argv[1:])
