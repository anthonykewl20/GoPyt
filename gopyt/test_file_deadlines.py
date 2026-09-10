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
    def test_mapped_backing_and_file_access_share_descriptor_capacity(self):
        import sys
        from gopyt.resource_budget import ResourceBudget, ResourceLimits
        from gopyt.resource_buffer import Buffer
        if sys.platform != 'linux' or not hasattr(os, 'memfd_create'):
            self.skipTest('Linux sealed mapping profile')
        budget = ResourceBudget(ResourceLimits(64, 32, 2, 4))
        self.path.write_bytes(b'before')
        with VM(self.vm.art, self.root, resource_budget=budget) as vm:
            mapped = Buffer.map_bytes(budget, b'abc')
            try:
                self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
                with self.assertRaises(Trap) as caught:
                    vm.call(self.ids['demo.persist'], [b'after'])
                self.assertEqual(caught.exception.code, 14)
                self.assertEqual(self.path.read_bytes(), b'before')
                self.assertEqual(vm.descriptors.pending(), 0)
                self.assertEqual(mapped.read(0, 3), b'abc')
            finally:
                mapped.close()
            vm.call(self.ids['demo.persist'], [b'after'])
            self.assertEqual(vm.call(self.ids['demo.load'], []), b'after')
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_descriptor_budget_rejects_before_creation_or_truncation(self):
        from gopyt.resource_budget import ResourceBudget, ResourceLimits
        for capacity in (0, 1, 2):
            for existing in (False, True):
                with self.subTest(capacity=capacity, existing=existing):
                    if self.path.exists(): self.path.unlink()
                    if existing: self.path.write_bytes(b'before')
                    budget = ResourceBudget(ResourceLimits(64, 0, capacity, 0))
                    with VM(self.vm.art, self.root, resource_budget=budget) as vm:
                        if capacity < 2:
                            with self.assertRaises(Trap) as caught:
                                vm.call(self.ids['demo.persist'], [b'after'])
                            self.assertEqual(caught.exception.code, 14)
                            self.assertEqual(self.path.exists(), existing)
                            if existing: self.assertEqual(self.path.read_bytes(), b'before')
                        else:
                            vm.call(self.ids['demo.persist'], [b'after'])
                            self.assertEqual(vm.call(self.ids['demo.load'], []), b'after')
                            self.assertEqual(budget.snapshot()['peak']['descriptors'], 2)
                        self.assertEqual(vm.descriptors.pending(), 0)
                        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_parent_close_failure_retains_charge_and_unwinds_child(self):
        from gopyt.resource_budget import ResourceBudget, ResourceLimits
        from gopyt.values import Record
        self.path.write_bytes(b'before')
        budget = ResourceBudget(ResourceLimits(64, 0, 2, 0))
        vm = VM(self.vm.art, self.root, resource_budget=budget)
        close = os.close
        opened = []
        original_open = os.open
        calls = []
        def tracked(*args, **kwargs):
            fd = original_open(*args, **kwargs)
            opened.append(fd)
            return fd
        def uncertain(fd):
            close(fd)
            calls.append(fd)
            if len(calls) == 1: raise OSError('injected parent close failure')
        with patch('gopyt.resource_descriptors.os.open', side_effect=tracked):
            with patch('gopyt.resource_descriptors.os.close', side_effect=uncertain):
                result = vm.call(self.ids['demo.persist'], [b'after'])
                self.assertIsInstance(result, Record)
                self.assertFalse(vm.close())
                self.assertFalse(vm.close())
        self.assertEqual(len(opened), 2)
        self.assertEqual(calls, opened)
        for fd in opened:
            with self.assertRaises(OSError): os.fstat(fd)
        self.assertEqual(vm.descriptors.pending(), 1)
        self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
        self.assertEqual(self.path.read_bytes(), b'before')

    def test_file_read_uses_shared_native_budget_and_preserves_trap_contract(self):
        from gopyt.resource_budget import ResourceBudget, ResourceLimits
        self.path.write_bytes(b'abcdefgh')
        self.vm = VM(self.vm.art, self.root, resource_budget=ResourceBudget(ResourceLimits(32, 0, 2, 0)))
        self.assertEqual(self.call('load'), b'abcdefgh')
        self.assertEqual(self.vm.resource_budget.snapshot()['active_reservations'], 0)
        self.vm = VM(self.vm.art, self.root, resource_budget=ResourceBudget(ResourceLimits(4, 0, 2, 0)))
        with self.assertRaises(Trap) as caught:
            self.call('load')
        self.assertEqual(caught.exception.code, 14)
        self.assertEqual(self.vm.resource_budget.snapshot()['active_reservations'], 0)
        self.assertEqual(self.path.read_bytes(), b'abcdefgh')

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
