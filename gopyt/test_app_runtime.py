"""Independent wire-level HTTP probes motivated by the ticket application.

These assert protocol outcomes and server recovery, without using the handler
implementation as the expected-result oracle. Small worker/queue limits make
exhaustion reproducible without opening thousands of sockets.
"""
import contextlib
import http.client
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from gopyt.cli import build, make_vm
from gopyt.testing import write_pkg
import gopyt.test_vm as fixtures


@contextlib.contextmanager
def running_server(**limits):
    with contextlib.ExitStack() as stack:
        for name, value in limits.items():
            stack.enter_context(patch('gopyt.server.' + name, value))
        root = stack.enter_context(tempfile.TemporaryDirectory())
        write_pkg(root, fixtures.API_FILES)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        stack.enter_context(patch.dict(os.environ, {'GOPYT_HTTP_ADDR': f'127.0.0.1:{port}'}))
        prog, art, ids = build(root)
        vm = make_vm(root, prog, art, ids)
        errors = []
        def serve():
            try:
                vm.call(ids['api.serve'], [])
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while getattr(vm, 'httpd', None) is None and time.monotonic() < deadline:
            time.sleep(.01)
        if getattr(vm, 'httpd', None) is None:
            raise AssertionError(f'server did not start: {errors!r}')
        try:
            yield vm, port
        finally:
            vm.httpd.shutdown()
            thread.join(5)
            if thread.is_alive():
                raise AssertionError('server did not join its children within five seconds')
            if errors:
                raise AssertionError(f'server crashed: {errors!r}')


def wire(port, request, half_close=False):
    with socket.create_connection(('127.0.0.1', port), timeout=2) as connection:
        connection.sendall(request)
        if half_close:
            connection.shutdown(socket.SHUT_WR)
        response = http.client.HTTPResponse(connection)
        response.begin()
        return response.status, response.read()


def request(port, method='GET', path='/echo/abc', body=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
    try:
        connection.request(method, path, body=body)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


class ApplicationHttpRuntime(unittest.TestCase):
    def test_partial_worker_start_failure_closes_listener_and_joins_workers(self):
        from gopyt.server import serve
        for error_type in (RuntimeError, OSError):
            for fail_at in (1, 2):
                with self.subTest(error=error_type.__name__, fail_at=fail_at), \
                        tempfile.TemporaryDirectory() as root:
                    write_pkg(root, fixtures.API_FILES)
                    prog, art, ids = build(root)
                    vm = make_vm(root, prog, art, ids)
                    servers, workers = [], []
                    original_start = threading.Thread.start
                    def start(worker):
                        servers.append(worker._target.__self__)
                        workers.append(worker)
                        if len(workers) == fail_at:
                            raise error_type('injected worker start failure')
                        original_start(worker)
                    try:
                        with patch('gopyt.server._addr', return_value=('127.0.0.1', 0)), \
                                patch('gopyt.server.MAX_HANDLERS', 3), \
                                patch('gopyt.server.threading.Thread.start', new=start):
                            result = serve(vm, 'api')
                        self.assertEqual(vm.type_name(result.type_id), 'core.status.ListenError')
                        self.assertFalse(vm.serving)
                        self.assertFalse(any(worker.is_alive() for worker in workers))
                        self.assertEqual(servers[0].socket.fileno(), -1)
                        self.assertEqual(servers[0].pending.unfinished_tasks, 0)
                        # The listening address can immediately be reused.
                        with socket.socket() as replacement:
                            replacement.bind(servers[0].server_address)
                    finally:
                        # Keep a failing pre-fix regression from leaking its real workers.
                        for server in set(servers):
                            live = [worker for worker in workers if worker.is_alive()]
                            for worker in live:
                                server.pending.put(None)
                            for worker in live:
                                worker.join(2)
                            server.socket.close()

    def test_serving_context_stops_idle_input_and_native_workers(self):
        from gopyt.vm import Cancelled, Trap
        for mode in ('deadline', 'cancel'):
            for activity in ('idle', 'input', 'native'):
                with self.subTest(mode=mode, activity=activity), \
                        tempfile.TemporaryDirectory() as root, \
                        patch('gopyt.server._addr', return_value=('127.0.0.1', 0)), \
                        patch('gopyt.server.MAX_HANDLERS', 2):
                    write_pkg(root, fixtures.API_FILES)
                    prog, art, ids = build(root)
                    vm = make_vm(root, prog, art, ids)
                    cancel, entered, exited = (threading.Event() for _ in range(3))
                    outcome = []
                    vm.natives = dict(vm.natives)
                    def slow_log(*args):
                        self.assertIn(cancel, vm.cancels)
                        entered.set()
                        try:
                            return vm.natives['core.time.sleep_ms'](vm, [30_000], None)
                        finally:
                            exited.set()
                    vm.natives['core.log.write'] = slow_log
                    def invoke():
                        vm.cancels = (cancel,)
                        if mode == 'deadline':
                            vm.deadline_ns = time.monotonic_ns() + 1_000_000_000
                        try:
                            vm.call(ids['api.serve'], [])
                        except BaseException as error:
                            outcome.append(error)
                    host = threading.Thread(target=invoke, daemon=True)
                    host.start()
                    connection = None
                    try:
                        until = time.monotonic() + 2
                        while getattr(vm, 'httpd', None) is None and time.monotonic() < until:
                            time.sleep(.005)
                        self.assertIsNotNone(getattr(vm, 'httpd', None))
                        workers = tuple(vm.httpd.workers)
                        if activity != 'idle':
                            connection = socket.create_connection(vm.httpd.server_address, timeout=2)
                            connection.sendall(b'GET /echo/abc HTTP/1.1\r\nHost: localhost\r\n' +
                                               (b'\r\n' if activity == 'native' else b''))
                        if activity == 'native':
                            self.assertTrue(entered.wait(2))
                        if mode == 'cancel':
                            cancel.set()
                        host.join(3)
                        self.assertFalse(host.is_alive(), 'serve ignored its calling context')
                        self.assertEqual(len(outcome), 1)
                        self.assertIsInstance(outcome[0], Trap if mode == 'deadline' else Cancelled)
                        if mode == 'deadline':
                            self.assertEqual(outcome[0].code, 6)
                        self.assertFalse(vm.serving)
                        self.assertEqual(vm.httpd.socket.fileno(), -1)
                        self.assertFalse(any(worker.is_alive() for worker in workers))
                        if activity == 'native':
                            self.assertTrue(exited.is_set())
                    finally:
                        cancel.set()
                        if connection is not None:
                            connection.close()
                        if getattr(vm, 'httpd', None) is not None:
                            vm.httpd.shutdown()
                        host.join(5)

    def test_expired_queued_connection_never_invokes_handler(self):
        from gopyt.values import UNIT
        entered, release = threading.Event(), threading.Event()
        calls = []
        with running_server(MAX_HANDLERS=1, REQUEST_TIMEOUT_SECONDS=.15) as (vm, port):
            vm.natives = dict(vm.natives)
            def hold(*args):
                calls.append(1)
                entered.set()
                release.wait(2)
                return UNIT
            vm.natives['core.log.write'] = hold
            with contextlib.ExitStack() as stack:
                first = stack.enter_context(socket.create_connection(('127.0.0.1', port), timeout=2))
                first.sendall(b'GET /echo/abc HTTP/1.1\r\nHost: localhost\r\n\r\n')
                self.assertTrue(entered.wait(2))
                second = stack.enter_context(socket.create_connection(('127.0.0.1', port), timeout=2))
                second.sendall(b'GET /echo/abc HTTP/1.1\r\nHost: localhost\r\n\r\n')
                until = time.monotonic() + 2
                while vm.httpd.pending.qsize() != 1 and time.monotonic() < until:
                    time.sleep(.005)
                self.assertEqual(vm.httpd.pending.qsize(), 1)
                time.sleep(.2)
                release.set()
                try:
                    received = second.recv(4096)
                except ConnectionResetError:
                    received = b''
                self.assertEqual(received, b'')
                self.assertEqual(calls, [1])

    def test_request_execution_budget_and_keepalive_reset(self):
        from gopyt.values import UNIT
        with running_server(MAX_HANDLERS=1, REQUEST_TIMEOUT_SECONDS=.2) as (vm, port):
            vm.natives = dict(vm.natives)
            def short(*args):
                time.sleep(.12)
                return UNIT
            vm.natives['core.log.write'] = short
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            try:
                for _ in range(2):
                    connection.request('GET', '/echo/abc')
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
            finally:
                connection.close()
            exited = threading.Event()
            def long(*args):
                try:
                    return vm.natives['core.time.sleep_ms'](vm, [30_000], None)
                finally:
                    exited.set()
            vm.natives['core.log.write'] = long
            with socket.create_connection(('127.0.0.1', port), timeout=2) as client:
                client.sendall(b'GET /echo/abc HTTP/1.1\r\nHost: localhost\r\n\r\n')
                self.assertEqual(client.recv(4096), b'')
            self.assertTrue(exited.is_set())

    def test_response_backpressure_consumes_remaining_budget(self):
        from gopyt.server import _DeadlineWriter
        with running_server(MAX_HANDLERS=1) as (vm, _):
            sender, receiver = socket.socketpair()
            with sender, receiver:
                sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
                vm.deadline_ns = time.monotonic_ns() + 100_000_000
                writer = _DeadlineWriter(sender, vm)
                try:
                    with self.assertRaises(TimeoutError):
                        writer.write(b'x' * 1_048_576)
                finally:
                    vm.deadline_ns = None
                    writer.close()

    def test_listener_startup_does_not_depend_on_reverse_dns(self):
        with patch('socket.getfqdn', side_effect=AssertionError('reverse DNS must not run')):
            with running_server() as (_, port):
                self.assertEqual(request(port)[0], 200)

    def test_small_responses_disable_nagle_on_the_accepted_socket(self):
        with running_server() as (vm, port):
            with socket.create_connection(('127.0.0.1', port), timeout=2):
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    with vm.httpd.connection_lock:
                        connections = tuple(vm.httpd.connections)
                    if connections and vm.httpd.pending.empty():
                        # setup runs just after dequeue; wait until its socket option is visible.
                        # Darwin returns its enabled flag bit (4); Linux returns 1.
                        if connections[0].getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) != 0:
                            break
                    time.sleep(.005)
                else:
                    self.fail('accepted HTTP socket did not enable TCP_NODELAY')

    def test_truncated_declared_body_does_not_invoke_post_handler(self):
        with running_server() as (vm, port):
            payload = b'{"amount":17}'
            raw = (b'POST /charge HTTP/1.1\r\nHost: localhost\r\n'
                   b'Content-Type: application/json\r\nContent-Length: 100\r\n\r\n' + payload)
            self.assertEqual(wire(port, raw, half_close=True), (400, b''))
            self.assertEqual(request(port), (200, b'{"amount":3}'))

    def test_truncated_ignored_get_body_is_still_bad_framing(self):
        with running_server() as (_, port):
            self.assertEqual(wire(port, b'GET /echo/abc HTTP/1.1\r\nHost: localhost\r\n'
                                  b'Content-Length: 100\r\n\r\nx', True), (400, b''))

    def test_ambiguous_or_oversized_framing_is_rejected(self):
        with running_server() as (_, port):
            cases = [(b'Content-Length: 0\r\nContent-Length: 0', 400),
                     (b'Content-Length: +1', 400),
                     (b'Content-Length: -1', 400),
                     (b'Transfer-Encoding: chunked', 400),
                     (b'Content-Length: 1048577', 413)]
            for headers, status in cases:
                with self.subTest(headers=headers):
                    raw = b'POST /charge HTTP/1.1\r\nHost: localhost\r\n' + headers + b'\r\n\r\n'
                    self.assertEqual(wire(port, raw), (status, b''))
            self.assertEqual(request(port), (200, b'{"amount":3}'))

    def test_malformed_json_is_rejected_and_connection_remains_usable(self):
        with running_server() as (_, port):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            try:
                cases = [b'{"amount":1,"amount":2}', b'{"amount":true}',
                         b'{"amount":9223372036854775808}', b'{"amount":1e999999}',
                         b'{"amount":1}{}', b'{"amount":1,"extra":2}',
                         b'{"amount":"\\ud800"}', b'\xff', b'[' * 1100]
                for body in cases:
                    with self.subTest(body=body[:50]):
                        connection.request('POST', '/charge', body, {'Content-Type': 'application/json'})
                        response = connection.getresponse()
                        self.assertEqual((response.status, response.read()), (400, b''))
                connection.request('POST', '/charge', b'{"amount":17}')
                response = connection.getresponse()
                self.assertEqual((response.status, response.read()), (200, b'{"amount":17}'))
            finally:
                connection.close()

    def test_pipelined_requests_preserve_response_boundaries(self):
        with running_server() as (_, port):
            with socket.create_connection(('127.0.0.1', port), timeout=2) as connection:
                connection.sendall(b'GET /echo/a HTTP/1.1\r\nHost: localhost\r\n\r\n'
                                   b'GET /echo/abcd HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                # One shared buffered reader avoids discarding the second response.
                reader = connection.makefile('rb')
                try:
                    for expected in (b'{"amount":1}', b'{"amount":4}'):
                        self.assertTrue(reader.readline().startswith(b'HTTP/1.1 200 '))
                        headers = http.client.parse_headers(reader)
                        self.assertEqual(reader.read(int(headers['Content-Length'])), expected)
                finally:
                    reader.close()

    def test_idle_connections_eventually_release_workers(self):
        with running_server(MAX_HANDLERS=2, REQUEST_TIMEOUT_SECONDS=.15) as (_, port):
            with contextlib.ExitStack() as sockets:
                for _ in range(2):
                    sockets.enter_context(socket.create_connection(('127.0.0.1', port), timeout=2))
                time.sleep(.05)
                self.assertEqual(request(port), (200, b'{"amount":3}'))

    def test_drip_fed_body_cannot_extend_request_deadline(self):
        with running_server(MAX_HANDLERS=1, REQUEST_TIMEOUT_SECONDS=.2) as (_, port):
            with socket.create_connection(('127.0.0.1', port), timeout=2) as slow:
                slow.sendall(b'POST /charge HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1000\r\n\r\n')
                stopped = threading.Event()
                def drip():
                    while not stopped.wait(.025):
                        try:
                            slow.sendall(b' ')
                        except OSError:
                            return
                dripper = threading.Thread(target=drip, daemon=True)
                dripper.start()
                try:
                    time.sleep(.05)
                    self.assertEqual(request(port), (200, b'{"amount":3}'))
                finally:
                    stopped.set()
                    dripper.join(2)

    def test_shutdown_interrupts_partial_headers_and_partial_bodies(self):
        # The fixture asserts server and worker termination while sockets remain open.
        with contextlib.ExitStack() as sockets:
            with running_server(MAX_HANDLERS=2) as (_, port):
                for raw in (b'GET /echo/a HTTP/1.1\r\nHost:',
                            b'POST /charge HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n{'):
                    connection = sockets.enter_context(socket.create_connection(('127.0.0.1', port), timeout=2))
                    connection.sendall(raw)

    def test_full_queue_returns_503_and_recovers_after_clients_close(self):
        with running_server(MAX_HANDLERS=1, QUEUE=1) as (vm, port):
            with contextlib.ExitStack() as sockets:
                active = sockets.enter_context(socket.create_connection(('127.0.0.1', port), timeout=2))
                active.sendall(b'GET /echo/a HTTP/1.1\r\nHost:')
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    with vm.httpd.connection_lock:
                        ready = len(vm.httpd.connections) == 1
                    if ready and vm.httpd.pending.empty():
                        break
                    time.sleep(.005)
                else:
                    self.fail('first client did not occupy the worker')
                sockets.enter_context(socket.create_connection(('127.0.0.1', port), timeout=2))
                deadline = time.monotonic() + 2
                while vm.httpd.pending.qsize() != 1 and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertEqual(vm.httpd.pending.qsize(), 1)
                self.assertEqual(request(port), (503, b''))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with vm.httpd.connection_lock:
                    if not vm.httpd.connections:
                        break
                time.sleep(.005)
            self.assertEqual(request(port), (200, b'{"amount":3}'))


if __name__ == '__main__':
    unittest.main()
