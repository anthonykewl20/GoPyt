"""Compiled outbound calls with real delayed/framed HTTP and verified TLS."""
import datetime
import ipaddress
import os
from pathlib import Path
import ssl
import threading
import time
import unittest
from unittest.mock import patch

from gopyt.cli import build
from gopyt.resource_authority import ResourceAuthority
from gopyt import test_resource_authority as fixtures
from gopyt.testing import write_lock
from gopyt.vm import VM, Trap, Cancelled


class OutboundBudgets(unittest.TestCase):
    def test_model_payload_alias_survives_transport_failure_with_its_charge(self):
        vm = self.fixture.vm
        for cancellation in (False, True):
            with self.subTest(cancellation=cancellation):
                aliases, requests = [], []
                class FailingTransport:
                    def open(self, request, **kwargs):
                        requests.append(request)
                        aliases.append(request.data)
                        if cancellation:
                            raise Cancelled()
                        raise OSError('transport failed before response')
                with patch('gopyt.netio.opener', return_value=FailingTransport()):
                    if cancellation:
                        with self.assertRaises(Cancelled): self.invoke('model')
                    else:
                        result = self.invoke('model')
                        self.assertEqual(vm.type_name(result.type_id), 'core.status.ModelError')
                self.assertEqual(len(aliases), 1)
                self.assertTrue(aliases[0].startswith(b'{"prompt":'))
                self.assertIsNone(requests[0].data)
                self.assertEqual(vm.resource_budget.snapshot()['used']['native_bytes'], len(aliases[0]))
                aliases.clear()
                self.assertEqual(vm.resource_budget.snapshot()['used']['native_bytes'], 0)

    def test_model_payload_admission_and_transport_lifetime(self):
        from gopyt.netio import _Connection
        vm = self.fixture.vm
        with vm.resource_budget.reserve(native_bytes=
                vm.resource_budget.snapshot()['limits']['native_bytes']):
            with patch('gopyt.netio.opener') as transport:
                result = self.invoke('model')
                self.assertEqual(vm.type_name(result.type_id), 'core.status.ModelError')
                transport.assert_not_called()
        observed = []
        original = _Connection.send
        def send(connection, data):
            try:
                if data.startswith(b'{"prompt":'):
                    observed.append((len(data), vm.resource_budget.snapshot()['used']['native_bytes']))
                return original(connection, data)
            finally:
                data = None
        with patch.object(_Connection, 'send', new=send):
            self.assertEqual(self.invoke('model'), 'local response')
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][0], observed[0][1])
        self.assertEqual(vm.resource_budget.snapshot()['used']['native_bytes'], 0)

    def tearDown(self):
        self.assertEqual(self.fixture.vm.descriptors.pending(), 0)
        self.assertEqual(self.fixture.vm.resource_budget.snapshot()['used']['descriptors'], 0)

    def test_descriptor_rejection_does_not_connect(self):
        from gopyt.resource_budget import ResourceBudget, ResourceLimits
        previous = self.fixture.vm
        self.fixture.vm = VM(previous.art, self.fixture.root, authority=self.fixture.authority,
                             resource_budget=ResourceBudget(ResourceLimits(0, 0, 0, 0)))
        with patch('gopyt.resource_sockets._Socket.__new__') as create:
            result = self.invoke('http')
        create.assert_not_called()
        self.assertEqual(self.fixture.vm.type_name(result.type_id), 'core.status.HttpError')

    def setUp(self):
        self.fixture = fixtures.NetworkCalls()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def invoke(self, mode):
        if mode == 'http':
            return self.fixture.request(self.fixture.origin + '/probe')
        with patch.dict(os.environ, {'GOPYT_MODEL_URL': self.fixture.origin + '/probe'}):
            return self.fixture.vm.call(self.fixture.ids['remote.complete'], [])

    def hold(self, raw, mode='http', condition='deadline'):
        entered, release, cancel = (threading.Event() for _ in range(3))
        outcomes = []
        def handler(connection):
            connection.connection.sendall(raw)
            entered.set()
            release.wait(3)
        def run():
            vm = self.fixture.vm
            vm.cancels = (cancel,)
            if condition == 'deadline':
                vm.deadline_ns = time.monotonic_ns() + 200_000_000
            try:
                outcomes.append(self.invoke(mode))
            except BaseException as error:
                outcomes.append(error)
        with patch.object(self.fixture.server.RequestHandlerClass, 'do_GET', handler), \
                patch('gopyt.natives.HTTP_TIMEOUT_MS', 200 if condition == 'own' else 30_000):
            caller = threading.Thread(target=run)
            caller.start()
            try:
                self.assertTrue(entered.wait(2))
                if condition == 'cancel':
                    cancel.set()
                caller.join(2)
                self.assertFalse(caller.is_alive(), 'call waited for the held peer')
                self.assertEqual(len(outcomes), 1)
                if condition == 'own':
                    self.assertEqual(self.fixture.vm.type_name(outcomes[0].type_id),
                                     'core.status.HttpError' if mode == 'http' else 'core.status.ModelError')
                else:
                    self.assertIsInstance(outcomes[0], Cancelled if condition == 'cancel' else Trap)
                    if condition == 'deadline':
                        self.assertEqual(outcomes[0].code, 6)
            finally:
                release.set()
                caller.join(4)

    def test_body_wait_observes_shared_deadline_cancellation_and_own_budget(self):
        for mode in ('http', 'model'):
            for condition in ('deadline', 'cancel', 'own'):
                with self.subTest(mode=mode, condition=condition):
                    self.hold(b'HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\n', mode, condition)

    def test_headers_chunk_sizes_and_trailers_consume_budget(self):
        for raw in (b'HTTP/1.1 200 OK\r\nX-Incomplete: ',
                    b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1;extension=',
                    b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\nTrailer: '):
            with self.subTest(raw=raw):
                self.hold(raw)

    def test_error_response_body_timeout_is_data_for_own_budget(self):
        self.hold(b'HTTP/1.1 500 Failed\r\nContent-Length: 20\r\n\r\n', condition='own')

    def test_chunked_body_and_received_error_status_are_preserved(self):
        for status in (200, 404):
            def handler(connection):
                connection.connection.sendall(
                    f'HTTP/1.1 {status} Status\r\nTransfer-Encoding: chunked\r\n\r\n'.encode()
                    + b'3\r\none\r\n3\r\ntwo\r\n0\r\nDone: yes\r\n\r\n')
            with patch.object(self.fixture.server.RequestHandlerClass, 'do_GET', handler):
                response = self.invoke('http')
            self.assertEqual(response.fields, [status, b'onetwo'])

    def test_expired_dns_does_not_start_a_connection(self):
        import socket
        original = socket.getaddrinfo
        def resolve(*args, **kwargs):
            result = original(*args, **kwargs)
            time.sleep(.05)
            return result
        with patch('gopyt.netio.socket.getaddrinfo', side_effect=resolve), \
                patch('gopyt.netio.socket.socket', side_effect=AssertionError('late connect')):
            self.fixture.vm.deadline_ns = time.monotonic_ns() + 10_000_000
            try:
                with self.assertRaises(Trap) as caught:
                    self.invoke('http')
                self.assertEqual(caught.exception.code, 6)
            finally:
                self.fixture.vm.deadline_ns = None

    def tls(self):
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.x509.oid import NameOID
        except ImportError:
            self.skipTest('cryptography extra required for local TLS certificate')
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'test server')])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                .sign(key, hashes.SHA256()))
        root = Path(self.fixture.root)
        cert_path, key_path = root / 'server.pem', root / 'server.key'
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                              serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        key_path.chmod(0o600)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        self.fixture.server.socket = context.wrap_socket(self.fixture.server.socket, server_side=True)
        self.change_origin(self.fixture.origin.replace('http:', 'https:'))
        return ssl.create_default_context(cafile=str(cert_path))

    def change_origin(self, origin):
        for path in Path(self.fixture.root).glob('spec/*.gopyt'):
            path.write_text(path.read_text().replace(self.fixture.origin, origin))
        self.fixture.origin = origin
        write_lock(self.fixture.root)
        _, art, ids = build(self.fixture.root)
        self.fixture.ids = ids
        self.fixture.vm = VM(art, self.fixture.root, authority=ResourceAuthority.issue(network=[origin]))

    def test_stalled_tls_handshake_consumes_shared_deadline(self):
        self.change_origin(self.fixture.origin.replace('http:', 'https:'))
        entered, release = threading.Event(), threading.Event()
        outcomes = []
        def handle(connection):
            entered.set()
            release.wait(3)
        def run():
            self.fixture.vm.deadline_ns = time.monotonic_ns() + 200_000_000
            try:
                outcomes.append(self.invoke('http'))
            except BaseException as error:
                outcomes.append(error)
        with patch.object(self.fixture.server.RequestHandlerClass, 'handle', handle):
            caller = threading.Thread(target=run)
            caller.start()
            try:
                self.assertTrue(entered.wait(2))
                caller.join(2)
                self.assertFalse(caller.is_alive())
                self.assertEqual(len(outcomes), 1)
                self.assertIsInstance(outcomes[0], Trap)
                self.assertEqual(outcomes[0].code, 6)
            finally:
                release.set()
                caller.join(4)

    def test_tls_trust_hostname_and_delayed_read(self):
        context = self.tls()
        denied = self.invoke('http')
        self.assertEqual(self.fixture.vm.type_name(denied.type_id), 'core.status.HttpError')
        with patch('ssl._create_default_https_context', return_value=context):
            response = self.invoke('http')
            self.assertEqual(response.fields, [200, b'{"text":"local response"}'])
            self.hold(b'HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\n')
            self.change_origin(self.fixture.origin.replace('127.0.0.1', 'localhost'))
            denied = self.invoke('http')
            self.assertEqual(self.fixture.vm.type_name(denied.type_id), 'core.status.HttpError')

    def test_tls_poll_timeout_can_resume_a_valid_response(self):
        context = self.tls()
        timed_out, release = threading.Event(), threading.Event()
        original = ssl.SSLSocket.recv_into
        def receive(sock, *args, **kwargs):
            try:
                return original(sock, *args, **kwargs)
            except TimeoutError:
                timed_out.set()
                raise
        def handler(connection):
            connection.connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n')
            release.wait(3)
            connection.connection.sendall(b'ok')
        outcomes = []
        def run():
            outcomes.append(self.invoke('http'))
        with patch('ssl._create_default_https_context', return_value=context), \
                patch.object(ssl.SSLSocket, 'recv_into', receive), \
                patch.object(self.fixture.server.RequestHandlerClass, 'do_GET', handler):
            caller = threading.Thread(target=run)
            caller.start()
            try:
                self.assertTrue(timed_out.wait(2))
            finally:
                release.set()
                caller.join(3)
            self.assertFalse(caller.is_alive())
        self.assertEqual(outcomes[0].fields, [200, b'ok'])
