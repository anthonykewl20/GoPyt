"""Real HTTP connection teardown must not require cyclic garbage collection."""
import gc
import http.client
from http.server import BaseHTTPRequestHandler
import queue
import socket
import threading
import unittest
import weakref
from unittest.mock import patch

from gopyt.test_app_runtime import running_server


class HttpRetention(unittest.TestCase):
    def test_failed_bind_releases_listener_charge(self):
        from gopyt.resource_budget import ResourceBudget, ResourceLimits
        from gopyt.resource_descriptors import DescriptorRegistry
        from gopyt.server import _BudgetedHTTPServer
        budget = ResourceBudget(ResourceLimits(0, 0, 1, 0))
        registry = DescriptorRegistry(budget)
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            with self.assertRaises(OSError):
                _BudgetedHTTPServer(occupied.getsockname(), BaseHTTPRequestHandler,
                                    descriptors=registry)
        self.assertEqual(budget.snapshot()['active_reservations'], 0)
        self.assertTrue(registry.close())

    def test_listener_and_accept_share_descriptor_capacity(self):
        from gopyt.resource_budget import ResourceBudget, ResourceLimits, ResourceLimitError
        from gopyt.resource_descriptors import DescriptorRegistry
        from gopyt.server import _BudgetedHTTPServer
        for capacity in (0, 1, 2):
            with self.subTest(capacity=capacity):
                budget = ResourceBudget(ResourceLimits(0, 0, capacity, 0))
                registry = DescriptorRegistry(budget)
                if capacity == 0:
                    with self.assertRaises(ResourceLimitError):
                        _BudgetedHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler,
                                            descriptors=registry)
                else:
                    server = _BudgetedHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler,
                                                 descriptors=registry)
                    try:
                        with socket.create_connection(server.server_address, timeout=2):
                            if capacity == 1:
                                with self.assertRaises(OSError): server.get_request()
                                self.assertEqual(registry.pending(), 1)
                            else:
                                request, address = server.get_request()
                                self.assertEqual(budget.snapshot()['used']['descriptors'], 2)
                                server.shutdown_request(request)
                                self.assertEqual(registry.pending(), 1)
                    finally:
                        server.server_close()
                self.assertTrue(registry.close())
                self.assertEqual(budget.snapshot()['active_reservations'], 0)

    def test_finished_handlers_are_reclaimed_with_cyclic_gc_disabled(self):
        finished = queue.Queue()
        original_finish = BaseHTTPRequestHandler.finish

        def observe_finish(handler):
            # Retain a weak reference only, after the real stream teardown.
            # This observes lifecycle behavior without inspecting the reader,
            # deadline representation, or other implementation internals.
            original_finish(handler)
            reclaimed = threading.Event()
            reference = weakref.ref(handler, lambda unused: reclaimed.set())
            finished.put((reference, reclaimed))

        with running_server(MAX_HANDLERS=2) as (vm, port):
            enabled = gc.isenabled()
            gc.disable()
            try:
                references = []
                with patch.object(BaseHTTPRequestHandler, 'finish', observe_finish):
                    for _ in range(6):
                        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                        try:
                            connection.request('GET', '/echo/abc', headers={'Connection': 'close'})
                            response = connection.getresponse()
                            self.assertEqual((response.status, response.read()), (200, b'{"amount":3}'))
                        finally:
                            connection.close()
                        references.append(finished.get(timeout=2))
                    for reference, reclaimed in references:
                        self.assertTrue(reclaimed.wait(2), 'finished HTTP handler retained until cyclic GC')
                        self.assertIsNone(reference())
            finally:
                if enabled:
                    gc.enable()
        self.assertEqual(vm.resource_budget.snapshot()['used']['descriptors'], 0)


if __name__ == '__main__':
    unittest.main()
