"""Persistent connections consume finite worker occupancy budgets."""
import http.client
import socket
import threading
import time
import unittest
from unittest.mock import patch

from gopyt import test_app_runtime as fixture
from gopyt.values import UNIT


class ConnectionBudget(unittest.TestCase):
    def queued_request(self, port, outcomes):
        try:
            outcomes.append(fixture.request(port))
        except Exception as error:
            outcomes.append(error)

    def wait_queued(self, vm):
        until = time.monotonic() + 2
        while vm.httpd.pending.qsize() != 1 and time.monotonic() < until:
            time.sleep(.001)
        self.assertEqual(vm.httpd.pending.qsize(), 1)

    def test_quota_releases_worker_to_already_queued_client(self):
        with fixture.running_server(MAX_HANDLERS=1, MAX_KEEPALIVE_REQUESTS=2) as (vm, port):
            first = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
            results = []
            second = threading.Thread(target=self.queued_request, args=(port, results))
            try:
                first.request('GET', '/echo/a')
                response = first.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                second.start()
                self.wait_queued(vm)
                first.request('GET', '/echo/b')
                response = first.getresponse()
                self.assertEqual(response.getheader('Connection'), 'close')
                self.assertEqual((response.status, response.read()), (200, b'{"amount":1}'))
                second.join(2)
                self.assertFalse(second.is_alive(), 'queued client still waits for peer teardown')
                self.assertEqual(results, [(200, b'{"amount":3}')])
            finally:
                first.close()
                if second.ident is not None:
                    second.join(4)

    def test_pipelined_excess_requests_never_dispatch(self):
        with fixture.running_server(MAX_HANDLERS=1, MAX_KEEPALIVE_REQUESTS=2) as (vm, port):
            calls = []
            vm.natives = dict(vm.natives)
            vm.natives['core.log.write'] = lambda *args: calls.append('called') or UNIT
            with socket.create_connection(('127.0.0.1', port), timeout=3) as client:
                client.sendall(b'GET /echo/abc HTTP/1.1\r\nHost: localhost\r\n\r\n' * 3)
                chunks = []
                while True:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
            wire = b''.join(chunks)
            self.assertEqual(wire.count(b'HTTP/1.1 200 '), 2)
            self.assertEqual(wire.count(b'{"amount":3}'), 2)
            self.assertEqual(wire.count(b'Connection: close\r\n'), 1)
            self.assertEqual(len(calls), 2)
            self.assertEqual(fixture.request(port), (200, b'{"amount":3}'))

    def test_new_requests_cannot_extend_connection_deadline(self):
        with fixture.running_server(MAX_HANDLERS=1, REQUEST_TIMEOUT_SECONDS=20,
                                    CONNECTION_TIMEOUT_SECONDS=10) as (vm, port):
            deadlines = []
            vm.natives = dict(vm.natives)
            vm.natives['core.log.write'] = lambda *args: deadlines.append(vm.deadline_ns) or UNIT
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
            try:
                for _ in range(3):
                    connection.request('GET', '/echo/abc')
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                self.assertEqual(len(deadlines), 3)
                self.assertEqual(len(set(deadlines)), 1)
            finally:
                connection.close()

    def test_idle_connection_lifetime_releases_queued_client(self):
        with fixture.running_server(MAX_HANDLERS=1, CONNECTION_TIMEOUT_SECONDS=1,
                                    REQUEST_TIMEOUT_SECONDS=5) as (vm, port):
            first = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
            outcomes = []
            second = threading.Thread(target=self.queued_request, args=(port, outcomes))
            try:
                first.request('GET', '/echo/abc')
                response = first.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                # Freeze distinct admission lifetimes: the old connection keeps
                # its captured one-second budget; the queued client gets five.
                with patch('gopyt.server.CONNECTION_TIMEOUT_SECONDS', 5):
                    second.start()
                    self.wait_queued(vm)
                    second.join(3)
                self.assertFalse(second.is_alive())
                self.assertEqual(outcomes, [(200, b'{"amount":3}')])
            finally:
                first.close()
                if second.ident is not None:
                    second.join(4)

    def test_non_handler_responses_also_consume_request_quota(self):
        with fixture.running_server(MAX_HANDLERS=1, MAX_KEEPALIVE_REQUESTS=2) as (_, port):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
            try:
                connection.request('GET', '/missing')
                response = connection.getresponse()
                self.assertEqual((response.status, response.read()), (404, b''))
                connection.request('GET', '/echo/abc')
                response = connection.getresponse()
                self.assertEqual(response.getheader('Connection'), 'close')
                self.assertEqual((response.status, response.read()), (200, b'{"amount":3}'))
            finally:
                connection.close()
