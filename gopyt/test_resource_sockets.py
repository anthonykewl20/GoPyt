"""Actual socket lifetime and shared descriptor admission."""
import os
import socket
import threading
import unittest
from unittest.mock import patch

from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
from gopyt.resource_control import ResourceClosedError
from gopyt.resource_descriptors import DescriptorRegistry
from gopyt.resource_sockets import open_socket


class SocketOwnership(unittest.TestCase):
    def test_teardown_during_socket_acquisition(self):
        budget, registry = self.fixture()
        entered, resume = threading.Event(), threading.Event()
        failures = []
        original = socket.socket.__init__
        def initialize(sock, *args):
            entered.set()
            if not resume.wait(3): raise AssertionError('acquisition not resumed')
            original(sock, *args)
        def acquire():
            try:
                open_socket(registry)
            except BaseException as error:
                failures.append(error)
        with patch('socket.socket.__init__', new=initialize):
            thread = threading.Thread(target=acquire)
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertFalse(registry.close())
                self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
            finally:
                resume.set()
                thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ResourceClosedError)
        self.assertTrue(registry.close())
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def fixture(self, capacity=1):
        budget = ResourceBudget(ResourceLimits(0, 0, capacity, 0))
        registry = DescriptorRegistry(budget)
        self.addCleanup(registry.close)
        return budget, registry

    def test_reader_retains_charge_and_blocks_teardown(self):
        budget, registry = self.fixture()
        sock = open_socket(registry)
        fd = sock.fileno()
        reader = sock.makefile('rb', buffering=0)
        try:
            sock.close()
            os.fstat(fd)
            self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
            self.assertFalse(registry.close())
        finally:
            reader.close()
        with self.assertRaises(OSError): os.fstat(fd)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        self.assertTrue(registry.close())
        sock.close()

    def test_admission_shared_with_file_and_failed_construction(self):
        budget, registry = self.fixture()
        owner = registry.open(os.devnull, os.O_RDONLY)
        with patch('gopyt.resource_sockets._Socket.__new__') as create:
            with self.assertRaises(ResourceLimitError): open_socket(registry)
            create.assert_not_called()
        owner.close()
        with self.assertRaises(OSError): open_socket(registry, family=999999)
        self.assertEqual(registry.pending(), 0)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        sock = open_socket(registry)
        with self.assertRaises(ResourceClosedError): sock.detach()
        sock.close()
        self.assertEqual(registry.pending(), 0)

    def test_ambiguous_physical_close_quarantines_without_retry(self):
        budget, registry = self.fixture()
        sock = open_socket(registry)
        real_close = socket.socket._real_close
        calls = []
        def close(item):
            calls.append(item.fileno())
            real_close(item)
            raise OSError('close result uncertain')
        with patch('socket.socket._real_close', new=close):
            with self.assertRaises(OSError): sock.close()
            self.assertFalse(registry.close())
        self.assertEqual(len(calls), 1)
        self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
        self.assertEqual(registry.pending(), 1)
