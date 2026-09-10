"""Real sealed backing, reservation peaks, and failed initialization cleanup."""
import os
import sys
import unittest
from unittest.mock import patch

from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
from gopyt.resource_buffer import Buffer
from gopyt.resource_control import ResourceClosedError
from gopyt.resource_mapping import MappingInitializationError


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'memfd_create'), 'Linux sealed profile')
class MappedResources(unittest.TestCase):
    def budget(self, *, descriptors=2):
        return ResourceBudget(ResourceLimits(16384, 8192, descriptors, 8))

    def test_real_mapping_nested_views_and_exact_budget_return(self):
        budget = self.budget()
        data = bytes(range(256)) * 16
        owner = Buffer.map_bytes(budget, data)
        self.addCleanup(owner.close)
        self.assertEqual(budget.snapshot()['used'],
                         dict(native_bytes=4096, mapped_bytes=4096, descriptors=1, handles=1))
        self.assertEqual(budget.snapshot()['peak']['descriptors'], 2)
        parent = owner.view(16, 128)
        child = parent.view(4, 32)
        self.addCleanup(parent.close)
        self.addCleanup(child.close)
        parent.close()
        self.assertEqual(child.read(0, 32), data[20:52])
        with self.assertRaises(ValueError): child.write(0, b'x')
        owner.freeze()
        self.assertEqual(owner.read(0, len(data)), data)
        with owner._control.lease() as storage:
            mapping = storage.mapping
            self.assertTrue(owner.close() is False)
            self.assertFalse(mapping.closed)
            self.assertEqual(budget.snapshot()['used']['mapped_bytes'], 4096)
            with self.assertRaises(ResourceClosedError): child.read(0, 1)
        self.assertTrue(mapping.closed)
        child.close()
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_descriptor_peak_rejected_before_memfd_acquisition(self):
        budget = self.budget(descriptors=1)
        with patch('gopyt.resource_mapping.os.memfd_create') as create:
            with self.assertRaises(ResourceLimitError): Buffer.map_bytes(budget, b'abcd')
            create.assert_not_called()
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def mapping_descriptors(self):
        """Descriptors in this process that still refer to a mapping backing.

        Counting all of `/proc/self/fd` would measure the whole process, so
        another test's daemon thread opening any file moves that number while
        saying nothing about this path. The backing memfd carries its own name,
        so the leak this test is about can be identified directly instead.
        """
        found = []
        for name in os.listdir('/proc/self/fd'):
            try:
                target = os.readlink('/proc/self/fd/' + name)
            except OSError:
                continue  # closed by another thread while this list was read
            if 'gopyt-buffer' in target:
                found.append(target)
        return found

    def test_a_live_mapping_is_visible_to_the_descriptor_check(self):
        """Without this, a leak check that finds nothing proves nothing."""
        budget = self.budget()
        self.assertEqual(self.mapping_descriptors(), [])
        owner = Buffer.map_bytes(budget, b'abcd')
        # The original descriptor is closed after mapping; the one that remains
        # is mmap's own duplicate, which the reservation already accounts for.
        self.assertEqual(len(self.mapping_descriptors()), 1)
        self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
        owner.close()
        self.assertEqual(self.mapping_descriptors(), [])
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_each_acquisition_failure_releases_budget_and_descriptors(self):
        for target in ('os.memfd_create', 'os.write', 'fcntl.fcntl', 'mmap.mmap'):
            with self.subTest(target=target):
                budget = self.budget()
                self.assertEqual(self.mapping_descriptors(), [])
                with patch('gopyt.resource_mapping.' + target, side_effect=OSError('injected')):
                    with self.assertRaises(OSError): Buffer.map_bytes(budget, b'abcd')
                self.assertEqual(budget.snapshot()['active_reservations'], 0)
                self.assertEqual(budget.snapshot()['used']['descriptors'], 0)
                self.assertEqual(self.mapping_descriptors(), [])

    def test_partial_writes_and_cancellation_after_mapping(self):
        budget = self.budget()
        write = os.write
        with patch('gopyt.resource_mapping.os.write', side_effect=lambda fd, data: write(fd, data[:1])):
            owner = Buffer.map_bytes(budget, b'abcd')
        self.assertEqual(owner.read(0, 4), b'abcd')
        owner.close()

        class Context:
            calls = 0
            def check_cancelled(self):
                self.calls += 1
                if self.calls == 6:
                    raise RuntimeError('cancel after mapping')

        context = Context()
        before = len(os.listdir('/proc/self/fd'))
        with self.assertRaisesRegex(RuntimeError, 'cancel after mapping'):
            Buffer.map_bytes(budget, b'abcd', context=context)
        self.assertEqual(context.calls, 6)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_ambiguous_original_close_is_not_retried_on_reused_number(self):
        budget = self.budget()
        original_close = os.close
        closed = []

        def ambiguous_close(fd):
            original_close(fd)
            closed.append(fd)
            raise OSError('injected error after physical close')

        with patch('gopyt.resource_mapping.os.close', side_effect=ambiguous_close):
            with self.assertRaises(MappingInitializationError) as caught:
                Buffer.map_bytes(budget, b'abcd')
        owner = caught.exception.owner
        replacement = os.open('/dev/null', os.O_RDONLY)
        try:
            self.assertEqual(replacement, closed[0])
            with patch('gopyt.resource_mapping.os.close') as close:
                self.assertFalse(owner.close())
                close.assert_not_called()
            os.fstat(replacement)
            self.assertEqual(owner._control.snapshot()['state'], 'closing')
            self.assertEqual(budget.snapshot()['used']['descriptors'], 2)
        finally:
            original_close(replacement)


class UnsupportedMapping(unittest.TestCase):
    def test_unsupported_profile_and_empty_input_do_not_allocate(self):
        budget = ResourceBudget(ResourceLimits(32, 32, 2, 2))
        with patch('gopyt.resource_mapping.sys.platform', 'unsupported'):
            with self.assertRaises(OSError): Buffer.map_bytes(budget, b'abc')
        with self.assertRaises(ValueError): Buffer.map_bytes(budget, b'')
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
