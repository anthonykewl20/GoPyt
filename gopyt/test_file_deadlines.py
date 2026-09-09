"""Compiled file I/O cancellation checkpoints and partial-progress semantics."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from gopyt import files
from gopyt.cli import build
from gopyt.testing import write_pkg
from gopyt.test_vm import module
from gopyt.vm import VM, Cancelled, Trap


class FileDeadlines(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = str(Path(temporary.name).resolve())
        signatures = '''task persist(data: bytes) -> unit | IoError
    effects { filesystem.write }

task load() -> bytes | IoError | NotFound
    effects { filesystem.read }
'''
        bodies = '''task persist(data: bytes) -> unit | IoError
    effects { filesystem.write }
{
    return core.file.write("data.txt", data)
}

task load() -> bytes | IoError | NotFound
    effects { filesystem.read }
{
    return core.file.read("data.txt")
}
'''
        status = 'use core.status { IoError, NotFound }'
        write_pkg(self.root, module(signatures, bodies,
                  uses=status + '\nuse core.file { read, write }', spec_uses=status), fmt=True)
        _, art, self.ids = build(self.root)
        self.vm = VM(art, self.root)
        self.cancel = threading.Event()
        self.vm.cancels = (self.cancel,)
        self.path = Path(self.root, 'data.txt')

    def call(self, task, *args):
        return self.vm.call(self.ids['demo.' + task], list(args))

    def test_expired_directory_resolution_cannot_create_or_truncate(self):
        original = files.parent_directory
        @contextmanager
        def resolving(*args, **kwargs):
            with original(*args, **kwargs) as opened:
                self.vm.deadline_ns = time.monotonic_ns() - 1
                yield opened
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.path.write_bytes(b'before')
                try:
                    with patch('gopyt.files.parent_directory', resolving), self.assertRaises(Trap) as caught:
                        self.call('persist', b'after')
                    self.assertEqual(caught.exception.code, 6)
                finally:
                    self.vm.deadline_ns = None
                self.assertEqual(self.path.exists(), existing)
                if existing:
                    self.assertEqual(self.path.read_bytes(), b'before')

    def test_cancelled_open_closes_descriptor_before_truncate(self):
        self.path.write_bytes(b'before')
        original = os.fstat
        opened = []
        def inspected(fd):
            result = original(fd)
            opened.append(fd)
            self.cancel.set()
            return result
        with patch('gopyt.files.os.fstat', inspected), self.assertRaises(Cancelled):
            self.call('persist', b'after')
        self.assertEqual(self.path.read_bytes(), b'before')
        self.assertEqual(len(opened), 1)
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    def test_short_raw_reads_and_writes_preserve_all_bytes(self):
        original = files.regular_file
        counts = {'read': 0, 'write': 0}
        @contextmanager
        def short(*args, **kwargs):
            self.assertEqual(kwargs['buffering'], 0)
            with original(*args, **kwargs) as stream:
                class Proxy:
                    def read(self, size):
                        counts['read'] += 1
                        return stream.read(min(size, 7))
                    def write(self, data):
                        counts['write'] += 1
                        return stream.write(data[:7])
                yield Proxy()
        data = bytes(range(256)) * 4
        with patch('gopyt.files.regular_file', short):
            self.call('persist', data)
            self.assertEqual(self.call('load'), data)
        self.assertEqual(self.path.read_bytes(), data)
        self.assertGreater(counts['read'], 1)
        self.assertGreater(counts['write'], 1)

    def test_cancellation_after_partial_write_stops_further_progress(self):
        self.path.write_bytes(b'before')
        original = files.regular_file
        writes = []
        @contextmanager
        def interrupted(*args, **kwargs):
            with original(*args, **kwargs) as stream:
                class Proxy:
                    def write(inner, data):
                        writes.append(len(data))
                        result = stream.write(data[:7])
                        self.cancel.set()
                        return result
                yield Proxy()
        with patch('gopyt.files.regular_file', interrupted), self.assertRaises(Cancelled):
            self.call('persist', b'after-partial-write')
        self.assertEqual(writes, [19])
        self.assertEqual(self.path.read_bytes(), b'after-p')

    def test_cancelled_read_stops_before_next_chunk(self):
        self.path.write_bytes(b'available')
        original = files.regular_file
        reads = []
        @contextmanager
        def interrupted(*args, **kwargs):
            with original(*args, **kwargs) as stream:
                class Proxy:
                    def read(inner, size):
                        reads.append(size)
                        result = stream.read(1)
                        self.cancel.set()
                        return result
                yield Proxy()
        with patch('gopyt.files.regular_file', interrupted), self.assertRaises(Cancelled):
            self.call('load')
        self.assertEqual(reads, [65_536])
        self.assertEqual(self.path.read_bytes(), b'available')

    def test_nonprogress_is_error_and_allocation_limit_remains_a_trap(self):
        self.path.write_bytes(b'123456789')
        with patch('gopyt.natives.ops.MAX_ALLOC', 8), self.assertRaises(Trap) as caught:
            self.call('load')
        self.assertEqual(caught.exception.code, 14)
        original = files.regular_file
        for mode, value in (('read', None), ('write', None), ('write', 0)):
            with self.subTest(mode=mode, value=value):
                @contextmanager
                def stopped(*args, **kwargs):
                    with original(*args, **kwargs):
                        class Proxy:
                            def read(self, size):
                                return value
                            def write(self, data):
                                return value
                        yield Proxy()
                with patch('gopyt.files.regular_file', stopped):
                    result = self.call('load') if mode == 'read' else self.call('persist', b'new')
                self.assertEqual(self.vm.type_name(result.type_id), 'core.status.IoError')
