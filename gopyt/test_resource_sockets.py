"""Actual socket lifetime and shared descriptor admission."""
import os
import socket
import ssl
import threading
import unittest
from unittest.mock import patch

from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
from gopyt.resource_control import ResourceClosedError
from gopyt.resource_descriptors import DescriptorRegistry
from gopyt.resource_sockets import open_socket, accept_socket, wrap_tls


class SocketOwnership(unittest.TestCase):
    def test_tls_teardown_during_descriptor_handoff(self):
        from gopyt.resource_sockets import _Socket
        for edge in ('before', 'after'):
            with self.subTest(edge=edge):
                budget, registry = self.fixture()
                sock = open_socket(registry)
                fd = sock.fileno()
                entered, resume = threading.Event(), threading.Event()
                outcomes = []
                original = _Socket.detach
                def detach(item):
                    result = original(item) if edge == 'after' else None
                    entered.set()
                    if not resume.wait(3): raise AssertionError('transfer not resumed')
                    return original(item) if edge == 'before' else result
                def transfer():
                    try:
                        outcomes.append(wrap_tls(sock, ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
                                                 server_hostname='localhost',
                                                 do_handshake_on_connect=False))
                    except BaseException as error:
                        outcomes.append(error)
                with patch.object(_Socket, 'detach', new=detach):
                    worker = threading.Thread(target=transfer)
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(3))
                        self.assertFalse(registry.close())
                        os.fstat(fd)
                        self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
                    finally:
                        resume.set()
                        worker.join(4)
                self.assertFalse(worker.is_alive())
                self.assertEqual(len(outcomes), 1)
                self.assertIsInstance(outcomes[0], ResourceClosedError)
                self.assertTrue(registry.close())
                self.assertEqual(budget.snapshot()['active_reservations'], 0)
                with self.assertRaises(OSError): os.fstat(fd)

    def test_tls_failure_on_each_side_of_detach_closes_once(self):
        from gopyt.resource_sockets import _Socket
        for edge in ('before', 'after'):
            with self.subTest(edge=edge):
                budget, registry = self.fixture()
                sock = open_socket(registry)
                fd = sock.fileno()
                original_detach = _Socket.detach
                original_close = socket.socket._real_close
                closed = []
                targets = []
                def detach(item):
                    targets.append(item._resource_owner.transfer_target)
                    if edge == 'after': original_detach(item)
                    raise MemoryError('handoff interrupted')
                def close(item):
                    if item.fileno() >= 0: closed.append(item.fileno())
                    original_close(item)
                with patch.object(_Socket, 'detach', new=detach), \
                        patch.object(socket.socket, '_real_close', new=close):
                    with self.assertRaises(MemoryError):
                        wrap_tls(sock, ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
                                 server_hostname='localhost', do_handshake_on_connect=False)
                self.assertEqual(closed, [fd])
                self.assertEqual(targets[0].fileno(), -1)
                self.assertTrue(registry.close())
                self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_tls_transfer_retains_one_charge_until_reader_close(self):
        budget, registry = self.fixture()
        sock = open_socket(registry)
        fd = sock.fileno()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        wrapped = wrap_tls(sock, context, server_hostname='localhost',
                           do_handshake_on_connect=False)
        self.assertEqual(sock.fileno(), -1)
        self.assertEqual(wrapped.fileno(), fd)
        sock.close()
        self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
        reader = wrapped.makefile('rb', buffering=0)
        try:
            wrapped.close()
            os.fstat(fd)
            self.assertFalse(registry.close())
        finally:
            reader.close()
        self.assertTrue(registry.close())
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_tls_handshake_and_validation_failure_release_owner(self):
        for hostname in (None, 'localhost'):
            with self.subTest(hostname=hostname):
                budget, registry = self.fixture()
                sock = open_socket(registry)
                sock.settimeout(2)
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1', 0))
                    listener.listen()
                    sock.connect(listener.getsockname())
                    peer, _ = listener.accept()
                    with peer:
                        peer.sendall(b'HTTP/1.0 400 Bad Request\r\n\r\n')
                        peer.shutdown(socket.SHUT_WR)
                        with self.assertRaises((ValueError, ssl.SSLError)):
                            wrap_tls(sock, ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
                                     server_hostname=hostname)
                self.assertEqual(registry.pending(), 0)
                self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_accept_admission_and_reader_ownership(self):
        budget, registry = self.fixture(2)
        listener = open_socket(registry)
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(2)
        held = registry.open(os.devnull, os.O_RDONLY)
        with socket.create_connection(listener.getsockname(), timeout=2) as client:
            with self.assertRaises(ResourceLimitError): listener.accept()
            held.close()
            accepted, address = listener.accept()
            self.assertEqual(address[0], '127.0.0.1')
            accepted.settimeout(2)
            client.sendall(b'x')
            reader = accepted.makefile('rb', buffering=0)
            try:
                accepted.close()
                self.assertEqual(reader.read(1), b'x')
                self.assertEqual(budget.snapshot()['used']['descriptors'], 2)
            finally:
                reader.close()
        listener.close()
        self.assertEqual(registry.pending(), 0)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_accept_failure_releases_unused_admission(self):
        budget, registry = self.fixture(2)
        listener = open_socket(registry)
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.setblocking(False)
        with self.assertRaises(BlockingIOError): accept_socket(registry, listener)
        self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
        self.assertEqual(registry.pending(), 1)
        listener.close()

    def test_accepted_descriptor_closed_if_adoption_fails(self):
        budget, registry = self.fixture(2)
        listener = open_socket(registry)
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(2)
        acquired = []
        def fail(sock, *args, **kwargs):
            acquired.append(kwargs['fileno'])
            raise MemoryError('socket initialization failed')
        with socket.create_connection(listener.getsockname(), timeout=2):
            with patch('socket.socket.__init__', new=fail):
                with self.assertRaises(MemoryError): listener.accept()
        self.assertEqual(len(acquired), 1)
        with self.assertRaises(OSError): os.fstat(acquired[0])
        self.assertEqual(registry.pending(), 1)
        self.assertEqual(budget.snapshot()['used']['descriptors'], 1)
        listener.close()

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
