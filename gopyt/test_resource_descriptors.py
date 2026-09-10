"""Real descriptor ownership, rejected acquisition, races and uncertain close."""
import os
import threading
import unittest
from unittest.mock import patch

from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
from gopyt.resource_control import ResourceClosedError
from gopyt.resource_descriptors import DescriptorRegistry


class DescriptorOwnership(unittest.TestCase):
    def fixture(self, limit=2):
        budget = ResourceBudget(ResourceLimits(0, 0, limit, 0))
        registry = DescriptorRegistry(budget)
        self.addCleanup(registry.close)
        return budget, registry

    def test_admission_before_open_and_exact_idempotent_release(self):
        budget, registry = self.fixture(1)
        owner = registry.open(os.devnull, os.O_RDONLY)
        fd = owner.fileno()
        with patch('gopyt.resource_descriptors.os.open') as opened:
            with self.assertRaises(ResourceLimitError): registry.open(os.devnull, os.O_RDONLY)
            opened.assert_not_called()
        self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
        self.assertTrue(owner.close())
        self.assertTrue(owner.close())
        with self.assertRaises(OSError): os.fstat(fd)
        with self.assertRaises(ResourceClosedError): owner.fileno()
        self.assertEqual(registry.pending(), 0)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_open_and_registration_failures_leave_no_charge(self):
        budget, registry = self.fixture()
        with patch('gopyt.resource_descriptors.os.open', side_effect=OSError('open failed')):
            with self.assertRaises(OSError): registry.open(os.devnull, os.O_RDONLY)
        self.assertEqual(registry.pending(), 0)
        class Reject(dict):
            def __setitem__(self, key, value):
                raise MemoryError('registry admission failed')
        registry._owners = Reject()
        with patch('gopyt.resource_descriptors.os.open') as opened:
            with self.assertRaises(MemoryError): registry.open(os.devnull, os.O_RDONLY)
            opened.assert_not_called()
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_ambiguous_close_never_closes_replacement_descriptor(self):
        budget, registry = self.fixture()
        owner = registry.open(os.devnull, os.O_RDONLY)
        fd = owner.fileno()
        close = os.close
        def ambiguous(number):
            close(number)
            raise OSError('physical close succeeded but response failed')
        with patch('gopyt.resource_descriptors.os.close', side_effect=ambiguous):
            self.assertFalse(owner.close())
        replacement = os.open(os.devnull, os.O_RDONLY)
        try:
            self.assertEqual(replacement, fd)
            with patch('gopyt.resource_descriptors.os.close') as closed:
                self.assertFalse(registry.close())
                self.assertFalse(owner.close())
                closed.assert_not_called()
            os.fstat(replacement)
            self.assertEqual(registry.pending(), 1)
            self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
        finally:
            close(replacement)

    def test_teardown_during_open_retains_then_closes_acquisition(self):
        budget, registry = self.fixture()
        entered = threading.Event()
        resume = threading.Event()
        original_open = os.open
        failures = []
        acquired = []
        def delayed(*args, **kwargs):
            entered.set()
            if not resume.wait(2): raise AssertionError('open not resumed')
            fd = original_open(*args, **kwargs)
            acquired.append(fd)
            return fd
        def worker():
            try: registry.open(os.devnull, os.O_RDONLY)
            except BaseException as error: failures.append(error)
        with patch('gopyt.resource_descriptors.os.open', side_effect=delayed):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(registry.close())
                self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
            finally:
                resume.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ResourceClosedError)
        with self.assertRaises(OSError): os.fstat(acquired[0])
        self.assertTrue(registry.close())
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
